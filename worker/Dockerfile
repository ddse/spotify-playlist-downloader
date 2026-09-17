FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg unzip \
    && rm -rf /var/lib/apt/lists/*

# Current yt-dlp YouTube extraction needs an external JavaScript runtime.
RUN curl -fsSL https://deno.land/install.sh | sh
ENV PATH="/root/.deno/bin:${PATH}"

# Install the EJS companion package together with yt-dlp.
RUN pip install --no-cache-dir "yt-dlp[default]"

WORKDIR /app
COPY worker.py scheduler.py .
CMD ["python", "worker.py"]
