import os, time, traceback
from pathlib import Path

import yt_dlp
import threading
from search import serve as serve_search_api
from yt_dlp.utils import DownloadError

DB_PATH = os.getenv('DB_PATH', '/state/app.db')
MUSIC_DIR = os.getenv('MUSIC_DIR', '/music')
FMT = os.getenv('AUDIO_FORMAT', 'mp3')
BITRATE = os.getenv('AUDIO_BITRATE', '320K')
SERVICE_NAME = os.getenv('WORKER_SERVICE_NAME', 'worker')
ROUTE_WIREGUARD = os.getenv('ROUTE_WIREGUARD', '0') in {'1','true','yes','on'}


def conn():
    from database import db
    return db()


def init(c):
    from database import init_db
    init_db(c)

def heartbeat(c, detail='idle'):
    c.execute(
        '''INSERT INTO service_heartbeat(service,heartbeat,detail)
           VALUES(?,?,?)
           ON CONFLICT(service) DO UPDATE SET heartbeat=excluded.heartbeat,detail=excluded.detail''',
        (SERVICE_NAME, time.time(), detail),
    )
    c.commit()


def download(row, c, track_id):
    Path(MUSIC_DIR).mkdir(parents=True, exist_ok=True)
    url = row['source_url']
    source_mode = row['source_mode'] or 'single'
    if not url:
        raise RuntimeError('No download source selected')

    artist = (row['artists'] or 'Unknown Artist').replace('/', '_')
    album = (row['album'] or 'YouTube').replace('/', '_')
    title = (row['title'] or 'Unknown Title').replace('/', '_')
    custom_folder = (row['download_folder'] or '').strip()
    if custom_folder:
        safe = Path(custom_folder)
        folder = Path(MUSIC_DIR) / safe
    else:
        folder = Path(MUSIC_DIR) / artist / album
    folder.mkdir(parents=True, exist_ok=True)
    output = str(folder / f'{title}.%(ext)s')

    last_progress = -1
    last_heartbeat = 0.0

    def progress_hook(data):
        nonlocal last_progress, last_heartbeat
        now = time.time()
        status = data.get('status')

        if status == 'downloading':
            total = data.get('total_bytes') or data.get('total_bytes_estimate')
            downloaded = data.get('downloaded_bytes', 0)
            percent = int(downloaded * 100 / total) if total else 1
            percent = max(1, min(99, percent))
            if percent != last_progress:
                c.execute(
                    'UPDATE tracks SET progress=?,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?',
                    (percent, track_id),
                )
                c.commit()
                last_progress = percent

            if now - last_heartbeat >= 5:
                heartbeat(c, 'downloading:' + track_id)
                last_heartbeat = now

        elif status == 'finished':
            c.execute(
                'UPDATE tracks SET progress=99,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?',
                (track_id,),
            )
            c.commit()
            heartbeat(c, 'postprocessing:' + track_id)

    download_type = row['download_type'] or row['source_type'] or 'audio'
    thumbnail = bool(row['thumbnail'])
    subtitle = bool(row['subtitle'])
    subtitle_lang = row['subtitle_lang'] or 'ja,en'
    subtitle_mode = row['subtitle_mode'] or 'prefer_manual'
    split_chapters = bool(row['split_chapters'])
    download_format = row['download_format'] or ('mp3' if download_type == 'audio' else 'any')
    download_quality = row['download_quality'] or 'best'
    video_codec = row['video_codec'] or 'auto'
    log_lines = []

    class YTDLPLogger:
        def debug(self, msg):
            if msg.startswith('[debug] '):
                log_lines.append(msg)
        def info(self, msg):
            log_lines.append(msg)
        def warning(self, msg):
            log_lines.append('[warning] ' + msg)
        def error(self, msg):
            log_lines.append('[error] ' + msg)

    opts = {
        'outtmpl': output,
        'noplaylist': source_mode == 'single',
        'quiet': False,
        'no_warnings': False,
        'logger': YTDLPLogger(),
        'verbose': True,
        'progress_hooks': [progress_hook],
        'overwrites': True,
        'embedmetadata': True,
        'writethumbnail': thumbnail,
        'writesubtitles': subtitle,
        'writeautomaticsub': subtitle and subtitle_mode in {'auto_only','prefer_auto'},
        'subtitleslangs': subtitle_lang.split(','),
        'embedchapters': not split_chapters,
        'ignoreerrors': False,
    }

    if download_type == 'audio':
        # Do not require the source stream itself to already be mp3/m4a/etc.
        # YouTube commonly serves webm/mp4 audio and ffmpeg converts it later.
        audio_format = download_format if download_format in {'m4a','mp3','opus','wav','flac'} else 'mp3'
        audio_quality = download_quality if download_quality in {'0','128','192','256','320','best'} else '320'
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': audio_format,
            'preferredquality': 0 if audio_quality == 'best' else audio_quality,
        }]
        if audio_format != 'wav' and thumbnail:
            opts['postprocessors'] += [
                {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg', 'when': 'before_dl'},
                {'key': 'FFmpegMetadata'},
                {'key': 'EmbedThumbnail'},
            ]
    else:
        quality = download_quality if download_quality in {'best','2160','1440','1080','720','480','360'} else 'best'
        height = '' if quality == 'best' else f'[height<={quality}]'

        # Keep the selector permissive and let yt-dlp choose formats actually
        # exposed by the current YouTube player client. Strict ext/codec filters
        # can produce "Requested format is not available" even when usable
        # formats exist.
        if download_format == 'ios':
            vsel = f"bestvideo[vcodec~='^(avc|h264)']{height}"
            fallback_vsel = f"bestvideo{height}"
            opts['format'] = f'{vsel}+bestaudio/{fallback_vsel}+bestaudio/best{height}'
        elif download_format == 'mp4':
            vsel = f'bestvideo[ext=mp4]{height}'
            opts['format'] = f'{vsel}+bestaudio[ext=m4a]/{vsel}+bestaudio/bestvideo{height}+bestaudio/best{height}'
        else:
            opts['format'] = f'bestvideo{height}+bestaudio/best{height}'
        opts['merge_output_format'] = 'mp4'

    with yt_dlp.YoutubeDL(opts) as ydl:
        result = ydl.download([url])

    if result not in (None, 0):
        raise RuntimeError(f'yt-dlp exited with code {result}')


def format_error(exc, logger_text=''):
    parts = []
    if logger_text.strip():
        parts.append(logger_text.strip())
    if isinstance(exc, DownloadError):
        parts.append('yt-dlp DownloadError: ' + str(exc))
        if getattr(exc, 'exc_info', None):
            parts.append(''.join(traceback.format_exception(*exc.exc_info)).strip())
    else:
        parts.append(type(exc).__name__ + ': ' + str(exc))
        parts.append(traceback.format_exc().strip())
    return '\n\n'.join(p for p in parts if p)[-20000:]


threading.Thread(target=serve_search_api, name='search-api', daemon=True).start()

while True:
    c = None
    try:
        c = conn()
        init(c)
        heartbeat(c)

        row = c.execute(
            "SELECT * FROM tracks WHERE status='queued' AND source_url IS NOT NULL AND COALESCE(wireguard,0)=? ORDER BY created_at LIMIT 1",
            (1 if ROUTE_WIREGUARD else 0,)
        ).fetchone()

        if not row:
            c.close()
            time.sleep(3)
            continue

        track_id = row['spotify_id']
        c.execute(
            "UPDATE tracks SET status='downloading',progress=1,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",
            (track_id,),
        )
        c.commit()
        heartbeat(c, 'starting:' + track_id)

        try:
            download(row, c, track_id)
            c.execute(
                "UPDATE tracks SET status='completed',progress=100,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",
                (track_id,),
            )
        except Exception as e:
            err = format_error(e, '\n'.join(log_lines) if 'log_lines' in locals() else '')
            c.execute(
                "UPDATE tracks SET status='failed',progress=0,error=?,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",
                (err, track_id),
            )
            heartbeat(c, 'failed:' + track_id)

        c.commit()
        heartbeat(c, 'idle')
        c.close()
    except Exception:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
        time.sleep(10)
