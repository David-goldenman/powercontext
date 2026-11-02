#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 11), "Python >=3.11 required"'
mkdir -p _work logs
mkdir -p _work/tmp _work/pip-cache
export TMPDIR="$PWD/_work/tmp"
export PIP_CACHE_DIR="$PWD/_work/pip-cache"
export PYTHONDONTWRITEBYTECODE=1
if [ -z "${POWERCONTEXT_SOURCE:-}" ]; then
  if [ ! -d _work/powercontext/.git ]; then
    git clone --branch 2026-competition https://github.com/David-goldenman/powercontext.git _work/powercontext
  fi
  git -C _work/powercontext checkout --detach 1be152d62a4c6df59709f15b184c6b93085fb76f
  POWERCONTEXT_SOURCE="$PWD/_work/powercontext"
fi
"$PYTHON_BIN" -c 'from pathlib import Path; import sys; from scripts.verify_foundation import git_revision; assert git_revision(Path(sys.argv[1]).resolve()) == "1be152d62a4c6df59709f15b184c6b93085fb76f"' "$POWERCONTEXT_SOURCE"
"$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install --index-url https://pypi.org/simple -r requirements.txt
printf 'Bootstrap complete. Export PYTHONPATH=%s/src and run .venv/bin/python scripts/verify_foundation.py\n' "$POWERCONTEXT_SOURCE"
