
import os
import uuid
import logging
import subprocess
from fastapi import FastAPI, BackgroundTasks, Form, File, UploadFile, HTTPException
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
jobs_db = {}

# =========================
# PARSE & VALIDASI WAKTU
# =========================
def parse_time(t: str) -> float:
    t = str(t).strip()
    if t.isdigit(): return float(t)
    p = [float(x) for x in t.split(":")]
    if len(p) == 3: return p[0]*3600 + p[1]*60 + p[2]
    if len(p) == 2: return p[0]*60 + p[1]
    return float(p[0])

# =========================
# FUNGSI WORKER (Berjalan di Background)
# =========================
def process_video_on_the_fly(job_id: str, url: str, start: str, end: str, cookie_path: str = None):
    jobs_db[job_id] = "processing"
    logging.info(f"START JOB {job_id} | URL: {url} | CLIP: {start} -> {end}")

    final_path = f"{DOWNLOAD_DIR}/{job_id}.mp4"

    try:
        ydl_opts = {"quiet": True}
        if cookie_path:
            ydl_opts["cookiefile"] = cookie_path
            logging.info("Using cookies")

        # 1. AMBIL DIRECT URL STREAM (TANPA DOWNLOAD)
        logging.info("Extracting stream URLs...")
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = info.get("formats", [])
        
        # 2. DETEKSI FORMAT & SUSUN PERINTAH FFMPEG
        videos = [f for f in formats if f.get("vcodec") != "none" and f.get("acodec") == "none"]
        audios = [f for f in formats if f.get("acodec") != "none" and f.get("vcodec") == "none"]

        if videos and audios:
            # Skenario Stream Terpisah (Khas YouTube)
            videos = sorted(videos, key=lambda x: (x.get("height", 0), x.get("tbr", 0)), reverse=True)
            audios = sorted(audios, key=lambda x: x.get("tbr", 0), reverse=True)
            
            best_video_url = videos[0]["url"]
            best_audio_url = audios[0]["url"]

            logging.info("Format terpisah dideteksi. Menjalankan FFmpeg...")
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
            # Skenario Stream Gabungan (Livestream Umum/TikTok/File MP4 biasa)
            best_combined = [f for f in formats if f.get("vcodec") != "none" and f.get("acodec") != "none"]
            if best_combined:
                best_combined = sorted(best_combined, key=lambda x: (x.get("height", 0), x.get("tbr", 0)), reverse=True)
                stream_url = best_combined[0]["url"]
            else:
                stream_url = info.get("url")
                
            logging.info("Format gabungan dideteksi. Menjalankan FFmpeg...")
            cmd = [
                "ffmpeg", "-y",
                "-ss", start, "-to", end, "-i", stream_url,
                "-c", "copy",
                "-avoid_negative_ts", "1",
                final_path
            ]

        # 3. EKSEKUSI PEMOTONGAN LANGSUNG DARI URL
        process = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        if process.returncode != 0:
            raise Exception(f"FFmpeg Error: {process.stderr}")

        if not os.path.exists(final_path):
            raise Exception("FFmpeg gagal membuat clip (File tidak ditemukan)")

        # 4. CLEANUP COOKIE & UPDATE STATUS SUKSES
        try:
            if cookie_path: os.remove(cookie_path)
        except: pass

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
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    start: str = Form(...),
    end: str = Form(...),
    cookie: UploadFile | None = File(default=None)
):
    # Validasi input waktu
    try:
        start_sec = parse_time(start)
        end_sec = parse_time(end)
        
        if end_sec <= start_sec:
            raise HTTPException(status_code=400, detail="Waktu 'end' harus lebih besar dari 'start'")
        
        # Batasi durasi klip (contoh: maksimal 10 menit / 600 detik)
        if (end_sec - start_sec) > 600:
            raise HTTPException(status_code=400, detail="Maksimal durasi klip adalah 10 menit")
            
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=400, detail="Format waktu tidak valid")

    job_id = str(uuid.uuid4())
    cookie_path = None

    if cookie:
        cookie_path = f"{COOKIE_DIR}/{job_id}.txt"
        with open(cookie_path, "wb") as f:
            f.write(await cookie.read())

    # Set status awal di memory
    jobs_db[job_id] = "processing"

    # Lempar ke background task
    background_tasks.add_task(
        process_video_on_the_fly, 
        job_id, 
        url, 
        str(start_sec), 
        str(end_sec), 
        cookie_path
    )

    return {"job_id": job_id, "status": "processing"}


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
        
    if res.startswith("error"):
        return {"status": "error", "msg": res.replace("error:", "", 1)}

    return {"status": "error", "msg": "Unknown error"}


@app.get("/file/{name}")
def get_file(name: str):
    path = f"{DOWNLOAD_DIR}/{name}"
    
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File video tidak ditemukan atau sudah dihapus")
        
    return FileResponse(path)