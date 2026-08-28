"""
Vercel serverless entrypoint — PUBLIC-POST-ONLY cloud demo of ReelGrab.

Why this is separate from the local app.py:
  - No login here. Browser-cookie import needs a real browser + macOS Keychain,
    which don't exist on a serverless server. Password login from a datacenter
    IP triggers Instagram's checkpoint almost every time. So the cloud build
    downloads only PUBLIC posts, anonymously.
  - The filesystem is read-only except /tmp, so all scratch files go to /tmp.
  - Instagram frequently blocks datacenter IPs (which is what Vercel runs on),
    so downloads here may fail even for public posts. The full experience lives
    in the local app — see the repo README.

Vercel's @vercel/python runtime serves the module-level `app` (a WSGI app).
"""

import io
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import instaloader
import requests as http_requests
from flask import Flask, jsonify, render_template, request, send_file

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(BASE, "templates"))

REPO_URL = os.environ.get("GITHUB_REPO_URL", "")

SHORTCODE_RE = re.compile(r"/(?:reels?|p|tv)/([A-Za-z0-9_-]{5,})")


def _new_loader(target_dir: str) -> instaloader.Instaloader:
    return instaloader.Instaloader(
        dirname_pattern=target_dir,
        filename_pattern="{shortcode}",
        download_video_thumbnails=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern="",
        max_connection_attempts=1,
    )


def extract_shortcode(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ValueError("Please paste an Instagram link.")
    if re.fullmatch(r"[A-Za-z0-9_-]{5,}", url):
        return url
    if "instagram.com" not in url:
        raise ValueError("That doesn't look like an Instagram link.")
    if "/share/" in url:
        try:
            resp = http_requests.get(
                url, allow_redirects=True, timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            url = resp.url
        except Exception:
            raise ValueError("Couldn't resolve this share link. Copy the direct post/reel link instead.")
    m = SHORTCODE_RE.search(url)
    if not m:
        raise ValueError("Couldn't find a post/reel in that link. Use a link like instagram.com/reel/XXXX.")
    return m.group(1)


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:80] or "instagram"


@app.route("/")
def index():
    return render_template("cloud.html", repo_url=REPO_URL)


@app.route("/api/download", methods=["POST"])
def download():
    data = request.get_json(silent=True) or {}
    try:
        shortcode = extract_shortcode(data.get("url"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    tmpdir = Path(tempfile.mkdtemp(prefix="ig_", dir="/tmp"))
    try:
        L = _new_loader(str(tmpdir))
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        L.download_post(post, target="")

        media = sorted(
            f for f in tmpdir.iterdir()
            if f.suffix.lower() in (".mp4", ".jpg", ".jpeg", ".png", ".webp")
        )
        videos = [f for f in media if f.suffix.lower() == ".mp4"]
        if videos and post.typename == "GraphVideo":
            media = videos
        if not media:
            return jsonify({"error": "No media found for that link."}), 404

        owner = _safe_name(post.owner_username)
        base = f"{owner}_{shortcode}"

        if len(media) == 1:
            f = media[0]
            payload = io.BytesIO(f.read_bytes())
            name = f"{base}{f.suffix.lower()}"
            mime = "video/mp4" if f.suffix.lower() == ".mp4" else "image/jpeg"
        else:
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as zf:
                for i, f in enumerate(media, 1):
                    zf.write(f, f"{base}_{i:02d}{f.suffix.lower()}")
            name = f"{base}.zip"
            mime = "application/zip"

        # Vercel serverless caps response bodies (~4.5 MB on Hobby). Larger
        # reels can't be proxied — tell the user to use the local app.
        size = payload.getbuffer().nbytes
        if size > 4_400_000:
            mb = size / 1_048_576
            return jsonify({"error": f"This file is {mb:.1f} MB — too large for the free cloud demo (limit ~4.5 MB). Use the local app for full-size videos (see the GitHub link)."}), 413

        payload.seek(0)
        return send_file(payload, as_attachment=True, download_name=name, mimetype=mime)

    except instaloader.exceptions.LoginRequiredException:
        return jsonify({"error": "Instagram is blocking anonymous access from this server (common for datacenter IPs). Try again, or use the local app for reliable downloads."}), 401
    except instaloader.exceptions.PrivateProfileNotFollowedException:
        return jsonify({"error": "This account is private. Private posts need the local app."}), 403
    except (instaloader.exceptions.ConnectionException, instaloader.exceptions.BadResponseException) as e:
        msg = str(e)
        if "404" in msg:
            return jsonify({"error": "Post not found — it may be deleted or private."}), 404
        if "401" in msg or "login" in msg.lower():
            return jsonify({"error": "Instagram blocked this server's request. This is expected on cloud IPs — use the local app for reliable downloads."}), 401
        if "429" in msg or "wait" in msg.lower():
            return jsonify({"error": "Instagram is rate-limiting this server. Wait a bit and retry, or use the local app."}), 429
        return jsonify({"error": "Instagram blocked or failed this request. Cloud servers get blocked often — use the local app for reliable downloads."}), 502
    except Exception as e:
        return jsonify({"error": f"Download failed: {e}"}), 500
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
