import os
import uuid
import logging
import asyncio
import subprocess
import zipfile
from fastapi import FastAPI, BackgroundTasks, Form, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from yt_dlp import YoutubeDL

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
# UTIL
# =========================
def parse_time(t: str) -> float:
    t = str(t).strip()
    if t.isdigit(): return float(t)
    p = [float(x) for x in t.split(":")]
    if len(p) == 3: return p[0]*3600 + p[1]*60 + p[2]
    if len(p) == 2: return p[0]*60 + p[1]
    return float(p[0])

async def remove_file(path: str, delay: int = 300):
    await asyncio.sleep(delay)
    if os.path.exists(path):
        os.remove(path)

def get_best_stream(url, cookie_path=None):
    logging.info(f"[STREAM] Extracting info from URL: {url}")

    ydl_opts = {
            "quiet": True,
        "js_runtimes": {
            "node": {}
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"]
            }
        }
    }

    if cookie_path:
        logging.info("[STREAM] Using cookie file")
        ydl_opts["cookiefile"] = cookie_path

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = info.get("formats", [])
    logging.info(f"[STREAM] Total formats found: {len(formats)}")

    videos = [f for f in formats if f.get("vcodec") != "none"]
    videos = sorted(
        videos,
        key=lambda x: (
            x.get("height", 0),
            x.get("fps", 0),
            x.get("tbr", 0)
        ),
        reverse=True
    )

    if videos:
        best = videos[0]
        logging.info(
            f"[STREAM] Selected format: "
            f"{best.get('height')}p | "
            f"{best.get('fps')}fps | "
            f"{best.get('tbr')}kbps"
        )
        return best["url"]

    logging.warning("[STREAM] No video-only format found, using fallback URL")
    return info.get("url")
# =========================
# WORKER
# =========================
def process_media(job_id, url, start, end, mode, interval, cookie_path):

    logging.info(f"[JOB {job_id}] START")
    logging.info(f"[JOB {job_id}] Mode: {mode}")
    logging.info(f"[JOB {job_id}] Time: {start} -> {end}")

    try:
        jobs_db[job_id] = {
            "status": "processing",
            "message": "Processing...",
            "step": 1
        }

        stream_url = get_best_stream(url, cookie_path)

        if not stream_url:
            raise Exception("Failed to get stream URL")

        logging.info(f"[JOB {job_id}] Stream URL acquired")

        # =========================
        # SUPER HD PNG
        # =========================
        if mode == "super_photo":
            final_path = f"{DOWNLOAD_DIR}/{job_id}.png"

            cmd = [
                "ffmpeg", "-y",
                "-ss", start,
                "-i", stream_url,
                "-frames:v", "1",
                "-compression_level", "0",
                "-vf", "scale=iw*2:ih*2:flags=lanczos",
                final_path
            ]

        # =========================
        # BURST
        # =========================
        elif mode == "burst":

            if not end:
                raise Exception("Burst mode requires end time")

            burst_folder = f"{DOWNLOAD_DIR}/{job_id}_frames"
            os.makedirs(burst_folder, exist_ok=True)

            cmd = [
                "ffmpeg", "-y",
                "-ss", start,
                "-to", end,
                "-i", stream_url,
                "-vf", f"fps=1/{interval}",
                f"{burst_folder}/frame_%03d.png"
            ]

            logging.info(f"[JOB {job_id}] Executing FFmpeg burst...")

            process = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True
            )

            if process.returncode != 0:
                logging.error(f"[JOB {job_id}] FFmpeg burst error:\n{process.stderr}")
                raise Exception(process.stderr)

            zip_path = f"{DOWNLOAD_DIR}/{job_id}.zip"

            with zipfile.ZipFile(zip_path, "w") as z:
                for file in sorted(os.listdir(burst_folder)):
                    z.write(os.path.join(burst_folder, file), file)

            final_path = zip_path

        # =========================
        # PHOTO JPG
        # =========================
        elif mode == "photo":
            final_path = f"{DOWNLOAD_DIR}/{job_id}.jpg"

            cmd = [
                "ffmpeg", "-y",
                "-ss", start,
                "-i", stream_url,
                "-frames:v", "1",
                "-q:v", "1",
                final_path
            ]

        # =========================
        # VIDEO
        # =========================
        else:
            final_path = f"{DOWNLOAD_DIR}/{job_id}.mp4"

            cmd = [
                "ffmpeg", "-y",
                "-ss", start,
                "-to", end,
                "-i", stream_url,
                "-c", "copy",
                final_path
            ]

        # Jalankan FFmpeg (kecuali burst karena sudah dijalankan)
        if mode != "burst":
            logging.info(f"[JOB {job_id}] Executing FFmpeg...")
            logging.info(f"[JOB {job_id}] Command: {' '.join(cmd)}")

            process = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True
            )

            if process.returncode != 0:
                logging.error(f"[JOB {job_id}] FFmpeg error:\n{process.stderr}")
                raise Exception(process.stderr)

        if not os.path.exists(final_path):
            raise Exception("Output file not created")

        size = os.path.getsize(final_path) / 1024 / 1024
        logging.info(f"[JOB {job_id}] SUCCESS | File: {final_path} | {size:.2f} MB")

        ext = final_path.split(".")[-1]
        jobs_db[job_id] = f"done:{ext}"

    except Exception as e:
        logging.error(f"[JOB {job_id}] FAILED: {str(e)}")
        jobs_db[job_id] = f"error:{str(e)}"

    finally:
        if cookie_path and os.path.exists(cookie_path):
            os.remove(cookie_path)
            logging.info(f"[JOB {job_id}] Cookie cleaned")
# =========================
# API
# =========================
@app.post("/download")
async def start_download(
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    start: str = Form(...),
    end: str | None = Form(None),
    mode: str = Form("video"),
    interval: int = Form(2),
    cookie: UploadFile | None = File(default=None)
):
    job_id = str(uuid.uuid4())
    cookie_path = None

    if cookie:
        cookie_path = f"{COOKIE_DIR}/{job_id}.txt"
        with open(cookie_path, "wb") as f:
            f.write(await cookie.read())

    jobs_db[job_id] = {"status": "processing"}

    background_tasks.add_task(
        process_media,
        job_id,
        url,
        start,
        end,
        mode,
        interval,
        cookie_path
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
        ext = res.split(":")[1]
        return {
            "status": "finished",
            "download": f"/file/{job_id}.{ext}"
        }

    return {"status": "error"}

@app.get("/file/{name}")
def get_file(name: str, background_tasks: BackgroundTasks):
    path = f"{DOWNLOAD_DIR}/{name}"
    if not os.path.exists(path):
        raise HTTPException(status_code=404)

    background_tasks.add_task(remove_file, path)

    if name.endswith(".png"):
        return FileResponse(path, media_type="image/png")
    if name.endswith(".jpg"):
        return FileResponse(path, media_type="image/jpeg")
    if name.endswith(".zip"):
        return FileResponse(path, media_type="application/zip")
    return FileResponse(path, media_type="video/mp4")