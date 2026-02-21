import os
import uuid
import logging
import subprocess
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse
from yt_dlp import YoutubeDL

# =========================
# LOGGING SETUP
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [APP] %(message)s"
)

app = FastAPI()

DOWNLOAD_DIR = "/tmp/downloads"
COOKIE_DIR = "/tmp/cookies"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(COOKIE_DIR, exist_ok=True)

# =========================
# PENGGANTI REDIS (In-Memory DB)
# =========================
# Menyimpan status job di dalam RAM. 
jobs_db = {}

# =========================
# FUNGSI WORKER (Berjalan di Background)
# =========================
def process_video(job_id: str, url: str, start: str, end: str, cookie_path: str = None):
    jobs_db[job_id] = "processing"
    logging.info(f"START JOB {job_id} | URL: {url} | CLIP: {start} -> {end}")

    try:
        # 1. DOWNLOAD VIDEO
        temp_template = f"{DOWNLOAD_DIR}/{job_id}_full.%(ext)s"
        ydl_opts = {
            "outtmpl": temp_template,
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True
        }

        if cookie_path:
            ydl_opts["cookiefile"] = cookie_path
            logging.info("Using cookies")

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            ext = info.get("ext", "mp4")

        full_path = f"{DOWNLOAD_DIR}/{job_id}_full.{ext}"

        if not os.path.exists(full_path):
            raise Exception("Download gagal, file tidak ditemukan")

        # 2. POTONG VIDEO DENGAN FFMPEG
        final_path = f"{DOWNLOAD_DIR}/{job_id}.mp4"
        logging.info("Cutting clip with FFmpeg...")

        process = subprocess.run([
            "ffmpeg",
            "-y",
            "-ss", str(start),
            "-to", str(end),
            "-i", full_path,
            "-c", "copy",
            "-avoid_negative_ts", "1",
            final_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        if process.returncode != 0:
            error_msg = process.stderr
            raise Exception(f"FFmpeg Error: {error_msg}")

        if not os.path.exists(final_path):
            raise Exception("FFmpeg gagal membuat clip")

        # 3. CLEANUP FILE TEMP
        try: os.remove(full_path)
        except: pass
        
        try:
            if cookie_path: os.remove(cookie_path)
        except: pass

        # 4. UPDATE STATUS SUKSES
        jobs_db[job_id] = "done:mp4"
        size = os.path.getsize(final_path) / 1024 / 1024
        logging.info(f"DONE JOB {job_id} | {size:.2f} MB")

    except Exception as e:
        # UPDATE STATUS ERROR
        jobs_db[job_id] = f"error:{str(e)}"
        logging.error(f"FAILED JOB {job_id} | Error: {e}")


# =========================
# ENDPOINTS API
# =========================

@app.post("/download")
async def start_download(
    background_tasks: BackgroundTasks, # Injeksi BackgroundTasks
    url: str = Form(...),
    start: str = Form(...),
    end: str = Form(...),
    cookie: UploadFile | None = File(default=None)
):
    job_id = str(uuid.uuid4())
    cookie_path = None

    if cookie:
        cookie_path = f"{COOKIE_DIR}/{job_id}.txt"
        with open(cookie_path, "wb") as f:
            f.write(await cookie.read())

    # Set status awal
    jobs_db[job_id] = "pending"

    # Jalankan fungsi process_video di latar belakang tanpa memblokir API
    background_tasks.add_task(process_video, job_id, url, start, end, cookie_path)

    return {"job_id": job_id}


@app.get("/status/{job_id}")
def status(job_id: str):
    res = jobs_db.get(job_id)

    if not res or res in ["pending", "processing"]:
        return {"status": "processing"}

    if res.startswith("done"):
        ext = res.split(":")[1]
        return {
            "status": "finished",
            "download": f"/file/{job_id}.{ext}"
        }

    return {"status": "error", "msg": res}


@app.get("/file/{name}")
def get_file(name: str):
    path = f"{DOWNLOAD_DIR}/{name}"
    return FileResponse(path)