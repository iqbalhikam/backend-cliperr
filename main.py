import os
import uuid
import logging
import asyncio
import subprocess
import zipfile

from fastapi import (
    FastAPI,
    BackgroundTasks,
    Form,
    File,
    UploadFile,
    HTTPException,
    Request
)
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from yt_dlp import YoutubeDL

# Rate limit
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

# =========================
# CONFIG
# =========================
API_KEY = os.getenv("MY_SECRET_API_KEY")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN")

if not API_KEY:
    raise Exception("MY_SECRET_API_KEY not set")

DOWNLOAD_DIR = "/tmp/downloads"
COOKIE_DIR = "/tmp/cookies"
MAX_DURATION = 600  # 10 menit max

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(COOKIE_DIR, exist_ok=True)

# =========================
# LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [APP] %(message)s"
)

# =========================
# APP INIT
# =========================
app = FastAPI()

# CORS (tidak wildcard)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN] if ALLOWED_ORIGIN else [],
    allow_credentials=True,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

# =========================
# AUTH MIDDLEWARE
# =========================
@app.middleware("http")
async def verify_api_key(request: Request, call_next):

    # File endpoint boleh tanpa API key
    if request.url.path.startswith("/file"):
        return await call_next(request)

    client_key = request.headers.get("x-api-key")

    if client_key != API_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized")

    return await call_next(request)

# =========================
# UTILITIES
# =========================
jobs_db = {}

def parse_time(t: str) -> float:
    t = str(t).strip()
    if t.isdigit():
        return float(t)
    p = [float(x) for x in t.split(":")]
    if len(p) == 3:
        return p[0]*3600 + p[1]*60 + p[2]
    if len(p) == 2:
        return p[0]*60 + p[1]
    return float(p[0])

async def remove_file(path: str, delay: int = 300):
    await asyncio.sleep(delay)
    if os.path.exists(path):
        os.remove(path)
        logging.info(f"Auto deleted: {path}")

def validate_youtube_url(url: str):
    if "youtube.com" not in url and "youtu.be" not in url:
        raise HTTPException(status_code=400, detail="Only YouTube URL allowed")

# =========================
# CORE PROCESSOR
# =========================
def process_media(job_id, url, start, end, mode, interval, cookie_path):

    final_path = None

    try:
        logging.info(f"[JOB {job_id}] START")
        jobs_db[job_id] = {"status": "processing", "step": 1}

        ydl_opts = {"quiet": True}
        if cookie_path:
            ydl_opts["cookiefile"] = cookie_path

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = info.get("formats", [])

        videos = [f for f in formats if f.get("vcodec") != "none"]
        videos = sorted(videos, key=lambda x: (x.get("height", 0), x.get("tbr", 0)), reverse=True)

        if not videos:
            raise Exception("No video stream found")

        best_video_url = videos[0]["url"]

        jobs_db[job_id]["step"] = 2

        final_path = f"{DOWNLOAD_DIR}/{job_id}.mp4"

        cmd = [
            "ffmpeg", "-y",
            "-ss", start,
            "-to", end,
            "-i", best_video_url,
            "-c", "copy",
            "-avoid_negative_ts", "1",
            final_path
        ]

        process = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if process.returncode != 0:
            raise Exception(process.stderr.decode())

        if not os.path.exists(final_path):
            raise Exception("Output file not created")

        jobs_db[job_id] = "done:mp4"

        logging.info(f"[JOB {job_id}] DONE")

    except Exception as e:
        logging.error(f"[JOB {job_id}] FAILED: {e}")
        jobs_db[job_id] = f"error:{str(e)}"

    finally:
        if cookie_path and os.path.exists(cookie_path):
            os.remove(cookie_path)

# =========================
# API
# =========================
@app.post("/download")
@limiter.limit("5/minute")
async def start_download(
    request: Request,
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    start: str = Form(...),
    end: str = Form(...)
):

    validate_youtube_url(url)

    try:
        start_sec = parse_time(start)
        end_sec = parse_time(end)

        if end_sec <= start_sec:
            raise HTTPException(status_code=400, detail="End must be greater")

        if end_sec - start_sec > MAX_DURATION:
            raise HTTPException(status_code=400, detail="Max duration 10 minutes")

    except:
        raise HTTPException(status_code=400, detail="Invalid time format")

    job_id = str(uuid.uuid4())
    jobs_db[job_id] = {"status": "processing", "step": 1}

    background_tasks.add_task(
        process_media,
        job_id,
        url,
        str(start_sec),
        str(end_sec),
        "video",
        2,
        None
    )

    return {"job_id": job_id}

@app.get("/status/{job_id}")
def status(job_id: str):

    res = jobs_db.get(job_id)

    if not res:
        return {"status": "error"}

    if isinstance(res, dict):
        return res

    if res.startswith("done"):
        return {
            "status": "finished",
            "download": f"/file/{job_id}.mp4"
        }

    return {"status": "error", "msg": res}

@app.get("/file/{name}")
def get_file(name: str, background_tasks: BackgroundTasks):

    path = f"{DOWNLOAD_DIR}/{name}"

    if not os.path.exists(path):
        raise HTTPException(status_code=404)

    background_tasks.add_task(remove_file, path)

    return FileResponse(path, media_type="video/mp4", filename=name)