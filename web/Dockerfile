# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend-build

WORKDIR /app

COPY package.json ./
RUN npm install

COPY frontend ./frontend
COPY vite.config.js ./
RUN npm run build


FROM python:3.13-slim AS python-build

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN python -m venv "$VIRTUAL_ENV"

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


FROM python:3.13-slim AS runtime

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=python-build /opt/venv /opt/venv
COPY --from=frontend-build /app/dist ./static

COPY app.py spotify.py youtube.py ./

CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8080"]
