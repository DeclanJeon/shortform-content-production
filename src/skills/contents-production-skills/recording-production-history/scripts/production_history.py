#!/usr/bin/env python3
"""Append-only production history for v5.1 asset packages.

Initializes a project storage root outside the skill repository
(default: ``<Documents>/studio_production/<project-id>``) with the v5.1
package layout, then records one chronological, immutable Markdown
event per production operation under
``<project-root>/.history/events/``.

Design contract:

* History is an audit trail, not an approval ledger. ``project.json``
  at the project root remains the single canonical ledger; this tool
  never writes it after ``init`` and never derives approval, quality,
  or QA verdicts from events. Recording an event is not a check that
  the artifact is correct — it is the orchestrator's audit trail.
* Events are append-only Markdown files named
  ``NNNNNN-YYYYMMDDTHHMMSSffffff-<operation-id>-<event>.md`` so
  filesystem order equals chronological order and concurrent writers
  cannot overwrite a recorded event (publication is staged, then
  claimed atomically under a per-project OS advisory lock; the lock is
  OS-released on death, so a killed writer never blocks resume).
* Every operation event stores the full actual prompt text read from
  the supplied prompt file. Credential-shaped values are redacted in
  the stored copy; the original file's SHA-256 is recorded so the
  exact creative prompt stays provable against the untouched source.
* A ``started`` operation with no later terminal event is reported as
  ``in_flight`` by ``status``. ``resume`` marks those operations
  ``interrupted`` explicitly and appends a ``resumed`` lifecycle
  event; it never fabricates a completion.
* Nothing written into the history store is derived from unsanitized
  user input: project ids, operation ids and filenames are slugged,
  and ``..`` / path separators are rejected.

CLI::

    production_history.py init --project-id ID [--documents PATH]
    production_history.py record --project-root ROOT --operation TEXT
        --prompt-file FILE --status started|completed|interrupted|failed
        [--output PATH ...] [--details TEXT] [--tool NAME]
        [--model NAME] [--method TEXT] [--operation-id ID]
        [--attempt N] [--scope operation|project]
    production_history.py status --project-root ROOT
    production_history.py resume --project-root ROOT [--operation-id ID]
        [--details TEXT]

Every command prints a single JSON object with actual absolute paths.
Exit codes: 0 success, 2 usage/preflight error, 1 I/O failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
class UsageError(ValueError):
    """A user-correctable project or command preflight failure."""



# ---------------------------------------------------------------------------
# Layout and naming
# ---------------------------------------------------------------------------

STUDIO_DIRNAME = "studio_production"
HISTORY_DIRNAME = ".history"
EVENTS_DIRNAME = "events"
PROMPTS_DIRNAME = "prompts"
PROJECT_JSON = "project.json"

# v5.1 standard package layout (Production_Package_v5.1_Architecture §46).
PACKAGE_DIRS = (
    "00_MANIFEST",
    "01_SOURCE",
    "02_BIBLE",
    "02_MASTER_ASSETS/CHARACTERS",
    "02_MASTER_ASSETS/LOCATIONS",
    "02_MASTER_ASSETS/PROPS",
    "02_MASTER_ASSETS/TOOLS",
    "02_MASTER_ASSETS/ITEMS",
    "02_MASTER_ASSETS/VEHICLES",
    "02_MASTER_ASSETS/PRODUCTS",
    "02_MASTER_ASSETS/ENVIRONMENTS",
    "02_MASTER_ASSETS/GRAPHICS",
    "02_MASTER_ASSETS/VFX",
    "03_SCENE_PACKS",
    "04_STORYBOARDS",
    "05_SHOT_CARDS",
    "06_GENERATION_SPECS",
    "07_AUDIO",
    "08_ANIMATIC_EDIT",
    "09_QA",
    "10_DELIVERY",
)

TERMINAL_TYPES = ("completed", "interrupted", "failed")
RECORD_STATUSES = ("started",) + TERMINAL_TYPES
SCOPES = ("operation", "project")

_SLUG_BAD = re.compile(r"[^\w.-]+")
_SLUG_RUNS = re.compile(r"-{2,}")
_EVENT_NAME = re.compile(
    r"^(?P<seq>\d{6})-(?P<stamp>\d{8}T\d{12})-(?P<op>.+)-(?P<etype>[a-z]+)\.md$")
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

# Credential-shaped values are redacted from the stored prompt copy only;
# the original prompt file is never modified and its SHA-256 is kept.
_REDACT_RULES = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"hf_[A-Za-z0-9]{8,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"""(?i)(["']?(?:api[_-]?key|api[_-]?secret|access[_-]?token|auth[_-]?token|secret|password|passwd|private[_-]?key)["']?\s*[:=]\s*)(["'])((?:\\.|(?!\2).)*?)\2"""),
    re.compile(
        r"""(?i)(["']?(?:api[_-]?key|api[_-]?secret|access[_-]?token|auth[_-]?token|secret|password|passwd|private[_-]?key)["']?\s*[:=]\s*)([^\s'"\r\n,}]{6,})"""),
)

