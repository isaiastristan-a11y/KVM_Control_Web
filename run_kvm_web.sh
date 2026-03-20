#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -d ".venv" ]]; then
  echo "[INFO] Creating virtual environment..."
  python3 -m venv .venv
fi

echo "[INFO] Installing/updating dependencies..."
.venv/bin/pip install -r requirements.txt

echo "[INFO] Starting Flask server at http://localhost:5000"
export FLASK_APP="kvm_web:app"
export FLASK_DEBUG=1
exec .venv/bin/flask run --host 0.0.0.0 --port 5000

