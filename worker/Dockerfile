FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir spotdl==4.4.3 spotipy==2.25.1
WORKDIR /app
COPY worker.py scheduler.py .
CMD ["python", "worker.py"]