# ---------------------------------------------------------------------------
# Naming / path safety
# ---------------------------------------------------------------------------

def slugify(value: str, field: str) -> str:
    """Return a filesystem-safe slug; reject anything that can traverse.

    Unicode letters/numbers/underscore/hyphen/dot are kept so Korean and
    other non-ASCII project names work on Windows, macOS and Linux. The
    slug is casefolded so case-insensitive filesystems cannot create two
    projects that differ only in case.
    """
    if not isinstance(value, str) or not value.strip():
        raise UsageError(f"{field} must be a non-empty string")
    if "/" in value or "\\" in value:
        raise UsageError(f"{field} must not contain path separators: {value!r}")
    slug = _SLUG_BAD.sub("-", value.strip())
    slug = _SLUG_RUNS.sub("-", slug).strip("-_.").casefold()
    if not slug or slug in (".", "..") or ".." in slug:
        raise UsageError(
            f"{field} has no usable filename-safe characters: {value!r}")
    basename = slug.split(".", 1)[0]
    if basename in _WINDOWS_RESERVED:
        raise UsageError(
            f"{field} resolves to a reserved Windows device name: {value!r}")
    return slug




def _resolve_inside(root: Path, raw: str, field: str) -> Path:
    """Resolve any output path; production outputs must stay under root."""
    candidate = Path(raw).expanduser()
    resolved = (candidate if candidate.is_absolute()
                else root / candidate).resolve()
    if resolved == root or root not in resolved.parents:
        raise UsageError(f"{field} escapes the project root: {raw!r}")
    return resolved


def _assert_managed_path(root: Path, target: Path) -> None:
    """Reject symlink/junction components and resolved paths outside root."""
    root = root.absolute()
    target = target.absolute()
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise UsageError(f"managed path escapes project root: {target}") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise UsageError(f"managed path traverses a symlink or junction: {current}")
    resolved_root = root.resolve()
    resolved_target = target.resolve()
    if resolved_target != resolved_root and resolved_root not in resolved_target.parents:
        raise UsageError(f"managed path resolves outside project root: {target}")


# ---------------------------------------------------------------------------
# Documents directory (cross-platform)
# ---------------------------------------------------------------------------

def _windows_documents(env, home) -> Path | None:
    """Real Windows Documents folder, honoring shell redirection/OneDrive."""
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]

        # FOLDERID_Documents = FDD39AD0-238F-46AF-ADB4-6C85480369C7
        guid = _GUID(0xFDD39AD0, 0x238F, 0x46AF,
                     (ctypes.c_byte * 8)(0xAD, 0xB4, 0x6C, 0x85,
                                         0x48, 0x03, 0x69, 0xC7))
        buf = ctypes.c_wchar_p()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(  # type: ignore[attr-defined]
            ctypes.byref(guid), 0, None, ctypes.byref(buf))
        if hr == 0 and buf.value:
            path = Path(buf.value)
            ctypes.windll.ole32.CoTaskMemFree(buf)  # type: ignore[attr-defined]
            return path
    except (OSError, AttributeError, ImportError):
        pass
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        try:
            raw, _ = winreg.QueryValueEx(key, "Personal")
        finally:
            winreg.CloseKey(key)
        expanded = os.path.expandvars(str(raw))
        if expanded and Path(expanded).is_absolute():
            return Path(expanded)
    except (OSError, ImportError):
        pass
    return None


