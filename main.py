from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
import subprocess, uuid, os, yt_dlp, time

app = FastAPI()

# =====================
# REQUEST MODEL
# =====================
class ClipRequest(BaseModel):
    url: HttpUrl
    start: str
    end: str


# =====================
# TIME PARSER
# =====================
def parse_time(t:str)->float:
    t=str(t)
    if t.isdigit():
        return float(t)

    p=[float(x) for x in t.split(":")]

    if len(p)==3:
        return p[0]*3600+p[1]*60+p[2]
    if len(p)==2:
        return p[0]*60+p[1]
    return p[0]


# =====================
# DELETE FILE LATER
# =====================
def cleanup(path):
    time.sleep(8)
    if os.path.exists(path):
        os.remove(path)


# =====================
# GET BEST STREAM URL
# =====================
def get_best_stream(url:str):

    ydl_opts={
        "quiet":True,
        "noplaylist":True,
        "extractor_args":{
            "youtube":{
                "player_client":["android","ios","tv","web"]
            }
        }
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info=ydl.extract_info(url,download=False)

        # ambil BEST video+audio
        if "requested_formats" in info:
            v=info["requested_formats"][0]["url"]
            a=info["requested_formats"][1]["url"]
            return v,a

        # fallback single stream
        return info["url"],None


# =====================
# MAIN API
# =====================
@app.post("/clip")
def clip(req:ClipRequest, bg:BackgroundTasks):

    start=parse_time(req.start)
    end=parse_time(req.end)

    if end<=start:
        raise HTTPException(400,"End harus lebih besar")

    if end-start>180:
        raise HTTPException(400,"Max 3 menit")

    filename=f"clip_{uuid.uuid4().hex}.mp4"

    try:

        video_url,audio_url=get_best_stream(str(req.url))

        # ===== FFmpeg PRO CLIP =====
        # TANPA encode ulang
        if audio_url:

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

        else:

            cmd=[
                "ffmpeg",
                "-ss",str(start),
                "-to",str(end),
                "-i",video_url,
                "-c","copy",
                filename
            ]

        subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

        if not os.path.exists(filename):
            raise HTTPException(500,"FFmpeg gagal")

        bg.add_task(cleanup,filename)

        return FileResponse(filename,filename="clip.mp4")

    except Exception as e:
        if os.path.exists(filename):
            os.remove(filename)
        raise HTTPException(500,str(e))