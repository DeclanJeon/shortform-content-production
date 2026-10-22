#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

# --- How to run ---
# 1. Install uv if it is not installed.
# 2. Run: python3 build/embed_skills.py --skills-root /path/to/contents-production-skills
# 3. The target is src/skills; conflicting existing bundles are never overwritten.
# -----------------

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

EXPECTED_PACKAGE_VERSION = "2.30"
EXPECTED_SOURCE_COMMIT = "dc90ccc2505a60204f9a889c2d2957e444bf38d8"
MAX_BUNDLE_BYTES = 600_000
SKILLS = (
    "creative-production",
    "topic-demand-selection",
    "designing-character-personas",
    "designing-video-character-sheets",
    "storytelling-with-still-images",
    "recording-production-history",
    "video-production-assets",
    "orchestrating-video-preproduction",
    "developing-video-synopses",
    "storyboarding-video",
)
REFERENCES = (
    "creative-production/references/workspace-integration.md",
    "creative-production/references/production-routing.md",
    "creative-production/references/creative-qa.md",
    "video-production-assets/references/contract.md",
    "video-production-assets/references/preproduction-review.md",
    "video-production-assets/references/16-concept-emotion-retention.md",
    "video-production-assets/references/03-screenplay.md",
    "video-production-assets/references/29-reference-driven-production.md",
    "video-production-assets/references/26-state-causality-continuity.md",
    "video-production-assets/references/04-performance.md",
    "video-production-assets/references/05-visual.md",
    "video-production-assets/references/09-edit.md",
    "video-production-assets/references/12-qa.md",
    "video-production-assets/references/storyboard-contract.md",
    "orchestrating-video-preproduction/references/video-direction.md",
    "orchestrating-video-preproduction/references/stage-execution.md",
    "developing-video-synopses/references/synopsis-craft.md",
    "recording-production-history/scripts/production_history.py",
)
RUNTIME_SCRIPTS = (
    "video-production-assets/scripts/validate_project.py",
    "video-production-assets/scripts/asset_gate.py",
    "video-production-assets/scripts/validate_storyboard.py",
    "video-production-assets/scripts/render_storyboard_sheet.py",
    "video-production-assets/scripts/split_storyboard.py",
)
STAGE_SKILLS = {
    "topic": ("creative-production", "topic-demand-selection"),
    "narrative": (
        "creative-production",
        "orchestrating-video-preproduction",
        "developing-video-synopses",
        "designing-character-personas",
    ),
    "storyboard": (
        "creative-production",
        "video-production-assets",
        "designing-character-personas",
        "designing-video-character-sheets",
        "storytelling-with-still-images",
        "recording-production-history",
        "storyboarding-video",
    ),
}
STAGE_LIMITATIONS = {
    "storyboard": (
        "Apply the complete production-board contract to the still-image pipeline. "
        "Keep physical 3D feasibility and human acceptance separate: 2D spatial N/A "
        "needs a reason, unresolved owner inputs remain indexed blockers, and AI image "
        "review must never be described as human approval or solved 3D verification."
    ),
}
EXCLUDED = {
    "community-shorts-stills-pipeline": (
        "Its fixed 60-second webtoon layout and playlist/publishing stages conflict "
        "with this project's up-to-180-second format and separate publisher."
    ),
    "Higgsfield image and publication skills": (
        "This creator project uses its configured Codex Imagen executor and hands off "
        "to youtube_publish_only; it does not generate Higgsfield images or publish."
    ),
    "unselected platform and product specialists": (
        "Optional branches are not part of this Korean still-image story pipeline."
    ),
}


def safe_source(root: Path, relative: str) -> tuple[Path, bytes]:
    path = root / relative
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError(f"skill_source_link_forbidden:{relative}")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError(f"skill_source_outside_root:{relative}")
    raw = resolved.read_bytes()
    if len(raw) > 100_000:
        raise ValueError(f"skill_source_file_too_large:{relative}")
    return resolved, raw


