#!/bin/bash
# One-time setup for MarkEraser's HD (AI) video mode — ProPainter + PyTorch.
# Heavy: ~2 GB of downloads. Only needed for the "HD AI" video option;
# photo removal and fast (delogo) video work without this.
set -e
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  python3 -m venv venv
  ./venv/bin/pip install -q -r requirements-local.txt
fi

echo "[1/3] Installing PyTorch + ProPainter dependencies…"
./venv/bin/pip install -q torch torchvision \
  av addict einops future scipy scikit-image imageio imageio-ffmpeg pyyaml timm yapf matplotlib

if [ ! -d ProPainter ]; then
  echo "[2/3] Cloning ProPainter…"
  git clone --depth 1 https://github.com/sczhou/ProPainter.git
else
  echo "[2/3] ProPainter already present."
fi

echo "[3/3] Checking PyTorch / Metal (MPS)…"
./venv/bin/python -c "import torch; print('   torch', torch.__version__, '| MPS:', torch.backends.mps.is_available())"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "   NOTE: ffmpeg not found — install with 'brew install ffmpeg' for audio + best quality."
fi

echo ""
echo "Done. Model weights (~500 MB) download automatically on the first HD run."
echo "Start the app with:  ./start_eraser.sh"
