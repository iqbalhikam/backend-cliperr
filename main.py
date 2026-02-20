from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
import yt_dlp
import subprocess
import uuid
import os
import time
import logging

# ======================
# LOGGING SETUP
# ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

app = FastAPI()

# ======================
# MODEL
# ======================
class ClipRequest(BaseModel):
    url: HttpUrl
    start: str
    end: str

# ======================
# TIME PARSER
# ======================
def parse_time(t:str)->float:
    t=str(t).strip()

    if t.isdigit():
        return float(t)

    p=[float(x) for x in t.split(":")]

    if len(p)==3:
        return p[0]*3600+p[1]*60+p[2]
    if len(p)==2:
        return p[0]*60+p[1]

    return p[0]

# ======================
# CLEANUP
# ======================
def cleanup(path):
    time.sleep(10)
    try:
        if os.path.exists(path):
            os.remove(path)
            logging.info(f"Deleted temp file {path}")
    except Exception as e:
        logging.error(f"Cleanup error {e}")

# ======================
# GET BEST STREAM (REAL PRO)
# ======================
def get_best_stream(url):

    logging.info("Extracting formats from YouTube...")

    with yt_dlp.YoutubeDL({"quiet":True}) as ydl:
        info=ydl.extract_info(url,download=False)

    formats=info["formats"]

    # VIDEO ONLY
    videos=[
        f for f in formats
        if f.get("vcodec")!="none" and f.get("acodec")=="none"
    ]

    # SORT BEST RESOLUTION + BITRATE
    videos=sorted(
        videos,
        key=lambda x:(x.get("height",0),x.get("tbr",0)),
        reverse=True
    )

    best_video=videos[0]

    # AUDIO ONLY
    audios=[
        f for f in formats
        if f.get("acodec")!="none" and f.get("vcodec")=="none"
    ]

    audios=sorted(
        audios,
        key=lambda x:x.get("tbr",0),
        reverse=True
    )

    best_audio=audios[0]

    logging.info(
        f"BEST VIDEO → {best_video.get('height')}p  bitrate={best_video.get('tbr')}"
    )

    logging.info(
        f"BEST AUDIO → bitrate={best_audio.get('tbr')}"
    )

    return best_video["url"],best_audio["url"]

# ======================
# MAIN API
# ======================
@app.post("/clip")
def clip(req:ClipRequest, bg:BackgroundTasks):

    start=parse_time(req.start)
    end=parse_time(req.end)

    if end<=start:
        raise HTTPException(400,"End harus lebih besar")

    duration=end-start

    if duration>180:
        raise HTTPException(400,"Max clip 180 detik")

    filename=f"clip_{uuid.uuid4().hex}.mp4"

    try:

        logging.info("====================================")
        logging.info(f"CLIP REQUEST")
        logging.info(f"URL: {req.url}")
        logging.info(f"START: {start}")
        logging.info(f"END: {end}")
        logging.info("====================================")

        video_url,audio_url=get_best_stream(str(req.url))

        cmd=[
            "ffmpeg",
            "-ss",str(start),
            "-to",str(end),
            "-i",video_url,
            "-ss",str(start),
            "-to",str(end),
            "-i",audio_url,
            "-map","0:v",
            "-map","1:a",
            "-c","copy",
            "-avoid_negative_ts","1",
            filename
        ]

        logging.info("Running FFmpeg...")
        logging.info(" ".join(cmd))

        subprocess.run(cmd)

        if not os.path.exists(filename):
            raise HTTPException(500,"FFmpeg gagal membuat file")

        size=os.path.getsize(filename)/1024/1024
        logging.info(f"Clip created → {size:.2f} MB")

        bg.add_task(cleanup,filename)

        return FileResponse(
            filename,
            filename="clip.mp4",
            media_type="video/mp4"
        )

    except Exception as e:
        logging.error(f"ERROR: {e}")

        if os.path.exists(filename):
            os.remove(filename)

        raise HTTPException(500,str(e))