#!/usr/bin/env bash
# Stage the private MOSS OCR worker on a dedicated Linux CPU host.
# Does not start a service, open ports, or modify the existing cPanel app.
set -Eeuo pipefail
umask 077

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run as an unprivileged application account, not root." >&2
  exit 2
fi

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${MOSS_AI_INSTALL_DIR:-$HOME/moss_ai}"
CACHE_DIR="${MOSS_AI_MODEL_CACHE:-$HOME/.paddlex}"
if [[ ! -f "$SOURCE_DIR/ai_worker.py" || ! -f "$SOURCE_DIR/requirements-ai-worker.txt" ]]; then
  echo "Run from the complete MOSS recognition server source folder" >&2
  exit 2
fi

PYTHON=""
for candidate in python3.11 python3.12; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON="$(command -v "$candidate")"
    break
  fi
done
if [[ -z "$PYTHON" ]]; then
  echo "This worker requires Python 3.11 or 3.12" >&2
  exit 2
fi

mkdir -p "$INSTALL_DIR" "$CACHE_DIR"
chmod 700 "$INSTALL_DIR" "$CACHE_DIR"
"$PYTHON" -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python" -m pip install --upgrade pip
# Pin CPU Paddle runtime before installing the OCR Python package.
"$INSTALL_DIR/venv/bin/python" -m pip install \
  paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
"$INSTALL_DIR/venv/bin/python" -m pip install \
  -r "$SOURCE_DIR/requirements-ai-worker.txt"
cp "$SOURCE_DIR/ai_worker.py" "$INSTALL_DIR/ai_worker.py"
chmod 600 "$INSTALL_DIR/ai_worker.py"
echo "MOSS AI packages installed at $INSTALL_DIR"
echo "Next: configure MOSS_AI_WORKER_TOKEN, warm model weights, and run preflight."
echo "To start in this shell: $INSTALL_DIR/venv/bin/python $INSTALL_DIR/ai_worker.py"
