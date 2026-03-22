import json
import subprocess
import sys
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ── Install yt-dlp at runtime if not present ──────────────────────────────
def ensure_ytdlp():
    try:
        import yt_dlp
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "yt-dlp", "-q"])

ensure_ytdlp()
import yt_dlp


# ── CORS headers ──────────────────────────────────────────────────────────
CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Content-Type": "application/json",
}


def get_info(video_url: str) -> dict:
    """Extract video info + direct format URLs using yt-dlp."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        # Use a realistic browser User-Agent
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)

    formats = info.get("formats", [])

    # ── Build a clean list of download options ────────────────────────────
    options = []

    # Collect combined video+audio formats (mp4 preferred)
    seen_heights = set()
    video_formats = [
        f for f in formats
        if f.get("vcodec") != "none"
        and f.get("acodec") != "none"
        and f.get("url")
        and f.get("ext") in ("mp4", "webm", "mov")
    ]
    # Sort best quality first
    video_formats.sort(key=lambda f: f.get("height") or 0, reverse=True)

    for f in video_formats:
        h = f.get("height") or 0
        if h and h not in seen_heights:
            seen_heights.add(h)
            label = f"{h}p"
            if h >= 1080:
                label += " Full HD"
            elif h >= 720:
                label += " HD"
            elif h >= 480:
                label += " SD"
            options.append({
                "type": "video",
                "label": label,
                "ext": f.get("ext", "mp4"),
                "url": f["url"],
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "height": h,
            })
        if len(seen_heights) >= 4:
            break

    # If no combined formats found, add best video-only + best audio
    if not options:
        best_video = next(
            (f for f in reversed(formats)
             if f.get("vcodec") != "none" and f.get("url")),
            None,
        )
        if best_video:
            options.append({
                "type": "video",
                "label": "Best Quality",
                "ext": best_video.get("ext", "mp4"),
                "url": best_video["url"],
                "filesize": best_video.get("filesize"),
                "height": best_video.get("height"),
            })

    # Audio-only (best mp3/m4a/opus)
    audio_formats = [
        f for f in formats
        if f.get("vcodec") == "none"
        and f.get("acodec") != "none"
        and f.get("url")
    ]
    audio_formats.sort(key=lambda f: f.get("abr") or 0, reverse=True)
    if audio_formats:
        af = audio_formats[0]
        options.append({
            "type": "audio",
            "label": "Audio Only (MP3)",
            "ext": af.get("ext", "m4a"),
            "url": af["url"],
            "filesize": af.get("filesize"),
            "abr": af.get("abr"),
        })

    return {
        "title": info.get("title", "Video"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "platform": info.get("extractor_key", ""),
        "options": options,
    }


def fmt_size(b):
    if not b:
        return "unknown size"
    if b < 1024 * 1024:
        return f"{b/1024:.0f} KB"
    return f"{b/1024/1024:.1f} MB"


# ── Vercel handler ────────────────────────────────────────────────────────
class handler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass  # silence default logs

    # Handle pre-flight CORS
    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in CORS.items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        video_url = params.get("url", [None])[0]

        if not video_url:
            self._respond(400, {"error": "Missing ?url= parameter"})
            return

        try:
            data = get_info(video_url)
            self._respond(200, {"ok": True, "data": data})
        except yt_dlp.utils.DownloadError as e:
            self._respond(422, {"ok": False, "error": str(e)})
        except Exception as e:
            self._respond(500, {"ok": False, "error": str(e)})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
            video_url = payload.get("url")
        except Exception:
            self._respond(400, {"error": "Invalid JSON"})
            return

        if not video_url:
            self._respond(400, {"error": "Missing url field"})
            return

        try:
            data = get_info(video_url)
            self._respond(200, {"ok": True, "data": data})
        except yt_dlp.utils.DownloadError as e:
            self._respond(422, {"ok": False, "error": str(e)})
        except Exception as e:
            self._respond(500, {"ok": False, "error": str(e)})

    def _respond(self, status: int, body: dict):
        payload = json.dumps(body).encode()
        self.send_response(status)
        for k, v in CORS.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
