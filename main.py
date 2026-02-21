import os
import uuid
import json
import logging
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from redis_client import redis_client

# =========================
# LOGGING SETUP
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [API] %(message)s"
)

app = FastAPI()

DOWNLOAD_DIR = "/tmp/downloads"
COOKIE_DIR = "/tmp/cookies"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(COOKIE_DIR, exist_ok=True)

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
# ENDPOINTS
# =========================
@app.post("/download")
async def start_download(
    url: str = Form(...),
    start: str = Form(...),
    end: str = Form(...),
    cookie: UploadFile | None = File(default=None)
):
    # 1. Validasi Durasi di awal (Cegah beban server sebelum masuk Redis)
    try:
        start_sec = parse_time(start)
        end_sec = parse_time(end)
        
        if end_sec <= start_sec:
            raise HTTPException(status_code=400, detail="Waktu 'end' harus lebih besar dari 'start'")
        
        # Opsional: Batasi maksimal durasi klip (misal 10 menit / 600 detik)
        if (end_sec - start_sec) > 600:
            raise HTTPException(status_code=400, detail="Maksimal durasi klip adalah 10 menit")
            
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=400, detail="Format waktu tidak valid")

    # 2. Persiapan Job
    job_id = str(uuid.uuid4())
    cookie_path = None

    if cookie:
        cookie_path = f"{COOKIE_DIR}/{job_id}.txt"
        with open(cookie_path, "wb") as f:
            f.write(await cookie.read())

    job = {
        "id": job_id,
        "url": url,
        "start": start,
        "end": end,
        "cookie": cookie_path
    }

    # 3. Lempar ke Antrean Redis
    redis_client.rpush("download_queue", json.dumps(job))
    logging.info(f"Job {job_id} berhasil masuk antrean Redis")

    return {"job_id": job_id, "status": "queued"}


@app.get("/status/{job_id}")
def status(job_id: str):
    res = redis_client.get(f"job:{job_id}")

    if not res:
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