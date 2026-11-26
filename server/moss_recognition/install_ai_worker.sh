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
MODEL_DIR="${MOSS_SET_MODEL_DIR:-$INSTALL_DIR/models}"
MODEL_BASE_URL="https://raw.githubusercontent.com/Mr-Azrack/Moss-Machines-Magic-the-Gathering-sorting/f891c161b6d63a47b02e2f231307b540242ac42c/Current%20version/models"
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

mkdir -p "$INSTALL_DIR" "$CACHE_DIR" "$MODEL_DIR"
chmod 700 "$INSTALL_DIR" "$CACHE_DIR" "$MODEL_DIR"
"$PYTHON" -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python" -m pip install --upgrade pip
# Install official CPU-only PyTorch wheels for the set-symbol CNN.
"$INSTALL_DIR/venv/bin/python" -m pip install torch torchvision \
  --index-url https://download.pytorch.org/whl/cpu
# Pin CPU Paddle runtime before installing the OCR Python package.
"$INSTALL_DIR/venv/bin/python" -m pip install \
  paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
"$INSTALL_DIR/venv/bin/python" -m pip install \
  -r "$SOURCE_DIR/requirements-ai-worker.txt"
cp "$SOURCE_DIR/ai_worker.py" "$SOURCE_DIR/set_symbol_model.py" "$INSTALL_DIR/"
for part in 00 01; do
  target="$MODEL_DIR/set_cnn.part${part}.pt"
  if [[ ! -s "$target" ]]; then
    curl -fsSL --retry 3 "$MODEL_BASE_URL/set_cnn.part${part}.pt" -o "$target"
  fi
  test -s "$target"
done
chmod 600 "$MODEL_DIR"/set_cnn.part*.pt
chmod 600 "$INSTALL_DIR/ai_worker.py" "$INSTALL_DIR/set_symbol_model.py"
echo "MOSS AI packages installed at $INSTALL_DIR"
echo "Set MOSS_SET_MODEL_DIR=$MODEL_DIR in the worker service, configure MOSS_AI_WORKER_TOKEN, then run preflight."
echo "To start in this shell: $INSTALL_DIR/venv/bin/python $INSTALL_DIR/ai_worker.py"
