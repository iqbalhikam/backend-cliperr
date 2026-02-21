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
    t=str(t).strip()

    if t.isdigit():
        return float(t)

    p=[float(x) for x in t.split(":")]

    if len(p)==3:
        return p[0]*3600+p[1]*60+p[2]

    if len(p)==2:
        return p[0]*60+p[1]

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
    start = job.get("start","0")
    end = job.get("end","999999")
    cookie_path = job.get("cookie")

    logging.info(f"START JOB {job_id}")
    logging.info(f"URL: {url}")
    logging.info(f"CLIP: {start} -> {end}")

    try:

        # =========================
        # DOWNLOAD FULL VIDEO HD
        # =========================

        temp_template = f"{DOWNLOAD_DIR}/{job_id}_full.%(ext)s"

        ydl_opts = {
            "outtmpl": temp_template,

            # kualitas mentok
            "format": "bestvideo+bestaudio/best",

            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True
        }

        # kalau ada cookie (member-only)
        if cookie_path:
            ydl_opts["cookiefile"] = cookie_path
            logging.info("Using cookies")

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            ext = info.get("ext","mp4")

        full_path = f"{DOWNLOAD_DIR}/{job_id}_full.{ext}"

        if not os.path.exists(full_path):
            raise Exception("Download gagal, file tidak ditemukan")

        # =========================
        # CLIP PAKAI FFMPEG
        # =========================

        final_path = f"{DOWNLOAD_DIR}/{job_id}.mp4"

        logging.info("Cutting clip with FFmpeg...")

        subprocess.run([
            "ffmpeg",
            "-y",
            "-ss", str(start),
            "-to", str(end),
            "-i", full_path,
            "-c", "copy",
            "-avoid_negative_ts", "1",
            final_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if not os.path.exists(final_path):
            raise Exception("FFmpeg gagal membuat clip")

        # =========================
        # CLEAN TEMP FILE
        # =========================
        try:
            os.remove(full_path)
        except:
            pass

        try:
            if cookie_path:
                os.remove(cookie_path)
        except:
            pass

        # =========================
        # SAVE STATUS REDIS
        # =========================
        redis_client.set(f"job:{job_id}", "done:mp4")

        size=os.path.getsize(final_path)/1024/1024
        logging.info(f"DONE JOB {job_id} | {size:.2f} MB")

    except Exception as e:

        redis_client.set(f"job:{job_id}", f"error:{str(e)}")

        logging.error(f"FAILED JOB {job_id}")
        logging.error(e)