def _linux_documents(env, home: Path) -> Path | None:
    """XDG user-dirs Documents when configured, else ~/Documents."""
    config_home = env.get("XDG_CONFIG_HOME")
    config_dir = Path(config_home) if config_home else home / ".config"
    try:
        text = (config_dir / "user-dirs.dirs").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'(?m)^\s*XDG_DOCUMENTS_DIR\s*=\s*"(.*)"\s*$', text)
    if not match:
        return None
    candidate = Path(match.group(1).replace("$HOME", str(home)))
    return candidate if candidate.is_absolute() else None


def detect_documents(platform: str | None = None, env=None, home=None) -> Path:
    """Resolve the actual user Documents directory for the current OS."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = Path(home) if home is not None else Path.home()
    override = env.get("STUDIO_DOCUMENTS_DIR")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
    detected = None
    if platform == "win32":
        detected = _windows_documents(env, home)
    elif platform == "darwin":
        detected = home / "Documents"
    elif platform.startswith(("linux", "freebsd", "openbsd")):
        detected = _linux_documents(env, home)
    if detected is not None:
        return detected
    return home / "Documents"


# ---------------------------------------------------------------------------
# Project root / initialization
# ---------------------------------------------------------------------------

def resolve_project_root(project_id: str,
                         documents: str | os.PathLike | None = None) -> Path:
    """Absolute default storage root: <Documents>/studio_production/<slug>."""
    slug = slugify(project_id, "project_id")
    base = Path(documents).expanduser() if documents else detect_documents()
    return (base / STUDIO_DIRNAME / slug).resolve()


def initialize_project(project_id: str,
                       documents: str | os.PathLike | None = None,
                       *, project_root: str | os.PathLike | None = None) -> dict:
    """Create a project in the default or explicitly selected root."""
    if project_root is not None and documents is not None:
        raise UsageError("--documents and --project-root cannot be used together")
    slug = slugify(project_id, "project_id")
    root = (Path(project_root).expanduser().absolute() if project_root is not None
            else resolve_project_root(project_id, documents))
    project_file = root / PROJECT_JSON
    for managed in (project_file, root / HISTORY_DIRNAME,
                    root / HISTORY_DIRNAME / EVENTS_DIRNAME,
                    root / HISTORY_DIRNAME / PROMPTS_DIRNAME):
        _assert_managed_path(root, managed)
    if project_file.exists():
        try:
            existing = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UsageError(f"existing project.json is unreadable; refusing to reuse "
                             f"root {root}: {exc}")
        other = existing.get("project_id")
        if other and slugify(str(other), "project_id") != slug:
            raise UsageError(f"project.json at {project_file} belongs to project {other!r}")
    root.mkdir(parents=True, exist_ok=True)
    created, kept = [], []
    for rel in PACKAGE_DIRS:
        target = root / rel
        _assert_managed_path(root, target)
        if target.exists():
            kept.append(rel)
        else:
            target.mkdir(parents=True)
            created.append(rel)
    history = root / HISTORY_DIRNAME
    (history / EVENTS_DIRNAME).mkdir(parents=True, exist_ok=True)
    (history / PROMPTS_DIRNAME).mkdir(parents=True, exist_ok=True)
    if not project_file.exists():
        skeleton = {"schema_version": "1.1", "project_id": slug, "version": "v001",
                    "target_duration_s": None, "fps": None, "aspect_ratio": None,
                    "scenes": [], "characters": [], "shots": [], "claims": [],
                    "artifacts": [], "asset_registry": []}
        stage = root / f".project.json.stage-{os.getpid()}-{os.urandom(4).hex()}"
        try:
            with open(stage, "x", encoding="utf-8") as handle:
                json.dump(skeleton, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.link(stage, project_file)
        except FileExistsError:
            raise UsageError(f"project.json appeared during initialization; preserved: {project_file}")
        finally:
            stage.unlink(missing_ok=True)
        created.append(PROJECT_JSON)
    else:
        kept.append(PROJECT_JSON)
    return {"project_root": str(root), "documents": str(root.parent.parent),
            "project_id": slug, "created": created, "kept": kept,
            "history_dir": str(history)}


# ---------------------------------------------------------------------------
# Event store
# ---------------------------------------------------------------------------

def _events_dir(root: Path) -> Path:
    return root / HISTORY_DIRNAME / EVENTS_DIRNAME


def _require_project(root: Path) -> None:
    if not (root / PROJECT_JSON).is_file():
        raise UsageError(f"{root} is not an initialized project; run init first")
    _assert_managed_path(root, root / HISTORY_DIRNAME / EVENTS_DIRNAME)
    _assert_managed_path(root, root / HISTORY_DIRNAME / PROMPTS_DIRNAME)


def _ensure_history_layout(root: Path) -> None:
    _require_project(root)
    _events_dir(root).mkdir(parents=True, exist_ok=True)
    (root / HISTORY_DIRNAME / PROMPTS_DIRNAME).mkdir(parents=True, exist_ok=True)


def _iter_events(root: Path):
    events = _events_dir(root)
    rows = []
    if events.is_dir():
        for entry in events.iterdir():
            _assert_managed_path(root, entry)
            match = _EVENT_NAME.match(entry.name)
            if match:
                rows.append((int(match.group("seq")), entry.name,
                             match.groupdict()))
    rows.sort(key=lambda row: (row[0], row[1]))
    yield from rows


def _next_seq(events: Path) -> int:
    seq = 0
    if events.is_dir():
        for entry in events.iterdir():
            match = _EVENT_NAME.match(entry.name)
            if match:
                seq = max(seq, int(match.group("seq")))
    return seq + 1


def _read_front_matter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    fields = {}
    for line in text[4:end].splitlines():
        if ":" not in line or line.startswith((" ", "-")):
            continue
        key, _, value = line.partition(":")
        raw_value = value.strip()
        try:
            fields[key.strip()] = json.loads(raw_value)
        except (json.JSONDecodeError, TypeError):
            fields[key.strip()] = raw_value
    return fields


def _redact(text: str) -> tuple[str, int]:
    count = 0
    for rule in _REDACT_RULES:
        if rule.groups == 3:
            text, n = rule.subn(
                lambda m: m.group(1) + m.group(2) + "[REDACTED]" +
                (m.group(2) if m.group(2) else ""), text)
        elif rule.groups:
            text, n = rule.subn(lambda m: m.group(1) + "[REDACTED]", text)
        else:
            text, n = rule.subn("[REDACTED]", text)
        count += n
    return text, count


def _describe_output(root: Path, raw: str) -> dict:
    # Production outputs must always resolve inside the project root,
    # whether given absolute or project-relative.
    resolved = _resolve_inside(root, raw, "output")
    return {"path": str(resolved),
            "relative": str(resolved.relative_to(root)),
            "exists": resolved.is_file()}



def _operation_attempt_ids(root: Path, operation_id: str) -> set[int]:
    ids = set()
    for _, name, meta in _iter_events(root):
        if meta["op"] != operation_id or meta["etype"] != "started":
            continue
        fields = _read_front_matter(_events_dir(root) / name)
        attempt = fields.get("attempt")
        if fields.get("scope") == "operation" and isinstance(attempt, int):
            ids.add(attempt)
    return ids


def _render_event(fields: dict, seq: int, stamp: datetime,
                  local_stamp: str) -> str:
    front = ["---",
             f"seq: {seq}",
             f"event_type: {fields['event_type']}",
             f"scope: {fields.get('scope', 'operation')}",
             f"operation_id: {fields['operation_id']}",
             f"attempt: {fields.get('attempt', 1)}",
             f"operation: {json.dumps(fields['operation'], ensure_ascii=False)}",
             f"utc: {stamp.strftime('%Y-%m-%dT%H:%M:%S.%f')}Z",
             f"local: {local_stamp}"]
    for key in ("tool", "model", "method", "details", "prompt_file_source",
                "prompt_sha256", "prompt_snapshot", "redactions",
                "marked_operations"):
        if key in fields and fields[key] not in (None, "", []):
            front.append(f"{key}: {json.dumps(fields[key], ensure_ascii=False)}")
    if fields.get("outputs"):
        front.append("outputs:")
        for out in fields["outputs"]:
            front.append(f"  - path: {json.dumps(out['path'])}")
            front.append(f"    relative: {json.dumps(out['relative'])}")
            front.append(f"    exists: {str(out['exists']).lower()}")
    front.append("---")
    front.append("")
    front.append(f"# {fields['event_type']} — {fields['operation']}")
    front.append("")
    front.extend(fields.get("body", []))
    return "\n".join(front) + "\n"


def _append_event(root: Path, fields: dict,
                  prompt_text: str | None = None) -> Path:
    """Atomically publish one unique chronological event file.

    The event body is rendered to a staging file first; the final
    filename is then claimed with ``os.link``, which fails if another
    writer took the name. A crash can therefore never leave an empty or
    partial file that ``status`` would count as a real event. Prompt
    snapshots are claimed exclusively too and rolled back when the
    event loses its seq to a concurrent writer.
    """
    events = _events_dir(root)
    prompts_dir = root / HISTORY_DIRNAME / PROMPTS_DIRNAME
    _assert_managed_path(root, events)
    _assert_managed_path(root, prompts_dir)
    seq_hint: int | None = None
    for _ in range(100):
        seq = seq_hint if seq_hint is not None else _next_seq(events)
        seq_hint = None
        stamp = datetime.now(timezone.utc)
        local_stamp = datetime.now().astimezone().isoformat(
            timespec="seconds")
        name = (f"{seq:06d}-{stamp.strftime('%Y%m%dT%H%M%S%f')}-"
                f"{fields['operation_id']}-{fields['event_type']}.md")
        target = events / name

        snapshot = None
        if prompt_text is not None:
            snapshot = (prompts_dir
                        / f"{seq:06d}-{fields['operation_id']}-prompt.txt")
            _assert_managed_path(root, snapshot)
            try:
                fd = os.open(snapshot,
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                    handle.write(prompt_text)
            except FileExistsError:
                seq_hint = seq + 1
                continue
            fields["prompt_snapshot"] = str(snapshot)
            body = ["## Actual prompt", ""]
            body.append(
                f"Source file: `{fields['prompt_file_source']}`")
            body.append(
                "SHA-256 (original, pre-redaction): "
                f"`{fields['prompt_sha256']}`")
            body.append(f"Stored snapshot: `{snapshot}`")
            if fields.get("redactions"):
                body.append(
                    "Redacted credential-shaped values: "
                    f"{fields['redactions']}")
            body.extend(("", "```text", prompt_text.rstrip("\n"),
                         "```", ""))
            fields["body"] = body

        stage = events / f".{name}.tmp{os.getpid()}-{os.urandom(4).hex()}"
        _assert_managed_path(root, stage)
        try:
            with open(stage, "x", encoding="utf-8", newline="") as handle:
                handle.write(_render_event(fields, seq, stamp, local_stamp))
            os.link(stage, target)
        except FileExistsError:
            if snapshot is not None:
                snapshot.unlink(missing_ok=True)
            seq_hint = seq + 1
            continue
        finally:
            stage.unlink(missing_ok=True)
        return target
    raise OSError("could not allocate a unique event filename")


# ---------------------------------------------------------------------------
# Writer serialization
# ---------------------------------------------------------------------------

# One advisory lock file per project root. The OS releases it when the
# holder exits or is killed, so a dead process can never block resume —
# there is no stale-lock cleanup path.
LOCK_NAME = "writer.lock"


class _WriterLock:
    """Advisory exclusive lock serializing event writers on one project."""

    def __init__(self, path: Path):
        self._path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        _assert_managed_path(self._path.parents[1], self._path)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if sys.platform == "win32":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    # LK_LOCK waits ~10s then raises; clear failure, no retry.
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                except OSError as exc:
                    raise UsageError(
                        "another production_history writer holds this project "
                        "(or the previous writer died mid-call); retry after "
                        "it exits") from exc
            else:
                import fcntl
                # Serialized: writers queue until the OS releases the lock.
                fcntl.flock(fd, fcntl.LOCK_EX)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(self._fd)
        self._fd = None


@contextmanager
def _writer_lock(root: Path):
    lock = _WriterLock(root / HISTORY_DIRNAME / LOCK_NAME)
    _assert_managed_path(root, root / HISTORY_DIRNAME / LOCK_NAME)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def _read_output_paths(path: Path) -> list[str]:
    """Declared output absolute paths recorded in one event file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if not text.startswith("---\n"):
        return []
    end = text.find("\n---\n", 4)
    if end == -1:
        return []
    paths: list[str] = []
    in_outputs = False
    for line in text[4:end].splitlines():
        if line.strip() == "outputs:":
            in_outputs = True
            continue
        if in_outputs:
            match = re.match(r'\s+- path: (".*")', line)
            if match:
                try:
                    paths.append(json.loads(match.group(1)))
                except json.JSONDecodeError:
                    pass
            elif not line.startswith(" "):
                break
    return paths


