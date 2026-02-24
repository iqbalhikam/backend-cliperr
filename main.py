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

# =========================
# LOGGING SETUP
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [APP] %(message)s"
)

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
# PARSE TIME
# =========================
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

# =========================
# AUTO CLEANUP
# =========================
async def remove_file(path: str, delay: int = 300):
    await asyncio.sleep(delay)
    if os.path.exists(path):
        os.remove(path)
        logging.info(f"Auto deleted: {path}")

# =========================
# CORE PROCESSOR
# =========================
def process_media(job_id, url, start, end, mode, interval, cookie_path):

    final_path = None

    try:
        logging.info(f"[JOB {job_id}] START | Mode: {mode}")
        jobs_db[job_id] = {"status": "processing", "message": "Extracting stream...", "step": 1}

        ydl_opts = {"quiet": True}
        if cookie_path:
            ydl_opts["cookiefile"] = cookie_path
            logging.info(f"[JOB {job_id}] Using cookies")

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = info.get("formats", [])

        # Detect separated stream
        videos = [f for f in formats if f.get("vcodec") != "none" and f.get("acodec") == "none"]
        audios = [f for f in formats if f.get("acodec") != "none" and f.get("vcodec") == "none"]

        best_video_url = None
        best_audio_url = None

        if videos:
            videos = sorted(videos, key=lambda x: (x.get("height", 0), x.get("tbr", 0)), reverse=True)
            best_video_url = videos[0]["url"]

        if audios:
            audios = sorted(audios, key=lambda x: x.get("tbr", 0)), 
            best_audio_url = audios[0][0]["url"]

        # Fallback combined
        if not best_video_url:
            combined = [f for f in formats if f.get("vcodec") != "none" and f.get("acodec") != "none"]
            if combined:
                combined = sorted(combined, key=lambda x: (x.get("height", 0), x.get("tbr", 0)), reverse=True)
                best_video_url = combined[0]["url"]

        if not best_video_url:
            raise Exception("No valid video stream found")

        jobs_db[job_id]["step"] = 2
        jobs_db[job_id]["message"] = "Processing media..."

        # =========================
        # SUPER HD PNG
        # =========================
        if mode == "super_photo":
            final_path = f"{DOWNLOAD_DIR}/{job_id}.png"

            cmd = [
                "ffmpeg", "-y",
                "-ss", start,
                "-i", best_video_url,
                "-frames:v", "1",
                "-compression_level", "0",
                "-vf", "scale=iw:ih:flags=lanczos",
                final_path
            ]

        # =========================
        # BURST MODE
        # =========================
        elif mode == "burst":
            if not end:
                raise Exception("Burst mode membutuhkan waktu end")

            burst_folder = f"{DOWNLOAD_DIR}/{job_id}_frames"
            os.makedirs(burst_folder, exist_ok=True)

            cmd = [
                "ffmpeg", "-y",
                "-ss", start,
                "-to", end,
                "-i", best_video_url,
                "-vf", f"fps=1/{interval}",
                f"{burst_folder}/frame_%03d.png"
            ]

            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

            zip_path = f"{DOWNLOAD_DIR}/{job_id}.zip"
            with zipfile.ZipFile(zip_path, "w") as z:
                for file in sorted(os.listdir(burst_folder)):
                    z.write(os.path.join(burst_folder, file), file)

            final_path = zip_path

        # =========================
        # NORMAL PHOTO
        # =========================
        elif mode == "photo":
            final_path = f"{DOWNLOAD_DIR}/{job_id}.jpg"

            cmd = [
                "ffmpeg", "-y",
                "-ss", start,
                "-i", best_video_url,
                "-frames:v", "1",
                "-q:v", "1",
                final_path
            ]

        # =========================
        # VIDEO CLIP
        # =========================
        else:
            final_path = f"{DOWNLOAD_DIR}/{job_id}.mp4"

            if best_audio_url:
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", start, "-to", end, "-i", best_video_url,
                    "-ss", start, "-to", end, "-i", best_audio_url,
                    "-map", "0:v", "-map", "1:a",
                    "-c", "copy",
                    "-avoid_negative_ts", "1",
                    final_path
                ]
            else:
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", start, "-to", end, "-i", best_video_url,
                    "-c", "copy",
                    "-avoid_negative_ts", "1",
                    final_path
                ]

        if mode != "burst":
            process = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if process.returncode != 0:
                raise Exception(process.stderr.decode())

        if not os.path.exists(final_path):
            raise Exception("Output file not created")

        ext = final_path.split(".")[-1]
        jobs_db[job_id] = f"done:{ext}"

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
async def start_download(
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    start: str = Form(...),
    end: str | None = Form(None),
    mode: str = Form("video"),
    interval: int = Form(2),
    cookie: UploadFile | None = File(default=None)
):
    try:
        start_sec = parse_time(start)
        end_sec = parse_time(end) if end else None

        if mode == "video":
            if end_sec is None or end_sec <= start_sec:
                raise HTTPException(status_code=400, detail="End harus lebih besar dari start")

    except:
        raise HTTPException(status_code=400, detail="Format waktu tidak valid")

    job_id = str(uuid.uuid4())
    cookie_path = None

    if cookie:
        cookie_path = f"{COOKIE_DIR}/{job_id}.txt"
        with open(cookie_path, "wb") as f:
            f.write(await cookie.read())

    jobs_db[job_id] = {"status": "processing", "message": "Waiting...", "step": 1}

    background_tasks.add_task(
        process_media,
        job_id,
        url,
        str(start_sec),
        str(end_sec) if end_sec else None,
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
        return {"status": "finished", "download": f"/file/{job_id}.{ext}"}

    return {"status": "error", "msg": res}

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