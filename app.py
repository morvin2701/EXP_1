"""
Insta Reel / Post Downloader
----------------------------
A small local web app. Paste an Instagram reel/post link and it downloads
the media (video, image, or a zip for carousels).

- Public posts: works anonymously (Instagram may sometimes block anonymous
  requests — logging in fixes that).
- Private accounts: log in with YOUR Instagram account. You can only download
  private posts from accounts that you follow (same as in the app).

Run:  python app.py   →  open http://127.0.0.1:5000
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

try:
    import browser_cookie3
except ImportError:
    browser_cookie3 = None
from flask import Flask, jsonify, render_template, request, send_file

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
SESSIONS_DIR = BASE_DIR / "sessions"
DOWNLOADS_DIR.mkdir(exist_ok=True)
SESSIONS_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

# One shared Instaloader instance (this is a single-user local tool).
L = instaloader.Instaloader(
    download_video_thumbnails=False,
    save_metadata=False,
    compress_json=False,
    post_metadata_txt_pattern="",
    max_connection_attempts=1,
)

# Kept between /api/login and /api/login/2fa when Instagram asks for a code.
_pending_2fa_user = None


def _session_file(username: str) -> Path:
    return SESSIONS_DIR / f"session-{username}"


def _try_restore_session() -> None:
    """On startup, restore the most recently saved login session if one exists."""
    sessions = sorted(
        SESSIONS_DIR.glob("session-*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for s in sessions:
        username = s.name.replace("session-", "", 1)
        try:
            L.load_session_from_file(username, str(s))
            print(f"[+] Restored Instagram session for @{username}")
            return
        except Exception as e:
            print(f"[!] Could not restore session for @{username}: {e}")


def _fresh_loader() -> instaloader.Instaloader:
    return instaloader.Instaloader(
        download_video_thumbnails=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern="",
        max_connection_attempts=1,
    )


COOKIE_NAMES = ("sessionid", "csrftoken", "ds_user_id", "mid", "ig_did")


def _login_with_cookies(cookies: dict) -> str:
    """Build a logged-in Instaloader from browser cookies. Returns username."""
    global L
    fresh = _fresh_loader()
    for name in COOKIE_NAMES:
        if cookies.get(name):
            fresh.context._session.cookies.set(
                name, cookies[name], domain=".instagram.com"
            )
    username = fresh.test_login()
    if not username:
        raise ValueError(
            "Instagram didn't accept this session. Make sure you are logged in "
            "to instagram.com in that browser, then try again."
        )
    fresh.context.username = username
    L = fresh
    L.save_session_to_file(str(_session_file(username)))
    return username


SHORTCODE_RE = re.compile(r"/(?:reels?|p|tv)/([A-Za-z0-9_-]{5,})")


def extract_shortcode(url: str) -> str:
    """Pull the post shortcode out of any Instagram post/reel URL."""
    url = url.strip()
    if not url:
        raise ValueError("Please paste an Instagram link.")

    # Bare shortcode pasted directly
    if re.fullmatch(r"[A-Za-z0-9_-]{5,}", url):
        return url

    if "instagram.com" not in url:
        raise ValueError("That doesn't look like an Instagram link.")

    # Share links (instagram.com/share/...) redirect to the real post URL.
    if "/share/" in url:
        try:
            resp = http_requests.get(
                url,
                allow_redirects=True,
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            url = resp.url
        except Exception:
            raise ValueError("Couldn't resolve this share link. Try copying the direct post/reel link instead.")

    m = SHORTCODE_RE.search(url)
    if not m:
        raise ValueError("Couldn't find a post/reel in that link. Use a link like instagram.com/reel/XXXX or instagram.com/p/XXXX.")
    return m.group(1)


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:80] or "instagram"


def download_post(shortcode: str):
    """Download a post's media files. Returns (list_of_file_paths, post)."""
    post = instaloader.Post.from_shortcode(L.context, shortcode)

    tmpdir = Path(tempfile.mkdtemp(prefix="ig_", dir=str(DOWNLOADS_DIR)))
    old_dirname = L.dirname_pattern
    try:
        L.dirname_pattern = str(tmpdir)
        L.filename_pattern = "{shortcode}"
        L.download_post(post, target="")
    finally:
        L.dirname_pattern = old_dirname

    media = sorted(
        f for f in tmpdir.iterdir()
        if f.suffix.lower() in (".mp4", ".jpg", ".jpeg", ".png", ".webp")
    )
    # A video post also downloads its own files fine; but if a video exists,
    # drop the cover image that instaloader saves alongside it.
    videos = [f for f in media if f.suffix.lower() == ".mp4"]
    if videos and post.typename == "GraphVideo":
        media = videos

    if not media:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError("Download finished but no media files were found.")
    return media, post, tmpdir


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    username = L.context.username
    return jsonify({"logged_in": username is not None, "username": username})


