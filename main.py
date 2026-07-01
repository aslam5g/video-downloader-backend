"""
Unlimited Video Downloader Backend
Supports: YouTube, Facebook, Instagram, TikTok, Twitter/X
Built with FastAPI + yt-dlp (no third-party API, no request limits)
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
import asyncio
import functools

app = FastAPI(title="Unlimited Video Downloader API")

# Allow requests from any frontend (adjust in production if you want to restrict)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class VideoRequest(BaseModel):
    url: str


def extract_info(video_url: str):
    """
    Runs yt-dlp to extract video info without downloading the file.
    This works for YouTube, Facebook, Instagram, TikTok, Twitter/X, and
    hundreds of other sites supported by yt-dlp.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": "best",
        # Helps avoid some bot-detection issues on YouTube
        "extractor_args": {
            "youtube": {"player_client": ["android"]}
        },
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        return info


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

            # Build a readable quality label
            if height:
                quality_label = f"{height}p"
            elif vcodec == "none" and acodec != "none":
                quality_label = "Audio Only"
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
        # Fallback: single direct url (common for TikTok/Instagram/FB)
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
    return {"status": "ok", "message": "Video Downloader API is running"}


@app.post("/api/fetch")
async def fetch_video(request: VideoRequest):
    video_url = request.url.strip()

    if not video_url:
        raise HTTPException(status_code=400, detail="URL is required")

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
