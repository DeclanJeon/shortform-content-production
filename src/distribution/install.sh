#!/usr/bin/env bash
# Linux + Docker Compose v2. No curl|sh, sudo, remote installer, or secret output.
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
command -v python3 >/dev/null || { printf 'Python 3 is required.\n' >&2; exit 1; }
exec python3 "$here/installer.py" install "$@"