@app.route("/api/login", methods=["POST"])
def login():
    global _pending_2fa_user
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lstrip("@")
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "Username and password are required."}), 400

    try:
        L.login(username, password)
    except instaloader.TwoFactorAuthRequiredException:
        _pending_2fa_user = username
        return jsonify({"needs_2fa": True})
    except instaloader.BadCredentialsException:
        return jsonify({"error": "Instagram rejected the password login. This often happens even with the CORRECT password — Instagram blocks logins that don't come from the official app/website. Use “Import login from my browser” below instead: log in to instagram.com in your browser once, then click that button."}), 401
    except instaloader.ConnectionException as e:
        msg = str(e)
        if "checkpoint" in msg.lower() or "challenge" in msg.lower():
            return jsonify({"error": "Instagram wants you to verify this login. Open the Instagram app or website, approve the login attempt, then try again."}), 403
        return jsonify({"error": f"Could not reach Instagram: {msg}"}), 502
    except Exception as e:
        msg = str(e)
        if "checkpoint" in msg.lower() or "challenge" in msg.lower():
            return jsonify({"error": "Instagram wants you to verify this login (security checkpoint). Open instagram.com in your browser, complete the verification it asks for, stay logged in there — then come back and use “Import login from my browser” instead of the password."}), 403
        return jsonify({"error": f"Login failed: {msg}"}), 500

    L.save_session_to_file(str(_session_file(username)))
    return jsonify({"logged_in": True, "username": username})


@app.route("/api/login/2fa", methods=["POST"])
def login_2fa():
    global _pending_2fa_user
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip().replace(" ", "")
    if not _pending_2fa_user:
        return jsonify({"error": "No login is waiting for a 2FA code. Start again."}), 400
    if not code:
        return jsonify({"error": "Enter the 6-digit code."}), 400

    try:
        L.two_factor_login(code)
    except Exception as e:
        return jsonify({"error": f"2FA failed: {e}"}), 401

    username = _pending_2fa_user
    _pending_2fa_user = None
    L.save_session_to_file(str(_session_file(username)))
    return jsonify({"logged_in": True, "username": username})


@app.route("/api/login/browser", methods=["POST"])
def login_browser():
    """Import an existing instagram.com login from the user's own browser."""
    if browser_cookie3 is None:
        return jsonify({"error": "browser-cookie3 is not installed. Run: pip install browser-cookie3"}), 500

    browsers = [
        ("Chrome", getattr(browser_cookie3, "chrome", None)),
        ("Safari", getattr(browser_cookie3, "safari", None)),
        ("Firefox", getattr(browser_cookie3, "firefox", None)),
        ("Brave", getattr(browser_cookie3, "brave", None)),
        ("Edge", getattr(browser_cookie3, "edge", None)),
        ("Chromium", getattr(browser_cookie3, "chromium", None)),
    ]

    errors = []
    for name, fn in browsers:
        if fn is None:
            continue
        try:
            jar = fn(domain_name="instagram.com")
            cookies = {c.name: c.value for c in jar if c.value}
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
        if not cookies.get("sessionid"):
            errors.append(f"{name}: no Instagram login found")
            continue
        try:
            username = _login_with_cookies(cookies)
            return jsonify({"logged_in": True, "username": username, "source": name})
        except ValueError as e:
            errors.append(f"{name}: {e}")
        except Exception as e:
            errors.append(f"{name}: {e}")

    return jsonify({
        "error": "Couldn't find a working Instagram login in any browser. "
                 "Log in to instagram.com in Chrome/Safari/Firefox first, then try again. "
                 "(If your Mac showed a Keychain permission popup, click “Always Allow” and retry.)",
        "details": errors[:6],
    }), 404


