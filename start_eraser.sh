#!/bin/bash
# Start MarkEraser (watermark / object remover)
cd "$(dirname "$0")"
if [ ! -d venv ]; then
  python3 -m venv venv
  ./venv/bin/pip install -q -r requirements-local.txt
fi
# Ensure image/video deps are present
./venv/bin/python -c "import cv2, numpy, PIL" 2>/dev/null || ./venv/bin/pip install -q opencv-python-headless numpy pillow
./venv/bin/python eraser_app.py
