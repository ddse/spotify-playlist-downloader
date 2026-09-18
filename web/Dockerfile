FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY package.json ./
RUN npm install
COPY vite.config.js ./
COPY frontend ./frontend
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt yt-dlp
COPY app.py spotify.py youtube.py ./
COPY --from=frontend /frontend/dist ./static
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8080"]