@app.route("/api/login/cookie", methods=["POST"])
def login_cookie():
    """Manual fallback: user pastes their `sessionid` cookie from the browser."""
    data = request.get_json(silent=True) or {}
    sessionid = (data.get("sessionid") or "").strip().strip('"')
    if not sessionid:
        return jsonify({"error": "Paste the sessionid cookie value."}), 400
    try:
        username = _login_with_cookies({"sessionid": sessionid})
        return jsonify({"logged_in": True, "username": username})
    except ValueError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        return jsonify({"error": f"Session import failed: {e}"}), 500


@app.route("/api/logout", methods=["POST"])
def logout():
    global L
    username = L.context.username
    if username:
        _session_file(username).unlink(missing_ok=True)
    try:
        L.close()
    except Exception:
        pass
    # Reset to a fresh anonymous context.
    L = _fresh_loader()
    return jsonify({"logged_in": False})


@app.route("/api/download", methods=["POST"])
def download():
    data = request.get_json(silent=True) or {}
    url = data.get("url") or ""

    try:
        shortcode = extract_shortcode(url)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    tmpdir = None
    try:
        media, post, tmpdir = download_post(shortcode)

        owner = _safe_name(post.owner_username)
        base = f"{owner}_{shortcode}"

        if len(media) == 1:
            f = media[0]
            payload = io.BytesIO(f.read_bytes())
            download_name = f"{base}{f.suffix.lower()}"
            mimetype = "video/mp4" if f.suffix.lower() == ".mp4" else "image/jpeg"
        else:
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as zf:
                for i, f in enumerate(media, 1):
                    zf.write(f, f"{base}_{i:02d}{f.suffix.lower()}")
            payload.seek(0)
            download_name = f"{base}.zip"
            mimetype = "application/zip"

        payload.seek(0)
        return send_file(
            payload,
            as_attachment=True,
            download_name=download_name,
            mimetype=mimetype,
        )

    except instaloader.exceptions.LoginRequiredException:
        return jsonify({"error": "Instagram requires a login to view this post. Log in below and try again."}), 401
    except instaloader.exceptions.PrivateProfileNotFollowedException:
        return jsonify({"error": "This account is private and the logged-in account doesn't follow it. You can only download private posts from accounts you follow."}), 403
    except instaloader.exceptions.BadResponseException as e:
        msg = str(e)
        if "404" in msg or "Fetching Post metadata failed" in msg:
            hint = "Post not found. It may have been deleted, or it's from a private account — log in with an account that follows them."
            if L.context.username:
                hint = "Couldn't fetch this post. It may be deleted, or the account is private and you don't follow it."
            return jsonify({"error": hint}), 404
        if "401" in msg or "login" in msg.lower():
            return jsonify({"error": "Instagram blocked the anonymous request. Log in below and try again."}), 401
        return jsonify({"error": f"Instagram error: {msg}"}), 502
    except instaloader.exceptions.ConnectionException as e:
        msg = str(e)
        if "401" in msg or "login" in msg.lower() or "redirected" in msg.lower():
            return jsonify({"error": "Instagram blocked the anonymous request. Log in below and try again."}), 401
        if "429" in msg or "Please wait" in msg:
            return jsonify({"error": "Instagram is rate-limiting you. Wait a few minutes and try again."}), 429
        return jsonify({"error": f"Connection problem: {msg}"}), 502
    except Exception as e:
        return jsonify({"error": f"Download failed: {e}"}), 500
    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    _try_restore_session()
    # Port 5001: macOS AirPlay Receiver occupies port 5000.
    print("\n  Insta Reel Downloader → http://127.0.0.1:5001\n")
    app.run(host="127.0.0.1", port=5001, debug=False)
