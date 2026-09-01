#!/bin/bash
# Start BOTH apps together:
#   ReelGrab downloader → http://127.0.0.1:5001
#   MarkEraser eraser   → http://127.0.0.1:5002
# Use the header links to switch between them. Ctrl+C stops both.
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  python3 -m venv venv
  ./venv/bin/pip install -q -r requirements-local.txt
fi

echo "Starting ReelGrab (downloader)  → http://127.0.0.1:5001"
./venv/bin/python app.py &
PID1=$!

echo "Starting MarkEraser (eraser)    → http://127.0.0.1:5002"
./venv/bin/python eraser_app.py &
PID2=$!

# Stop both on Ctrl+C
trap "echo; echo 'Stopping both…'; kill $PID1 $PID2 2>/dev/null; exit 0" INT TERM

echo ""
echo "Both running. Open either URL and use the header link to switch."
echo "Press Ctrl+C to stop both."
wait
