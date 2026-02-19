# Gunakan Python 3.9
FROM python:3.9-slim

# 1. Install FFmpeg & Update System
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# 2. Setup User & Permission (Wajib di Hugging Face)
# HF menjalankan aplikasi sebagai user ID 1000, bukan root.
# Kita harus buat user itu dan kasih izin tulis folder.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR $HOME/app

# 3. Copy file requirements & install
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy sisa codingan (main.py)
COPY --chown=user . .

# 5. Jalankan Aplikasi di Port 7860 (Port Keramat HF)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]