# ⚡ ReelGrab — Instagram Reel & Post Downloader

Download Instagram reels, video posts, photos, and carousels.

There are **two ways** to run it:

| | Local app (`app.py`) | Cloud demo (Vercel) |
|---|---|---|
| Public posts | ✅ | ✅ (may be IP-blocked) |
| **Private accounts you follow** | ✅ | ❌ |
| Login (browser / cookie / password) | ✅ | ❌ |
| Full-size videos | ✅ | ❌ (~4.5 MB cap) |
| Your data stays on your machine | ✅ | — |

The **local app is the real product.** The cloud demo is a convenience for quick
public downloads only.

---

## 🖥️ Run locally (full features)

```bash
./start.sh
```

Then open **http://127.0.0.1:5001**.
(Port 5001 because macOS AirPlay Receiver occupies 5000.)

### How to use
1. Copy any reel/post link (Instagram → Share → Copy Link).
2. Paste it and hit **Download**.
   - Reels/videos → `.mp4`
   - Photos → `.jpg`
   - Carousels → `.zip`

### Private accounts
Open the account menu (top-right) and connect your Instagram. Three methods,
most reliable first:

1. **Import login from my browser** *(recommended)* — be logged in to
   instagram.com in Chrome/Safari/Firefox, click the button. If macOS asks for
   Keychain access, enter your **Mac password** and click **“Always Allow.”**
2. **Paste session cookie** — instagram.com → F12 → Application → Cookies →
   copy the `sessionid` value.
3. **Username + password** (2FA supported) — often blocked by Instagram even
   with the correct password; use method 1 or 2 if so.

You can only download private posts from accounts **you follow** — the same
access you have in the Instagram app. Your login goes directly from your
computer to Instagram and is saved locally in `sessions/`.

---

## ☁️ The live cloud demo (Vercel)

A public-posts-only version runs as a Flask serverless function in
[`api/index.py`](api/index.py).

**Deploy your own:**

1. Push this repo to GitHub (see below).
2. Go to [vercel.com](https://vercel.com) → **Add New → Project** → import the repo.
3. Vercel auto-detects the Python function and [`vercel.json`](vercel.json) — just click **Deploy**.
4. *(Optional)* In **Settings → Environment Variables**, add
   `GITHUB_REPO_URL` = your repo URL, so the site links back to the source and
   the "run locally" instructions.

**Known cloud limitations (by design, not bugs):**
- No login → **public posts only**.
- Instagram often blocks datacenter IPs, so downloads may fail; the local app is reliable.
- Serverless response cap → files over ~4.5 MB are refused.

---

## Project layout

```
app.py                  # Local full-featured Flask app (login + downloads)
templates/index.html    # Local UI
api/index.py            # Vercel serverless function (public-only)
api/templates/cloud.html# Cloud UI
vercel.json             # Vercel build/route config
requirements.txt        # Base deps (cloud + local)
requirements-local.txt  # Base deps + browser-cookie3 (local only)
start.sh                # One-command local launcher
```

---

---

## 🧹 MarkEraser — watermark / object remover (separate tool)

A standalone local app for erasing watermarks or objects from **your own**
photos and videos. Fully offline.

```bash
./start_eraser.sh
```

Open **http://127.0.0.1:5002**.

- **Photos** — brush over the mark → content-aware inpainting → `cleaned.png`.
- **Videos** — two modes:
  - **⚡ Fast** — ffmpeg `delogo`; great for fixed semi-transparent marks, keeps
    audio, processes in seconds. (Needs ffmpeg: `brew install ffmpeg`.)
  - **✨ HD AI** — [ProPainter](https://github.com/sczhou/ProPainter) deep-learning
    temporal inpainting; genuinely clean even for opaque logos, runs on Apple
    MPS. Slower (30 s–several minutes). Enable it once with:
    ```bash
    ./setup_ai.sh      # installs PyTorch + ProPainter (~2 GB)
    ```
    HD processing is capped to ~720p-equivalent for memory safety, and the
    original audio is muxed back automatically.

**Intended for content you own or have the rights to edit.**

---

For personal use only. Respect creators’ rights and Instagram’s Terms of Use.
