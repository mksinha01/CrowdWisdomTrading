#!/usr/bin/env bash
# scripts/bootstrap.sh — Linux/macOS bootstrap. Secondary to bootstrap.ps1.
set -euo pipefail

echo "=== CWT Video Ads Agent — bootstrap ==="

PY=$(command -v python3 || command -v python || true)
[ -z "$PY" ] && { echo "python3 not found"; exit 1; }
"$PY" - <<'EOF'
import sys
assert sys.version_info >= (3, 11), f"Python {sys.version_info[:2]} found; 3.11+ required."
EOF

[ -d .venv ] || "$PY" -m venv .venv
./.venv/bin/python -m pip install --upgrade pip --quiet
./.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt --quiet
./.venv/bin/python -m pip install -e . --quiet

[ -f .env ] || { cp .env.example .env; echo "  .env created — FILL IN YOUR KEYS"; }

./.venv/bin/python -m cwt doctor
