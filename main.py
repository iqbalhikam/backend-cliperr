from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
import yt_dlp
import os
import uuid
import time

app = FastAPI()

# ========================
# GENERATE COOKIES
# ========================
cookie_content = os.getenv("YOUTUBE_COOKIES")
COOKIE_PATH = "cookies.txt"

if cookie_content and not os.path.exists(COOKIE_PATH):
    with open(COOKIE_PATH, "w", encoding="utf-8") as f:
        f.write(cookie_content)
    print("cookies.txt generated")

# ========================
# CORS
# ========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========================
# REQUEST MODEL
# ========================
class ClipRequest(BaseModel):
    url: HttpUrl
    start: str
    end: str

# ========================
# SAFE DELETE
# ========================
def remove_file(path: str):
    time.sleep(5)  # beri waktu agar download benar2 selesai
    try:
        if os.path.exists(path):
            os.remove(path)
            print("deleted:", path)
    except Exception as e:
        print("delete fail:", e)

# ========================
# TIME PARSER STRONG VERSION
# ========================
def parse_time(t: str) -> float:
    t = str(t).strip()

    # detik langsung
    if t.isdigit():
        return float(t)

    # format HH:MM:SS / MM:SS
    parts = t.split(":")
    parts = [float(x) for x in parts]

    if len(parts) == 3:
        return parts[0]*3600 + parts[1]*60 + parts[2]
    elif len(parts) == 2:
        return parts[0]*60 + parts[1]
    elif len(parts) == 1:
        return parts[0]

    raise ValueError("Invalid time format")

# ========================
# MAIN ENDPOINT
# ========================
@app.post("/download-clip")
def download_clip(req: ClipRequest, background_tasks: BackgroundTasks):

    filename = f"clip_{uuid.uuid4().hex}.mp4"

    try:
        start = parse_time(req.start)
        end = parse_time(req.end)

        duration = end - start

        # ==== SERVER PROTECTION RULES ====
        if duration <= 0:
            raise HTTPException(400, "End harus lebih besar dari start")

        if duration > 180:
            raise HTTPException(400, "Max clip 180 detik")

        print("clip:", req.url, start, end)

        ydl_opts = {

            # kualitas tertinggi
            'format': 'bv*+ba/b',

            # output
            'outtmpl': filename,

            # CUT LANGSUNG (SUPER PENTING)
            'download_ranges': lambda info, ydl: [{
                "start_time": start,
                "end_time": end
            }],

            'force_keyframes_at_cuts': True,

            'merge_output_format': 'mp4',
            'noplaylist': True,
            'quiet': True,
            'nocheckcertificate': True,
            'cookiefile': COOKIE_PATH if os.path.exists(COOKIE_PATH) else None,

            # anti blocking youtube
            'extractor_args': {
                'youtube': {
                    'player_client': ['ios','android','tv','web']
                }
            },

            'concurrent_fragment_downloads': 3,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([str(req.url)])

        if not os.path.exists(filename):
            raise HTTPException(500, "Clip gagal dibuat")

        background_tasks.add_task(remove_file, filename)

        return FileResponse(
            filename,
            filename="clip.mp4",
            media_type="video/mp4"
        )

    except HTTPException:
        raise

    except Exception as e:
        if os.path.exists(filename):
            os.remove(filename)
        print("ERROR:", e)
        raise HTTPException(500, str(e))