#!/bin/bash
# Start the Insta Reel Downloader
cd "$(dirname "$0")"
if [ ! -d venv ]; then
  echo "Setting up for the first time..."
  python3 -m venv venv
  ./venv/bin/pip install -q -r requirements-local.txt
fi
./venv/bin/python app.py
