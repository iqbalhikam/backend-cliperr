import os
import json
import logging
import subprocess
from yt_dlp import YoutubeDL
from redis_client import redis_client

# =========================
# LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WORKER] %(message)s"
)

# =========================
# DIR SETUP
# =========================
DOWNLOAD_DIR = "/tmp/downloads"
COOKIE_DIR = "/tmp/cookies"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(COOKIE_DIR, exist_ok=True)


# =========================
# PARSE TIME
# =========================
def parse_time(t: str) -> float:
    t = str(t).strip()
    if t.isdigit(): return float(t)
    p = [float(x) for x in t.split(":")]
    if len(p) == 3: return p[0]*3600 + p[1]*60 + p[2]
    if len(p) == 2: return p[0]*60 + p[1]
    return p[0]


# =========================
# WORKER LOOP
# =========================
logging.info("Worker started... waiting job")

while True:
    # Tunggu job dari Redis
    _, job = redis_client.blpop("download_queue")
    job = json.loads(job)

    job_id = job["id"]
    url = job["url"]
    start = str(parse_time(job.get("start", "0")))
    end = str(parse_time(job.get("end", "999999")))
    cookie_path = job.get("cookie")

    logging.info(f"START JOB {job_id} | URL: {url} | CLIP: {start} -> {end}")

    try:
        ydl_opts = {"quiet": True}
        if cookie_path:
            ydl_opts["cookiefile"] = cookie_path
            logging.info("Using cookies")

        # ==========================================
        # 1. EXTRACT METADATA (TIDAK DOWNLOAD FULL)
        # ==========================================
        logging.info("Extracting stream URLs...")
        with YoutubeDL(ydl_opts) as ydl:
            # download=False memastikan kita hanya mengambil direct URL
            info = ydl.extract_info(url, download=False)

        formats = info.get("formats", [])
        
        # Coba pisahkan Video & Audio (Khas YouTube)
        videos = [f for f in formats if f.get("vcodec") != "none" and f.get("acodec") == "none"]
        audios = [f for f in formats if f.get("acodec") != "none" and f.get("vcodec") == "none"]

        final_path = f"{DOWNLOAD_DIR}/{job_id}.mp4"
        cmd = []

        # ==========================================
        # 2. SUSUN PERINTAH FFMPEG BERDASARKAN FORMAT
        # ==========================================
        if videos and audios:
            # Skenario Format Terpisah
            videos = sorted(videos, key=lambda x: (x.get("height", 0), x.get("tbr", 0)), reverse=True)
            audios = sorted(audios, key=lambda x: x.get("tbr", 0), reverse=True)
            
            best_video_url = videos[0]["url"]
            best_audio_url = audios[0]["url"]

            logging.info("Detected separated streams. Building FFmpeg command...")
            
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
            # Skenario Format Gabungan (Livestream umum/Direct MP4)
            best_combined = [f for f in formats if f.get("vcodec") != "none" and f.get("acodec") != "none"]
            
            if best_combined:
                best_combined = sorted(best_combined, key=lambda x: (x.get("height", 0), x.get("tbr", 0)), reverse=True)
                stream_url = best_combined[0]["url"]
            else:
                stream_url = info.get("url") # Fallback paling mentok
                
            logging.info("Detected combined streams. Building FFmpeg command...")

            cmd = [
                "ffmpeg", "-y",
                "-ss", start, "-to", end, "-i", stream_url,
                "-c", "copy",
                "-avoid_negative_ts", "1",
                final_path
            ]

        # ==========================================
        # 3. PROSES CLIPPING LANGSUNG DARI URL
        # ==========================================
        logging.info("Cutting clip directly with FFmpeg...")
        
        # stderr=subprocess.PIPE penting agar error FFmpeg terbaca di log
        process = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        if process.returncode != 0:
            error_msg = process.stderr
            raise Exception(f"FFmpeg Error: {error_msg}")

        if not os.path.exists(final_path):
            raise Exception("FFmpeg gagal membuat clip (File tidak ditemukan)")

        # ==========================================
        # 4. CLEANUP COOKIE & UPDATE STATUS REDIS
        # ==========================================
        try:
            if cookie_path: os.remove(cookie_path)
        except: pass

        redis_client.set(f"job:{job_id}", "done:mp4")
        
        size = os.path.getsize(final_path) / 1024 / 1024
        logging.info(f"DONE JOB {job_id} | {size:.2f} MB")

    except Exception as e:
        redis_client.set(f"job:{job_id}", f"error:{str(e)}")
        logging.error(f"FAILED JOB {job_id} | Error: {e}")