#!/usr/bin/env bash
set -euo pipefail

HOST="127.0.0.1"
PORT="8000"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID=""

cleanup() {
  if [[ -n "${PID}" ]]; then
    kill "${PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

cd "${ROOT}"

echo "Starting server..."
py -3.12 -m uvicorn app.main:app --host "${HOST}" --port "${PORT}" --log-level warning &
PID=$!
sleep 3

echo "Checking /health..."
curl -sf "http://${HOST}:${PORT}/health"
echo

echo "Posting sample txt to /upload..."
curl -sf -F "file=@sample_docs/sample_apa.txt" "http://${HOST}:${PORT}/upload"
echo

echo "Posting sample txt to /check..."
curl -sf -F "file=@sample_docs/sample_apa.txt" "http://${HOST}:${PORT}/check" >/tmp/ref_checker_report.html
echo "Report HTML saved to /tmp/ref_checker_report.html"
