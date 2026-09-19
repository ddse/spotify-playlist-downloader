# syntax=docker/dockerfile:1
FROM python:3.13-slim AS builder
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://deno.land/install.sh | sh
RUN pip install --no-cache-dir --target /opt/python-packages "yt-dlp[default]" requests==2.32.5
FROM python:3.13-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg wireguard-tools iproute2 procps kmod iptables \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /root/.deno/bin/deno /usr/local/bin/deno
COPY --from=builder /opt/python-packages /usr/local/lib/python3.13/site-packages
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY worker/worker.py worker/scheduler.py worker/search.py worker/wireguard.py database.py ./
COPY worker/providers ./providers
CMD ["python", "worker.py"]
