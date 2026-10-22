#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
command -v python3 >/dev/null || { echo 'Python 3.12+ is required.' >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ required"'
if [[ "${1:-}" == "--non-interactive" ]]; then
  shift
  exec python3 -B "$ROOT/src/distribution/manage.py" setup "$@"
fi
exec python3 -B "$ROOT/src/distribution/manage.py" wizard "$@"
