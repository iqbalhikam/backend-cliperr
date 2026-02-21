# =========================
# BASE IMAGE
# =========================
FROM python:3.10-slim

# =========================
# INSTALL SYSTEM DEPENDENCY
# =========================
RUN apt-get update && \
    apt-get install -y ffmpeg curl && \
    rm -rf /var/lib/apt/lists/*

# =========================
# CREATE USER (SECURE)
# =========================
RUN useradd -m -u 1000 appuser
USER appuser

ENV HOME=/home/appuser
WORKDIR $HOME/app

# =========================
# INSTALL PYTHON DEP
# =========================
COPY --chown=appuser requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# =========================
# COPY PROJECT
# =========================
COPY --chown=appuser . .

# =========================
# PORT (Railway pakai $PORT)
# =========================
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}