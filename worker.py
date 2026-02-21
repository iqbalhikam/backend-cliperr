import os
import json
import logging
from yt_dlp import YoutubeDL
from redis_client import redis_client

logging.basicConfig(level=logging.INFO)

DOWNLOAD_DIR = "/tmp/downloads"
COOKIE_DIR = "/tmp/cookies"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(COOKIE_DIR, exist_ok=True)


while True:

    _, job = redis_client.blpop("download_queue")

    job = json.loads(job)

    job_id = job["id"]
    url = job["url"]
    cookie_path = job.get("cookie")

    logging.info(f"START JOB {job_id}")

    try:

        ydl_opts = {
            "outtmpl": f"{DOWNLOAD_DIR}/{job_id}.%(ext)s",
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "noplaylist": True
        }

        if cookie_path:
            ydl_opts["cookiefile"] = cookie_path

        with YoutubeDL(ydl_opts) as ydl:

            info = ydl.extract_info(url, download=True)
            ext = info.get("ext","mp4")

        redis_client.set(f"job:{job_id}", f"done:{ext}")

        logging.info(f"DONE JOB {job_id}")

    except Exception as e:

        redis_client.set(f"job:{job_id}", f"error:{str(e)}")

        logging.error(e)