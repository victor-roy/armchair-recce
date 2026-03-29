import logging
import os
import subprocess

import yt_dlp
from yt_dlp.utils import sanitize_filename

MAX_AUDIO_DURATION_SECONDS = 30 * 60  # 30 minutes


def _parse_proxy(line):
    """Accept either socks5://user:pass@host:port or host:port:user:pass (Webshare format)."""
    line = line.strip()
    if not line:
        return None
    if line.startswith("socks5://") or line.startswith("http://"):
        return line
    parts = line.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        return f"socks5://{user}:{password}@{host}:{port}"
    return line  # unknown format — pass through and let yt-dlp error


# Load residential proxy list from env var (newline-separated, either format)
_PROXY_LIST = [
    p
    for p in (
        _parse_proxy(raw) for raw in os.environ.get("YTDLP_PROXIES", "").splitlines()
    )
    if p
]
if _PROXY_LIST:
    logging.info(f"Loaded {len(_PROXY_LIST)} proxies from YTDLP_PROXIES")

_BOT_SIGNALS = ("sign in", "bot", "confirm", "getpot")


def get_youtube_title(url, max_duration=MAX_AUDIO_DURATION_SECONDS):
    logging.info(f"Fetching video info: {url}")
    proxies = _PROXY_LIST or [None]
    last_exc = None
    for proxy in proxies:
        opts = {"quiet": True, "no_warnings": True}
        if proxy:
            opts["proxy"] = proxy
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get("title", "youtube_video")
                duration = info.get("duration", 0)
            if duration > max_duration:
                max_min = max_duration // 60
                raise ValueError(
                    f"Video is {int(duration // 60)} min long — maximum is {max_min} minutes."
                )
            sanitized = sanitize_filename(title)
            logging.info(
                f"Title: {title} ({int(duration // 60)}m {int(duration % 60)}s)"
            )
            return sanitized
        except ValueError:
            raise
        except Exception as e:
            if any(w in str(e).lower() for w in _BOT_SIGNALS):
                logging.warning(f"Proxy {proxy} bot-detected, trying next...")
                last_exc = e
                continue
            raise
    raise last_exc


def download_youtube_audio(url, output_dir):
    audio_path = os.path.join(output_dir, "audio.wav")
    proxies = _PROXY_LIST or [None]
    last_exc = None
    for proxy in proxies:
        cmd = [
            "yt-dlp",
            "--force-ipv4",
            "-f",
            "bestaudio/best",
            "-o",
            os.path.join(output_dir, "audio.%(ext)s"),
            "--extract-audio",
            "--audio-format",
            "wav",
            "--postprocessor-args",
            "ffmpeg:-ar 16000 -ac 1",
            "--quiet",
            *(["--proxy", proxy] if proxy else []),
            url,
        ]
        try:
            logging.info("Downloading audio...")
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return audio_path
        except subprocess.CalledProcessError as e:
            err = (e.stderr or "").lower()
            if any(w in err for w in _BOT_SIGNALS):
                logging.warning(
                    f"Proxy {proxy} bot-detected on download, trying next..."
                )
                last_exc = e
                continue
            raise
    raise last_exc
