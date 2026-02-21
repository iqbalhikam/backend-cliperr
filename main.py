import os
import uuid
import logging
import subprocess
import random
import time

from fastapi import FastAPI, BackgroundTasks, Form, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from yt_dlp import YoutubeDL

# =========================
# LOGGING
# =========================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [APP] %(message)s")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = "/tmp/downloads"
COOKIE_DIR = "/tmp/cookies"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(COOKIE_DIR, exist_ok=True)

jobs_db = {}

# =========================
# USER AGENTS
# =========================

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Mozilla/5.0 (X11; Linux x86_64)",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
    "Mozilla/5.0 (Android 13; Mobile)"
]

def random_agent():
    return random.choice(USER_AGENTS)

# =========================
# BUILD YTDLP OPTIONS
# =========================

def build_ydl(cookie_path=None):

    opts = {
        "quiet": True,
        "nocheckcertificate": True,

        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best",

        "extractor_args": {
            "youtube": {
                "player_client": ["web"]
            }
        },

        "http_headers": {
            "User-Agent": random_agent(),
        },

        "retries": 5,
    }

    if cookie_path:
        opts["cookiefile"] = cookie_path

    return opts

# =========================
# PARSE TIME
# =========================

def parse_time(t: str) -> float:
    t = str(t).strip()
    if t.isdigit():
        return float(t)

    parts = [float(x) for x in t.split(":")]

    if len(parts) == 3:
        return parts[0]*3600 + parts[1]*60 + parts[2]

    if len(parts) == 2:
        return parts[0]*60 + parts[1]

    return float(parts[0])


# =========================
# VIDEO PROCESSOR
# =========================

def process_video(job_id, url, start, end, cookie_path=None):

    jobs_db[job_id] = "processing"
    final_path = f"{DOWNLOAD_DIR}/{job_id}.mp4"

    try:

        # ======================
        # STEP 1: Extract (android first)
        # ======================
        logging.info("Extracting with android client...")

        info = None

        try:
            with YoutubeDL(build_ydl(cookie_path, "android")) as ydl:
                info = ydl.extract_info(url, download=False)
        except:
            pass

        # fallback to web if needed
        if not info:
            logging.info("Fallback to web client...")
            with YoutubeDL(build_ydl(cookie_path, "web")) as ydl:
                info = ydl.extract_info(url, download=False)

        if not info:
            raise Exception("Failed to extract video info")

        # ======================
        # STEP 2: Select BEST format
        # ======================

        with YoutubeDL(build_ydl(cookie_path, "android")) as ydl:
            selector = ydl.build_format_selector("bestvideo+bestaudio/best")
            formats = list(selector(info))

            if not formats:
                raise Exception("No format found")

            best = formats[0]

        requested = best.get("requested_formats")

        if requested:
            video_url = requested[0]["url"]
            audio_url = requested[1]["url"]

            logging.info("Using separate streams (HD)")

            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-to", str(end),
                "-i", video_url,
                "-i", audio_url,
                "-map", "0:v",
                "-map", "1:a",
                "-c:v", "copy",
                "-c:a", "copy",
                final_path
            ]

        else:
            stream_url = best["url"]

            logging.info("Using combined stream")

            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-to", str(end),
                "-i", stream_url,
                "-c", "copy",
                final_path
            ]

        # ======================
        # STEP 3: Execute FFmpeg
        # ======================

        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True
        )

        if proc.returncode != 0:
            raise Exception(proc.stderr[:1000])

        if not os.path.exists(final_path):
            raise Exception("Clip not created")

        jobs_db[job_id] = "done:mp4"
        logging.info("JOB DONE")

    except Exception as e:
        jobs_db[job_id] = f"error:{str(e)}"
        logging.error(f"FAILED: {e}")


# =========================
# API
# =========================

@app.post("/download")
async def start_download(
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    start: str = Form(...),
    end: str = Form(...),
    cookie: UploadFile | None = File(default=None)
):

    start_sec = parse_time(start)
    end_sec = parse_time(end)

    if end_sec <= start_sec:
        raise HTTPException(400, "End time must be greater than start")

    if (end_sec - start_sec) > 600:
        raise HTTPException(400, "Max clip length is 10 minutes")

    job_id = str(uuid.uuid4())

    cookie_path = None
    if cookie:
        cookie_path = f"{COOKIE_DIR}/{job_id}.txt"
        with open(cookie_path, "wb") as f:
            f.write(await cookie.read())

    background_tasks.add_task(
        process_video,
        job_id,
        url,
        start_sec,
        end_sec,
        cookie_path
    )

    return {"job_id": job_id}


@app.get("/status/{job_id}")
def status(job_id: str):

    res = jobs_db.get(job_id)

    if not res or res == "processing":
        return {"status": "processing"}

    if res.startswith("done"):
        return {
            "status": "finished",
            "download": f"/file/{job_id}.mp4"
        }

    return {"status": "error", "msg": res}


@app.get("/file/{name}")
def get_file(name: str):

    path = f"{DOWNLOAD_DIR}/{name}"

    if not os.path.exists(path):
        raise HTTPException(404)

    return FileResponse(path)