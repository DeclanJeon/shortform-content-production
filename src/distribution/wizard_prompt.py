"""Dependency-free terminal prompts for the existing portable CLI."""
from __future__ import annotations

import getpass
from pathlib import Path
import re
import sys
from typing import Callable, Final, Protocol
from urllib.parse import urlparse
import warnings

from instance import SetupError

_SECRET_LIKE: Final = re.compile(r'(?:^sk-|^ya29\.|^GOCSPX-|^Bearer |PRIVATE KEY)')


class Console(Protocol):
    def say(self, message: str) -> None: ...
    def ask(self, label: str, default: str = '') -> str: ...
    def secret(self, label: str) -> str: ...


class Terminal:
    def __init__(self) -> None:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise SetupError('interactive_terminal_required_use_non_interactive_for_core_only')

    def say(self, message: str) -> None:
        print(message,flush=True)

    def ask(self, label: str, default: str = '') -> str:
        shown=''.join(c for c in default if ord(c)>=32 and ord(c)!=127)
        if _SECRET_LIKE.search(shown):shown=''
        hint=f' [{shown}]' if shown else ''
        return input(label+hint+' > ').strip() or default

    def secret(self, label: str) -> str:
        # Never allow getpass's warning-and-echo fallback on an unsafe terminal.
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            try:return getpass.getpass(label+': ').strip()
            except getpass.GetPassWarning:
                raise SetupError('secure_secret_input_unavailable') from None


def confirm(console: Console, label: str, *, default: bool = False) -> bool:
    while True:
        value=console.ask(label+' (y/n)','y' if default else 'n').lower()
        if value in ('y','yes','예','네'):return True
        if value in ('n','no','아니오','아니요'):return False
        console.say('y 또는 n으로 입력하세요.')


def text(console: Console, label: str, default: str = '', *, valid: Callable[[str],bool] = bool) -> str:
    while True:
        value=console.ask(label,default)
        secret_like=_SECRET_LIKE.search(value)
        if valid(value) and not secret_like and not any(ord(c)<32 or ord(c)==127 for c in value):return value
        console.say('입력 형식을 확인하세요. 이 항목에는 키·토큰을 입력하지 마세요.')


def integer(console: Console, label: str, default: int, valid: Callable[[int],bool]) -> int:
    return int(text(console,label,str(default),valid=lambda s:s.isascii() and s.isdigit() and valid(int(s))))


def identifier(console: Console, label: str, default: str = '') -> str:
    """Accept Google resource IDs or canonical Drive/Sheets resource URLs."""
    while True:
        value=text(console,label,default)
        parsed=urlparse(value)
        if parsed.scheme:
            parts=parsed.path.strip('/').split('/')
            host=parsed.hostname
            if host in ('docs.google.com','drive.google.com') and ('d' in parts or 'folders' in parts):
                marker='d' if 'd' in parts else 'folders';index=parts.index(marker)+1
                value=parts[index] if index<len(parts) else ''
            else:value=''
        if re.fullmatch(r'[A-Za-z0-9_-]{1,200}',value):return value
        console.say('Google 리소스 ID 또는 해당 Drive/Sheets 리소스의 정식 URL을 입력하세요.')


def external_path(value: str, source: Path) -> Path:
    """Reject symlinked paths and private files placed inside the source checkout."""
    raw=Path(value).expanduser().absolute()
    # Check links before resolve() can erase them, then compare canonical paths:
    # Path.absolute() alone retains '..' and permits lexical checkout bypasses.
    if any(p.is_symlink() for p in (raw,*raw.parents)):
        raise SetupError('wizard_private_paths_must_be_outside_checkout_without_links')
    path=raw.resolve(strict=False)
    checkout=(source.parent if source.name=='src' else source).resolve(strict=True)
    if path.is_relative_to(checkout):
        raise SetupError('wizard_private_paths_must_be_outside_checkout_without_links')
    return path
