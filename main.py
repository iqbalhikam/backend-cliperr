import os
import uuid
import json
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse
from redis_client import redis_client

app = FastAPI()

DOWNLOAD_DIR="/tmp/downloads"
COOKIE_DIR="/tmp/cookies"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(COOKIE_DIR, exist_ok=True)


@app.post("/download")
async def start_download(
    url: str = Form(...),
    start: str = Form(...),   # TAMBAHAN
    end: str = Form(...),     # TAMBAHAN
    cookie: UploadFile | None = File(default=None)
):

    job_id=str(uuid.uuid4())
    cookie_path=None

    if cookie:
        cookie_path=f"{COOKIE_DIR}/{job_id}.txt"
        with open(cookie_path,"wb") as f:
            f.write(await cookie.read())

    job={
        "id":job_id,
        "url":url,
        "start":start,   # TAMBAHAN
        "end":end,       # TAMBAHAN
        "cookie":cookie_path
    }

    redis_client.rpush("download_queue", json.dumps(job))

    return {"job_id":job_id}


@app.get("/status/{job_id}")

def status(job_id:str):

    res=redis_client.get(f"job:{job_id}")

    if not res:
        return {"status":"processing"}

    if res.startswith("done"):

        ext=res.split(":")[1]

        return {

            "status":"finished",
            "download":f"/file/{job_id}.{ext}"
        }

    return {"status":"error","msg":res}


@app.get("/file/{name}")

def get_file(name:str):

    path=f"{DOWNLOAD_DIR}/{name}"
    return FileResponse(path)