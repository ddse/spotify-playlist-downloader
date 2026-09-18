# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend-build
WORKDIR /app
COPY web/package.json ./
RUN npm install
COPY web/frontend ./frontend
COPY web/vite.config.js ./
RUN npm run build
FROM python:3.13-slim AS python-build
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
RUN python -m venv "$VIRTUAL_ENV"
COPY web/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt
FROM python:3.13-slim AS runtime
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=python-build /opt/venv /opt/venv
COPY --from=frontend-build /app/dist ./static
COPY web/app.py web/spotify.py web/youtube.py database.py ./
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8080"]
