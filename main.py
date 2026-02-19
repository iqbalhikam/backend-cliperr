from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
import os
import uuid

app = FastAPI()

# --- TAMBAHAN BARU: Membuat cookies.txt otomatis dari .env ---
cookie_content = os.getenv("YOUTUBE_COOKIES")
if cookie_content:
    with open("cookies.txt", "w") as f:
        f.write(cookie_content)
    print("✅ File cookies.txt berhasil di-generate dari Environment Variable!")
else:
    print("⚠️ WARNING: YOUTUBE_COOKIES tidak ditemukan di Environment Variables!")
# -------------------------------------------------------------

# 1. Setup CORS (Agar Next.js bisa akses)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Di production, ganti "*" dengan domain Next.js kamu
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Model Data yang diterima
class ClipRequest(BaseModel):
    url: str
    start: str  # Bisa format "00:00:10" atau detik "10"
    end: str    # Bisa format "00:00:20" atau detik "20"

# 3. Fungsi bersih-bersih file temp
def remove_file(path: str):
    try:
        os.remove(path)
        print(f"File dihapus: {path}")
    except Exception as e:
        print(f"Gagal hapus file: {e}")

# 4. Helper Konversi Waktu
def parse_time(time_str):
    try:
        # Coba anggap float/int dulu (detik)
        return float(time_str)
    except ValueError:
        # Kalau gagal, berarti format HH:MM:SS
        parts = list(map(float, time_str.split(':')))
        if len(parts) == 3: return parts[0]*3600 + parts[1]*60 + parts[2]
        if len(parts) == 2: return parts[0]*60 + parts[1]
        return parts[0]

@app.post("/download-clip")
def download_clip(req: ClipRequest, background_tasks: BackgroundTasks):
    unique_name = f"clip_{uuid.uuid4().hex[:8]}.mp4"
    
    try:
        start_sec = parse_time(req.start)
        end_sec = parse_time(req.end)
        durasi = end_sec - start_sec

        # 3. Validasi tambahan untuk melindungi server
        if durasi < 1:
            raise HTTPException(status_code=400, detail="Durasi terlalu pendek")
        if durasi > 180:
            raise HTTPException(status_code=400, detail="Maksimal durasi klip adalah 3 menit (180 detik)")

        print(f"Processing: {req.url} ({start_sec}s - {end_sec}s)")

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'merge_output_format': 'mp4',
            'outtmpl': unique_name,
            'download_ranges': lambda info, ydl_ops: [{"start_time": start_sec, "end_time": end_sec}],
            'force_ipv4': True,
            'noplaylist': True,
            'quiet': True,
            
            # TAMBAHKAN BARIS INI UNTUK MEMBACA COOKIES
            'cookiefile': 'cookies.txt',
            
            'extractor_args': {
                'youtube': {
                    'player_client': ['web']
                }
            }
        }

        # Eksekusi Download
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([req.url])

        if not os.path.exists(unique_name):
            raise HTTPException(status_code=500, detail="Gagal memproses file klip")

        # Kirim file ke user & hapus setelah terkirim (background task)
        background_tasks.add_task(remove_file, unique_name)
        
        return FileResponse(
            path=unique_name, 
            filename="my_clip.mp4", 
            media_type="video/mp4"
        )

    except Exception as e:
        # Bersihkan jika error di tengah jalan
        if os.path.exists(unique_name):
            os.remove(unique_name)
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
# Cara Jalankan: uvicorn main:app --reload