def _declared_missing_outputs(root: Path, operation_id: str) -> list[str]:
    """Check only outputs declared by the operation's latest completed attempt."""
    events = []
    for _, name, meta in _iter_events(root):
        if meta['op'] != operation_id or meta['etype'] == 'resumed':
            continue
        path = _events_dir(root) / name
        fields = _read_front_matter(path)
        if fields.get('scope') == 'operation':
            events.append((path, fields))
    starts = [fields for _, fields in events
              if fields.get('event_type') == 'started']
    if not starts:
        return []
    # The live attempt is the CHRONOLOGICALLY last started event — an
    # explicit lower-numbered attempt started after a failed higher one is
    # still the current attempt (status.current_attempt agrees).
    latest_attempt = starts[-1].get('attempt')
    current = [(path, fields) for path, fields in events
               if fields.get('attempt') == latest_attempt]
    terminal = next(((path, fields) for path, fields in reversed(current)
                     if fields.get('event_type') in TERMINAL_TYPES), None)
    if terminal is None or terminal[1].get('event_type') != 'completed':
        return []
    declared = {raw for path, _ in current for raw in _read_output_paths(path)}
    return sorted(raw for raw in declared if not Path(raw).is_file())


# ---------------------------------------------------------------------------
# record / status / resume
# ---------------------------------------------------------------------------

