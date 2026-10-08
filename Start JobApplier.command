#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  echo "Install Python dependencies first; see README.md."
  read "?Press Return to close."
  exit 1
fi
if [[ ! -f frontend/dist/index.html ]]; then
  echo "Building the dashboard..."
  (cd frontend && npm run build)
fi
echo "JobApplier: http://127.0.0.1:8765"
echo "Keep this terminal open. Press Control-C to stop."
exec .venv/bin/python -m uvicorn jobapplier.app:app --host 127.0.0.1 --port 8765
