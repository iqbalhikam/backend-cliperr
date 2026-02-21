FROM python:3.11-slim

# install ffmpeg
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# install python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copy project
COPY . .

# railway inject PORT otomatis
CMD ["python", "worker.py"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]