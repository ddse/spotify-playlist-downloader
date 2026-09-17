FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt yt-dlp
COPY app.py spotify.py youtube.py .
COPY templates ./templates
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
