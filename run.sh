#!/usr/bin/env bash
# Cross-platform runner — mirrors run.ps1.
# Usage: ./run.sh <topic> [min_files] [max_iterations] [--skip-compile] [--skip-cleanup]
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <topic> [min_files] [max_iterations] [--skip-compile] [--skip-cleanup]"
    exit 1
fi

TOPIC="$1"; shift
MIN_FILES=100
MAX_ITER=5
SKIP_COMPILE=""
SKIP_CLEANUP=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-compile) SKIP_COMPILE="--skip-compile" ;;
        --skip-cleanup) SKIP_CLEANUP=1 ;;
        *) if [[ -z "${MIN_FILES_SET:-}" ]]; then MIN_FILES="$1"; MIN_FILES_SET=1
           else MAX_ITER="$1"; fi ;;
    esac
    shift
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
cd "$SCRIPT_DIR"

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || { echo "[ERROR] $PYTHON not found"; exit 1; }

echo "==> Checking dependencies"
"$PYTHON" -c "import requests, bs4, pypdf" 2>/dev/null || {
    echo "  Installing requirements..."
    "$PYTHON" -m pip install -r requirements.txt
}

echo "==> Checking Ollama"
if curl -sf --max-time 5 http://localhost:11434/api/tags >/dev/null; then
    echo "  Ollama running"
else
    echo "  [WARN] Ollama not reachable at localhost:11434 — LLM steps will degrade"
fi

echo "==> Running harvester: '$TOPIC' (min_files=$MIN_FILES, max_iter=$MAX_ITER)"
"$PYTHON" main.py "$TOPIC" "$MIN_FILES" "$MAX_ITER" $SKIP_COMPILE

if [[ $SKIP_CLEANUP -eq 0 ]]; then
    echo "==> Running dedup cleanup"
    SLUG="${TOPIC// /_}"
    "$PYTHON" cleanup.py --topic "$SLUG" --go || echo "  [WARN] Cleanup failed"
fi

echo "Done."
