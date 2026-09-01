"""
MarkEraser — local object / watermark remover for photos and videos.
--------------------------------------------------------------------
Standalone tool, separate from the downloader. You upload media, mark the
region to erase, and it fills it in (content-aware inpainting for photos,
ffmpeg `delogo` for video).

Intended for content you own or have the rights to edit.

Run:  python eraser_app.py   →  http://127.0.0.1:5002
"""

import base64
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, jsonify, render_template, request, send_file
from PIL import Image

BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = Path(tempfile.gettempdir()) / "markeraser"
WORK_DIR.mkdir(exist_ok=True)

HAS_FFMPEG = shutil.which("ffmpeg") is not None
PROPAINTER_DIR = BASE_DIR / "ProPainter"
HAS_PROPAINTER = (PROPAINTER_DIR / "inference_propainter.py").exists()

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["MAX_CONTENT_LENGTH"] = 400 * 1024 * 1024  # 400 MB


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def _cleanup_old(max_age_sec: int = 3600) -> None:
    """Delete temp uploads older than an hour so /tmp doesn't fill up."""
    now = time.time()
    for p in WORK_DIR.glob("*"):
        try:
            if now - p.stat().st_mtime > max_age_sec:
                p.unlink()
        except Exception:
            pass


def _decode_mask(data_url: str, h: int, w: int) -> np.ndarray:
    """Turn a PNG data-URL (painted strokes, transparent elsewhere) into a
    binary uint8 mask matching the image size."""
    b64 = data_url.split(",", 1)[1]
    m = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
    alpha = np.array(m)[:, :, 3]
    if alpha.shape[:2] != (h, w):
        alpha = cv2.resize(alpha, (w, h), interpolation=cv2.INTER_NEAREST)
    mask = (alpha > 20).astype(np.uint8) * 255
    # grow slightly so the mark's soft edges are fully covered
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=2)
    return mask


