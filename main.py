"""
Instagram Video Downloader Backend
Built with FastAPI + yt-dlp + cookie rotation (no third-party API)
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
import asyncio
import functools
import os
import re
import tempfile

app = FastAPI(title="Instagram Video Downloader API")

# Allow requests from any frontend (adjust in production if you want to restrict)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------
# Instagram cookie rotation (single file, multiple accounts)
# ------------------------------------------------------------------
# All cookies live in one file: cookies_accounts.txt, next to this file.
# Separate each account's exported cookies.txt content with a line like:
#   ---ACCOUNT1---
#   (cookies.txt content for account 1, in standard Netscape format)
#   ---ACCOUNT2---
#   (cookies.txt content for account 2)
#
# Each request randomly picks one account's cookies to spread load
# across multiple dummy accounts instead of hammering a single one.
COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies_accounts.txt")

_ACCOUNT_SPLIT_RE = re.compile(r"^---ACCOUNT\d*---\s*$", re.MULTILINE)


def load_account_cookie_blocks():
    """
    Reads cookies_accounts.txt and splits it into a list of cookie blocks,
    one per account, based on the ---ACCOUNTx--- markers.
    """
    if not os.path.isfile(COOKIES_FILE):
        return []

    with open(COOKIES_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Split on the marker lines, drop empty leading chunk
    parts = _ACCOUNT_SPLIT_RE.split(content)
    blocks = [p.strip() for p in parts if p.strip()]
    return blocks


def get_next_cookiefile_path():
    """
    Picks the next account's cookie block in round-robin order
    (account 1, then 2, then 3... then back to 1), writes it to a
    temporary file, and returns the path so yt-dlp can use it via
    the cookiefile option.
    """
    blocks = load_account_cookie_blocks()
    if not blocks:
        return None

    global _rotation_index
    index = _rotation_index % len(blocks)
    _rotation_index = (_rotation_index + 1) % len(blocks)

    chosen = blocks[index]

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    tmp.write(chosen)
    tmp.close()
    return tmp.name


# Tracks which account to use next (round-robin position)
_rotation_index = 0


class VideoRequest(BaseModel):
    url: str


def extract_info(video_url: str):
    """
    Runs yt-dlp to extract Instagram video info without downloading the file.
    Uses the next account's cookies (round-robin) to bypass Instagram's
    login-wall for public posts/reels.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": "best",
    }

    cookie_path = get_next_cookiefile_path()
    if cookie_path:
        ydl_opts["cookiefile"] = cookie_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            return info
    finally:
        # Clean up the temporary cookie file after use
        if cookie_path and os.path.exists(cookie_path):
            os.remove(cookie_path)


def build_response(info: dict):
    """
    Converts raw yt-dlp info dict into a clean, frontend-friendly format.
    """
    title = info.get("title", "Untitled Video")
    author = info.get("uploader") or info.get("channel") or "Unknown"
    thumbnail = info.get("thumbnail", "")

    medias = []

    # If the extractor found multiple formats, list the useful ones
    formats = info.get("formats", [])
    if formats:
        seen_qualities = set()
        for f in formats:
            # Skip formats with no direct url or audio-only unless needed
            if not f.get("url"):
                continue

            height = f.get("height")
            ext = f.get("ext", "mp4")
            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")

            # Skip audio-only formats — only show actual video downloads
            if vcodec == "none":
                continue

            # Build a readable quality label
            if height:
                quality_label = f"{height}p"
            else:
                quality_label = f.get("format_note", "Unknown")

            # Avoid duplicate quality entries
            dedupe_key = (quality_label, ext)
            if dedupe_key in seen_qualities:
                continue
            seen_qualities.add(dedupe_key)

            medias.append({
                "quality": quality_label,
                "type": "video" if vcodec != "none" else "audio",
                "extension": ext,
                "data_size": f.get("filesize") or f.get("filesize_approx") or 0,
                "url": f["url"],
            })
    else:
        # Fallback: single direct url (common for Instagram reels/posts)
        if info.get("url"):
            medias.append({
                "quality": "Default",
                "type": "video",
                "extension": info.get("ext", "mp4"),
                "data_size": info.get("filesize") or 0,
                "url": info["url"],
            })

    return {
        "title": title,
        "author": author,
        "thumbnail": thumbnail,
        "medias": medias,
    }


@app.get("/")
async def root():
    return {"status": "ok", "message": "Instagram Video Downloader API is running"}


@app.post("/api/fetch")
async def fetch_video(request: VideoRequest):
    video_url = request.url.strip()

    if not video_url:
        raise HTTPException(status_code=400, detail="URL is required")

    if "instagram.com" not in video_url:
        raise HTTPException(status_code=400, detail="শুধুমাত্র Instagram লিংক সাপোর্ট করা হয়")

    try:
        # Run the blocking yt-dlp call in a thread so it doesn't block the event loop
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, functools.partial(extract_info, video_url))

        if not info:
            raise HTTPException(status_code=404, detail="Could not extract video information")

        result = build_response(info)
        return result

    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"Failed to process video: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