def _record(args) -> dict:
    root = Path(args.project_root).expanduser().resolve()
    _ensure_history_layout(root)
    scope = args.scope
    operation = args.operation
    operation_id = args.operation_id or f"op-{slugify(operation, 'operation')}"
    operation_id = slugify(operation_id, "operation_id")

    with _writer_lock(root):
        state = _status(root)
        if scope == "operation":
            current = state["operations"].get(operation_id, {})
            current_state = current.get("state")
            if args.status == "started" and current_state == "started":
                raise UsageError(
                    f"operation {operation_id} is already in flight "
                    f"(attempt {current.get('attempts', 1)}); record its "
                    f"terminal event or interrupt it with resume before "
                    f"starting another attempt")
            if args.status in TERMINAL_TYPES and current_state != "started":
                raise UsageError(f"cannot record {args.status} for {operation_id}: "
                                 f"no pending started attempt")
            if args.status in TERMINAL_TYPES and args.attempt is not None \
                    and args.attempt != current.get("current_attempt"):
                raise UsageError(f"terminal attempt {args.attempt} does not match "
                                 f"current attempt {current.get('current_attempt')}")
        elif args.status == "completed":
            # Project completion is a lifecycle claim: never record it
            # while work is in flight or declared outputs are missing.
            blockers = []
            for op in state["in_flight"]:
                blockers.append(f"{op} (in flight)")
            for op, row in sorted(state["operations"].items()):
                if row["state"] == "completed":
                    missing = _declared_missing_outputs(root, op)
                    if missing:
                        blockers.append(f"{op} (completed output missing)")
                else:
                    blockers.append(f"{op} ({row['state']} attempt unresolved)")
            if blockers:
                raise UsageError("cannot record project completion; unresolved operations: "
                                 + ', '.join(blockers))

        outputs = [_describe_output(root, out)
                   for out in (args.output or [])]
        if args.status == "completed":
            # completed (operation or project scope) must report real
            # files; missing outputs belong on failed/interrupted.
            missing = [o["path"] for o in outputs if not o["exists"]]
            if missing:
                raise UsageError(
                    "cannot record completed: declared output(s) do not "
                    f"exist: {', '.join(missing)}")

        prompt_text = None
        fields: dict = {
            "event_type": args.status,
            "scope": scope,
            "operation": operation,
            "operation_id": operation_id,
        }
        if args.prompt_file:
            source = Path(args.prompt_file).expanduser().resolve()
            if not source.is_file():
                raise UsageError(f"prompt file does not exist: {source}")
            try:
                raw_bytes = source.read_bytes()
                raw = raw_bytes.decode("utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise UsageError(f"prompt file is not readable UTF-8 text: {exc}")
            if not raw.strip():
                raise UsageError(f"prompt file is empty: {source}")
            fields["prompt_file_source"] = str(source)
            fields["prompt_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
            prompt_text, redactions = _redact(raw)
            fields["redactions"] = redactions
        elif scope == "operation":
            raise UsageError(
                "--prompt-file is required for operation events; only "
                "--scope project events may omit it")

        fields["outputs"] = outputs
        for key in ("tool", "model", "method", "details"):
            value = getattr(args, key, None)
            if value:
                fields[key] = value
        if scope == "operation" and args.status == "started":
            used_attempts = _operation_attempt_ids(root, operation_id)
            if args.attempt is not None:
                if args.attempt in used_attempts:
                    raise UsageError(f"attempt {args.attempt} was already used for {operation_id}")
                fields["attempt"] = args.attempt
            else:
                fields["attempt"] = max(used_attempts, default=0) + 1
        elif scope == "operation":
            fields["attempt"] = current.get("current_attempt", 1)
        else:
            fields["attempt"] = args.attempt if args.attempt is not None else 1

        event_path = _append_event(root, fields, prompt_text)
    return {"project_root": str(root), "event": str(event_path),
            "operation_id": operation_id, "status": args.status,
            "attempt": fields["attempt"], "outputs": fields["outputs"]}


def _status(root: Path) -> dict:
    _require_project(root)
    operations: dict[str, dict] = {}
    lifecycle = []
    for seq, name, meta in _iter_events(root):
        fields = _read_front_matter(_events_dir(root) / name)
        op = meta["op"]
        etype = meta["etype"]
        if etype == "resumed" or fields.get("scope") == "project":
            lifecycle.append({"seq": seq, "event": name, "type": etype,
                              "operation": fields.get("operation", op),
                              "utc": fields.get("utc", "")})
            continue
        row = operations.setdefault(
            op, {"operation_id": op, "events": [], "attempts": 0,
                 "current_attempt": None, "state": None})
        if etype == "started":
            row["attempts"] += 1
        row["current_attempt"] = fields.get("attempt", row["current_attempt"])
        row["state"] = etype
        row["events"].append({"seq": seq, "type": etype, "attempt": row["current_attempt"],
                              "utc": fields.get("utc", "")})
        if fields.get("operation"):
            row["operation"] = fields["operation"]
    in_flight = sorted(op for op, row in operations.items()
                       if row["state"] == "started")
    return {"project_root": str(root), "operations": operations,
            "in_flight": in_flight, "lifecycle": lifecycle,
            "event_count": sum(len(r["events"]) for r in operations.values())
            + len(lifecycle)}


def _resume(args) -> dict:
    root = Path(args.project_root).expanduser().resolve()
    _ensure_history_layout(root)
    with _writer_lock(root):
        state = _status(root)
        only = (slugify(args.operation_id, "operation_id")
                if args.operation_id else None)
        if only is not None and only not in state["operations"]:
            raise UsageError(f"unknown operation id: {only}")
        pending = [op for op in state["in_flight"]
                   if only is None or op == only]
        if only is not None and only not in pending:
            raise UsageError(
                f"operation {only} is not in flight (state: "
                f"{state['operations'][only]['state']}); refusing to mark it")
        written = []
        details = args.details or (
            "marked interrupted by resume: previous run ended without a "
            "terminal event; completion is not implied")
        for op in pending:
            row = state["operations"][op]
            written.append(str(_append_event(root, {
                "event_type": "interrupted",
                "scope": "operation",
                "operation": row.get("operation", op),
                "operation_id": op,
                "attempt": row["current_attempt"],
                "details": details,
                "outputs": [],
            })))
        written.append(str(_append_event(root, {
            "event_type": "resumed",
            "scope": "project",
            "operation": "production history resumed after interruption",
            "operation_id": "project",
            "attempt": 1,
            "details": args.details or "",
            "marked_operations": pending,
        })))
    return {"project_root": str(root), "marked_interrupted": pending,
            "events_written": written}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="production_history.py")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create project storage layout")
    init.add_argument("--project-id", required=True)
    init.add_argument("--documents", default=None,
                      help="override the detected Documents directory")
    init.add_argument("--project-root", default=None,
                      help="initialize exactly this selected project root")

    rec = sub.add_parser("record", help="append one production event")
    rec.add_argument("--project-root", required=True)
    rec.add_argument("--operation", required=True)
    rec.add_argument("--prompt-file", default=None)
    rec.add_argument("--status", required=True, choices=RECORD_STATUSES)
    rec.add_argument("--output", action="append", default=[])
    rec.add_argument("--details", default=None)
    rec.add_argument("--tool", default=None)
    rec.add_argument("--model", default=None)
    rec.add_argument("--method", default=None)
    rec.add_argument("--operation-id", default=None)
    rec.add_argument("--attempt", type=int, default=None)
    rec.add_argument("--scope", choices=SCOPES, default="operation")

    status = sub.add_parser("status", help="report operation states")
    status.add_argument("--project-root", required=True)

    resume = sub.add_parser(
        "resume", help="close stale in-flight ops and record resumption")
    resume.add_argument("--project-root", required=True)
    resume.add_argument("--operation-id", default=None)
    resume.add_argument("--details", default=None)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            result = initialize_project(args.project_id, args.documents,
                                        project_root=args.project_root)
        elif args.command == "record":
            result = _record(args)
        elif args.command == "status":
            result = _status(Path(args.project_root).expanduser().resolve())
        elif args.command == "resume":
            result = _resume(args)
        else:  # pragma: no cover - argparse enforces
            raise UsageError(f"unknown command {args.command}")
    except UsageError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    except OSError as exc:
        print(json.dumps({"error": f"I/O failure: {exc}"}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