def _inpaint(img_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Content-aware fill. Blend Telea + Navier-Stokes for smoother results."""
    a = cv2.inpaint(img_bgr, mask, 4, cv2.INPAINT_TELEA)
    b = cv2.inpaint(img_bgr, mask, 4, cv2.INPAINT_NS)
    return cv2.addWeighted(a, 0.5, b, 0.5, 0)


# ----------------------------------------------------------------------------
# routes
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("eraser.html", has_ffmpeg=HAS_FFMPEG,
                           has_ai=HAS_PROPAINTER)


@app.route("/api/erase/image", methods=["POST"])
def erase_image():
    if "media" not in request.files:
        return jsonify({"error": "No image uploaded."}), 400
    mask_url = request.form.get("mask", "")
    if not mask_url.startswith("data:image"):
        return jsonify({"error": "Nothing was marked to erase — paint over the watermark first."}), 400

    try:
        pil = Image.open(request.files["media"].stream).convert("RGB")
    except Exception:
        return jsonify({"error": "Couldn't read that image file."}), 400

    img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    h, w = img.shape[:2]

    mask = _decode_mask(mask_url, h, w)
    if int(mask.sum()) == 0:
        return jsonify({"error": "The marked area is empty — paint over the watermark first."}), 400

    result = _inpaint(img, mask)
    ok, buf = cv2.imencode(".png", result)
    if not ok:
        return jsonify({"error": "Failed to encode the result."}), 500

    return send_file(
        io.BytesIO(buf.tobytes()),
        mimetype="image/png",
        as_attachment=True,
        download_name="cleaned.png",
    )


@app.route("/api/video/prepare", methods=["POST"])
def video_prepare():
    """Save an uploaded video and return its first frame for region selection."""
    _cleanup_old()
    if "media" not in request.files:
        return jsonify({"error": "No video uploaded."}), 400

    f = request.files["media"]
    vid = uuid.uuid4().hex
    ext = os.path.splitext(f.filename or "")[1].lower() or ".mp4"
    if ext not in (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"):
        ext = ".mp4"
    path = WORK_DIR / f"{vid}{ext}"
    f.save(str(path))

    cap = cv2.VideoCapture(str(path))
    ok, frame = cap.read()
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    cap.release()

    if not ok:
        path.unlink(missing_ok=True)
        return jsonify({"error": "Couldn't read frames from that video."}), 400

    ret, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    frame_url = "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode()

    dur = round(frames / fps, 1) if fps else None
    return jsonify({"id": vid, "frame": frame_url, "w": w, "h": h,
                    "duration": dur, "has_ffmpeg": HAS_FFMPEG,
                    "has_ai": HAS_PROPAINTER})


@app.route("/api/video/process", methods=["POST"])
def video_process():
    data = request.get_json(silent=True) or {}
    vid = data.get("id", "")
    try:
        x = max(0, int(data["x"])); y = max(0, int(data["y"]))
        bw = int(data["w"]); bh = int(data["h"])
    except Exception:
        return jsonify({"error": "Invalid region."}), 400
    if bw < 4 or bh < 4:
        return jsonify({"error": "Draw a box over the watermark first."}), 400

    matches = list(WORK_DIR.glob(f"{vid}.*")) if vid else []
    src = next((p for p in matches if not p.name.endswith(".out.mp4")), None)
    if not src or not src.exists():
        return jsonify({"error": "This video session expired — re-upload the video."}), 404

    quality = (data.get("quality") or "fast").lower()
    out = WORK_DIR / f"{vid}.out.mp4"
    try:
        if quality == "hd" and HAS_PROPAINTER:
            _process_propainter(src, out, x, y, bw, bh)
        elif HAS_FFMPEG:
            _process_ffmpeg(src, out, x, y, bw, bh)
        else:
            _process_opencv(src, out, x, y, bw, bh)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or b"").decode(errors="replace")[-500:] if e.stderr else str(e)
        return jsonify({"error": f"Video processing failed. {detail}"}), 500
    except Exception as e:
        return jsonify({"error": f"Video processing failed: {e}"}), 500

    if not out.exists() or out.stat().st_size == 0:
        return jsonify({"error": "Processing produced no output."}), 500

    resp = send_file(str(out), mimetype="video/mp4",
                     as_attachment=True, download_name="cleaned.mp4")
    return resp


def _process_ffmpeg(src: Path, out: Path, x, y, w, h) -> None:
    """delogo interpolates neighbouring pixels over the region on every frame,
    and audio is copied through untouched."""
    vf = f"delogo=x={x}:y={y}:w={w}:h={h}"
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _process_propainter(src: Path, out: Path, x, y, w, h) -> None:
    """AI temporal video inpainting (ProPainter). Reconstructs the masked region
    using information from neighbouring frames — genuinely clean, even for opaque
    logos. Runs on Apple MPS. Caps processing to ~720p-equivalent for memory
    safety, then muxes the original audio back."""
    cap = cv2.VideoCapture(str(src))
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    cap.release()

    x2 = min(W, x + w); y2 = min(H, y + h)
    mask = np.zeros((H, W), np.uint8)
    mask[y:y2, x:x2] = 255

    work = WORK_DIR / f"pp_{uuid.uuid4().hex}"
    work.mkdir(exist_ok=True)
    mask_path = work / "mask.png"
    cv2.imwrite(str(mask_path), mask)
    out_root = work / "out"

    # keep processing under ~720p-equivalent so 16 GB stays safe on long clips
    target_px = 1280 * 720
    ratio = min(1.0, (target_px / max(1, W * H)) ** 0.5)

    cmd = [
        sys.executable, "inference_propainter.py",
        "-i", str(src), "-m", str(mask_path), "-o", str(out_root),
        "--resize_ratio", f"{ratio:.4f}",
        "--mask_dilation", "6",
        "--neighbor_length", "6",
        "--subvideo_length", "40",
        "--raft_iter", "12",
        "--save_fps", str(int(round(fps)) or 24),
    ]
    env = dict(os.environ)
    env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"  # unsupported ops fall back to CPU
    try:
        subprocess.run(cmd, cwd=str(PROPAINTER_DIR), check=True,
                       capture_output=True, env=env)
        inpainted = out_root / src.stem / "inpaint_out.mp4"
        if not inpainted.exists():
            raise RuntimeError("ProPainter produced no output video.")
        if HAS_FFMPEG:
            # graft the original audio (ProPainter output is silent)
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(inpainted), "-i", str(src),
                 "-map", "0:v:0", "-map", "1:a:0?",
                 "-c:v", "copy", "-c:a", "aac", "-shortest",
                 "-movflags", "+faststart", str(out)],
                check=True, capture_output=True,
            )
        else:
            shutil.copy(str(inpainted), str(out))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _process_opencv(src: Path, out: Path, x, y, w, h) -> None:
    """Fallback with no ffmpeg: per-frame inpaint. No audio, and uses the
    mp4v codec (widely playable, lower quality than H.264)."""
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    x2 = min(W, x + w); y2 = min(H, y + h)
    mask = np.zeros((H, W), np.uint8)
    mask[y:y2, x:x2] = 255

    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            writer.write(cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA))
    finally:
        cap.release()
        writer.release()


if __name__ == "__main__":
    print("\n  MarkEraser → http://127.0.0.1:5002")
    print(f"  ffmpeg: {'found' if HAS_FFMPEG else 'NOT found (brew install ffmpeg)'}")
    print(f"  AI mode (ProPainter): {'available' if HAS_PROPAINTER else 'not installed'}\n")
    app.run(host="127.0.0.1", port=5002, debug=False, threaded=True)