def source_revision(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def committed_source(root: Path, relative: str) -> tuple[Path, bytes]:
    """Only embed bytes from the reviewed commit, not dirty checkout content."""
    path, raw = safe_source(root, relative)
    committed = subprocess.run(
        ["git", "-C", str(root), "show", f"{EXPECTED_SOURCE_COMMIT}:{relative}"],
        capture_output=True, check=True,
    ).stdout
    if raw != committed:
        raise ValueError(f"skill_source_drift:{relative}")
    return path, raw


def verify_existing(destination: Path) -> None:
    """A rebuild never repairs or overwrites unverified output silently."""
    _, raw = safe_source(destination, "BUNDLE_MANIFEST.json")
    manifest = json.loads(raw)
    expected = {"BUNDLE_MANIFEST.json"}
    for entry in manifest["files"]:
        relative = entry["path"]
        _, data = safe_source(destination, relative)
        if (hashlib.sha256(data).hexdigest() != entry["sha256"]
                or len(data) != entry["size_bytes"]):
            raise ValueError(f"embedded_skill_drift:{relative}")
        expected.add(relative)
    actual = set()
    for path in destination.rglob("*"):
        if path.is_symlink():
            raise ValueError("embedded_skill_link_forbidden")
        if path.is_file():
            actual.add(path.relative_to(destination).as_posix())
    if actual != expected:
        raise ValueError("embedded_skill_inventory_drift")


def embed(skills_root: Path, project_root: Path, *, rebuild: bool = False) -> dict[str, str | int]:
    source = skills_root.expanduser().resolve(strict=True)
    project = project_root.expanduser().resolve(strict=True)
    if source.name != "contents-production-skills":
        source = source / "contents-production-skills"
    _, upstream_raw = committed_source(source, "manifest.json")
    manifest = json.loads(upstream_raw)
    if manifest.get("version") != EXPECTED_PACKAGE_VERSION:
        raise ValueError("contents_production_skills_version_changed_review_required")
    revision = source_revision(source)
    if revision != EXPECTED_SOURCE_COMMIT:
        raise ValueError("contents_production_skills_source_changed_review_required")
    if not set(SKILLS).issubset(set(manifest.get("skills", []))):
        raise ValueError("required_content_skill_missing")

    selected: list[tuple[str, Path, bytes]] = []
    for name in SKILLS:
        relative = f"{name}/SKILL.md"
        path, raw = committed_source(source, relative)
        frontmatter = raw.decode("utf-8")
        if not re.search(r"^name:\s*" + re.escape(name) + r"\s*$", frontmatter, re.MULTILINE):
            raise ValueError(f"content_skill_name_mismatch:{name}")
        selected.append((f"contents-production-skills/{relative}", path, raw))
    for relative in REFERENCES:
        path, raw = committed_source(source, relative)
        selected.append((f"contents-production-skills/{relative}", path, raw))
    for relative in RUNTIME_SCRIPTS:
        path, raw = committed_source(source, relative)
        selected.append((f"contents-production-skills/{relative}", path, raw))
    if sum(len(raw) for _, _, raw in selected) > MAX_BUNDLE_BYTES:
        raise ValueError("skill_bundle_too_large")

    stage_files: dict[str, list[str]] = {}
    for stage, names in STAGE_SKILLS.items():
        paths = [f"contents-production-skills/{name}/SKILL.md" for name in names]
        paths += [
            "contents-production-skills/creative-production/references/workspace-integration.md",
            "contents-production-skills/video-production-assets/references/contract.md",
        ]
        if stage == "topic":
            paths += [
                "contents-production-skills/creative-production/references/production-routing.md",
                "contents-production-skills/topic-demand-selection/SKILL.md",
            ]
        elif stage == "narrative":
            paths += [
                "contents-production-skills/orchestrating-video-preproduction/references/video-direction.md",
                "contents-production-skills/orchestrating-video-preproduction/references/stage-execution.md",
                "contents-production-skills/developing-video-synopses/references/synopsis-craft.md",
                "contents-production-skills/video-production-assets/references/16-concept-emotion-retention.md",
                "contents-production-skills/video-production-assets/references/03-screenplay.md",
            ]
        else:
            paths += [
                "contents-production-skills/creative-production/references/production-routing.md",
                "contents-production-skills/creative-production/references/creative-qa.md",
                "contents-production-skills/video-production-assets/references/contract.md",
                "contents-production-skills/video-production-assets/references/storyboard-contract.md",
                "contents-production-skills/video-production-assets/references/preproduction-review.md",
                "contents-production-skills/video-production-assets/references/26-state-causality-continuity.md",
                "contents-production-skills/video-production-assets/references/04-performance.md",
                "contents-production-skills/video-production-assets/references/05-visual.md",
                "contents-production-skills/video-production-assets/references/09-edit.md",
                "contents-production-skills/video-production-assets/references/29-reference-driven-production.md",
                "contents-production-skills/video-production-assets/references/12-qa.md",
                "contents-production-skills/recording-production-history/SKILL.md",
                "contents-production-skills/storyboarding-video/SKILL.md",
            ]
        stage_files[stage] = list(dict.fromkeys(paths))

    files = [
        {"path": relative, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        for relative, _, raw in selected
    ]
    bundle_manifest = {
        "format": "shortform-content-skills-v3",
        "upstream_repository": "DeclanJeon/contents-production-skills",
        "upstream_package_version": EXPECTED_PACKAGE_VERSION,
        "upstream_commit": revision,
        "upstream_manifest_sha256": hashlib.sha256(upstream_raw).hexdigest(),
        "source_books_included": False,
        "included_skills": list(SKILLS),
        "stage_skill_files": stage_files,
        "stage_limitations": STAGE_LIMITATIONS,
        "runtime_scripts": {
            "storyboard_validation": [
                f"contents-production-skills/{name}"
                for name in RUNTIME_SCRIPTS[:3]
            ],
            "storyboard_renderer": [
                "contents-production-skills/video-production-assets/scripts/render_storyboard_sheet.py"
            ],
            "storyboard_splitter": [
                "contents-production-skills/video-production-assets/scripts/split_storyboard.py"
            ],
        },
        "external_required_skills": ["ai-delegation-loop", "codex-imagen"],
        "external_files": {
            "coordination": "ai-delegation-loop/SKILL.md",
            "image_executor": "configured Codex Imagen script beneath host_skill_root",
        },
        "excluded": EXCLUDED,
        "files": files,
    }
    destination = project / "src/skills"
    manifest_path = destination / "BUNDLE_MANIFEST.json"
    if destination.exists():
        verify_existing(destination)
        if not manifest_path.is_file():
            raise FileExistsError("skill_bundle_destination_conflict")
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing == bundle_manifest:
            return {"state": "SKILL_BUNDLE_ALREADY_CURRENT", "files": len(files), "revision": revision}
        if not rebuild:
            raise FileExistsError("skill_bundle_changed_explicit_rebuild_required")

    staging_parent = destination.parent
    temporary = Path(tempfile.mkdtemp(prefix=".skills-bundle-", dir=staging_parent))
    try:
        for relative, _, raw in selected:
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        (temporary / "BUNDLE_MANIFEST.json").write_text(
            json.dumps(bundle_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        if destination.exists():
            backup = temporary / ".previous"
            destination.replace(backup)
            try:
                temporary.replace(destination)
            except OSError:
                backup.replace(destination)
                raise
            shutil.rmtree(destination / ".previous")
        else:
            temporary.replace(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {"state": "SKILL_BUNDLE_EMBEDDED", "files": len(files), "revision": revision}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skills-root", type=Path, required=True)
    parser.add_argument("--rebuild", action="store_true", help="Replace only a verified existing bundle.")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    arguments = parser.parse_args()
    print(json.dumps(embed(arguments.skills_root, arguments.project_root, rebuild=arguments.rebuild), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
