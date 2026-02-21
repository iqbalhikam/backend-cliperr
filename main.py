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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [APP] %(message)s")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR="/tmp/downloads"
COOKIE_DIR="/tmp/cookies"

os.makedirs(DOWNLOAD_DIR,exist_ok=True)
os.makedirs(COOKIE_DIR,exist_ok=True)

jobs_db={}

# =========================
# ROTATING USER AGENTS
# =========================

USER_AGENTS=[
"Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
"Mozilla/5.0 (X11; Linux x86_64)",
"Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
"Mozilla/5.0 (Android 13; Mobile)"
]

def random_agent():
    return random.choice(USER_AGENTS)

# =========================
# SAFE YTDLP OPTIONS
# =========================

def build_ydl(cookie_path=None):

    opts={

        "quiet":True,
        "nocheckcertificate":True,

        # CRITICAL FOR YOUTUBE 2026
        "extractor_args":{
            "youtube":{
                "player_client":["android","web","tv"]
            }
        },

        # PREVENT FORMAT FAIL
        "format":"bv*+ba/best",

        # NETWORK DISGUISE
        "http_headers":{
            "User-Agent":random_agent(),
            "Accept-Language":"en-US,en"
        },

        # ANTI RATE LIMIT
        "sleep_interval":1,
        "max_sleep_interval":3,

        "retries":10,
        "fragment_retries":10,

        # IMPORTANT FOR LIVE STREAM
        "live_from_start":True,

    }

    if cookie_path:
        opts["cookiefile"]=cookie_path

    return opts

# =========================
# WORKER
# =========================

def process_video_on_the_fly(job_id,url,start,end,cookie_path=None):

    jobs_db[job_id]="processing"
    final=f"{DOWNLOAD_DIR}/{job_id}.mp4"

    logging.info(f"START JOB {job_id}")

    try:

        # retry extraction loop
        info=None

        for attempt in range(5):

            try:
                with YoutubeDL(build_ydl(cookie_path)) as ydl:
                    info=ydl.extract_info(url,download=False)
                break

            except Exception as e:

                logging.warning(f"extract retry {attempt}: {e}")
                time.sleep(2+attempt)

        if not info:
            raise Exception("Cannot extract video info")

        formats=info.get("formats",[])

        videos=[f for f in formats if f.get("vcodec")!="none" and f.get("acodec")=="none"]
        audios=[f for f in formats if f.get("acodec")!="none" and f.get("vcodec")=="none"]

        # pick highest quality safely
        videos=sorted(videos,key=lambda x:(x.get("height",0),x.get("tbr",0)),reverse=True)
        audios=sorted(audios,key=lambda x:x.get("tbr",0),reverse=True)

        if videos and audios:

            v=videos[0]["url"]
            a=audios[0]["url"]

            logging.info("SEPARATE STREAM")

            cmd=[
                "ffmpeg","-y",
                "-ss",start,"-to",end,"-i",v,
                "-ss",start,"-to",end,"-i",a,
                "-map","0:v","-map","1:a",
                "-c","copy",
                final
            ]

        else:

            combined=[f for f in formats if f.get("vcodec")!="none"]

            combined=sorted(combined,key=lambda x:(x.get("height",0),x.get("tbr",0)),reverse=True)

            if not combined:
                raise Exception("no playable stream")

            stream=combined[0]["url"]

            logging.info("COMBINED STREAM")

            cmd=[
                "ffmpeg","-y",
                "-ss",start,"-to",end,"-i",stream,
                "-c","copy",
                final
            ]

        proc=subprocess.run(cmd,stderr=subprocess.PIPE,text=True)

        if proc.returncode!=0:
            raise Exception(proc.stderr[:2000])

        if not os.path.exists(final):
            raise Exception("clip not created")

        jobs_db[job_id]="done:mp4"

        logging.info(f"DONE {job_id}")

    except Exception as e:

        jobs_db[job_id]=f"error:{e}"
        logging.error(f"FAILED {job_id} {e}")

# =========================
# API
# =========================

@app.post("/download")

async def start(
background_tasks:BackgroundTasks,
url:str=Form(...),
start:str=Form(...),
end:str=Form(...),
cookie:UploadFile|None=File(default=None)
):

    job=str(uuid.uuid4())

    cookie_path=None

    if cookie:
        cookie_path=f"{COOKIE_DIR}/{job}.txt"
        with open(cookie_path,"wb") as f:
            f.write(await cookie.read())

    jobs_db[job]="processing"

    background_tasks.add_task(
        process_video_on_the_fly,
        job,url,start,end,cookie_path
    )

    return {"job_id":job}

@app.get("/status/{job}")

def status(job:str):

    res=jobs_db.get(job)

    if not res or res=="processing":
        return {"status":"processing"}

    if res.startswith("done"):
        return {"status":"finished","download":f"/file/{job}.mp4"}

    return {"status":"error","msg":res}

@app.get("/file/{name}")

def file(name:str):

    path=f"{DOWNLOAD_DIR}/{name}"

    if not os.path.exists(path):
        raise HTTPException(404)

    return FileResponse(path)