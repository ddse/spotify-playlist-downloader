import os, time, traceback, logging
from pathlib import Path

import yt_dlp
import threading
from search import serve as serve_search_api
from yt_dlp.utils import DownloadError

import wireguard as manager
from providers.zingmp3 import get_stream_url as zing_get_stream_url
from providers.nhaccuatui import get_stream_url as nct_get_stream_url

import re
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

DEBUG_MODE = os.getenv('DEBUG', '0').lower() in {'1', 'true', 'yes', 'on', 'debug'}
LOG_LEVEL = 'DEBUG' if DEBUG_MODE else os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format='%(asctime)s %(levelname)s [worker] %(message)s')
logger = logging.getLogger('worker')
logger.info('Worker logging initialized debug=%s log_level=%s', DEBUG_MODE, LOG_LEVEL)

def normalize_youtube_url(url, source_mode='single'):
    """Normalize a YouTube watch URL for single-item downloads.

    YouTube copy/share links often contain playlist/radio parameters such as
    ``list`` and ``start_radio``. When the UI queues a single item, those
    parameters are not part of the selected video and can make extraction
    ambiguous. Keep the video id (and an optional timestamp) only.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        if source_mode != 'single' or host not in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}:
            return url
        if host == 'youtu.be':
            video_id = parsed.path.lstrip('/').split('/')[0]
        else:
            video_id = (parse_qs(parsed.query).get('v') or [''])[0]
        if not video_id:
            return url
        original = parse_qs(parsed.query)
        query = {'v': [video_id]}
        for key in ('t', 'start'):
            if original.get(key):
                query[key] = [original[key][0]]
        return urlunparse(('https', 'www.youtube.com', '/watch', '', urlencode(query, doseq=True), ''))
    except Exception:
        return url

def is_zingmp3(url):
    try:
        host = (urlparse(url).hostname or '').lower()
        return host == 'zingmp3.vn' or host.endswith('.zingmp3.vn')
    except Exception:
        return False


def is_nhaccuatui(url):
    try:
        host = (urlparse(url).hostname or '').lower()
        return host == 'nhaccuatui.com' or host.endswith('.nhaccuatui.com')
    except Exception:
        return False

DB_PATH = os.getenv('DB_PATH', '/state/app.db')
MUSIC_DIR = os.getenv('MUSIC_DIR', '/music')
FMT = os.getenv('AUDIO_FORMAT', 'mp3')
BITRATE = os.getenv('AUDIO_BITRATE', '320K')
YOUTUBE_PLAYER_CLIENTS = [x.strip() for x in os.getenv('YOUTUBE_PLAYER_CLIENTS', 'default,web_embedded').split(',') if x.strip()]


class DownloadDebugError(RuntimeError):
    def __init__(self, message, logs=''):
        super().__init__(message)
        self.logs = logs


def conn():
    from database import db
    return db()


def init(c):
    from database import init_db
    init_db(c)

def format_speed(value):
    value = float(value or 0)
    if value <= 0:
        return ''
    units = ('B/s', 'KB/s', 'MB/s', 'GB/s')
    i = 0
    while value >= 1024 and i < len(units) - 1:
        value /= 1024
        i += 1
    return f'{value:.1f} {units[i]}'


from output import describe_recent_media, resolve_downloaded_file
from progress import clear_download_progress

def format_eta(seconds):
    if seconds is None or seconds < 0:
        return ''
    seconds = int(seconds)
    if seconds < 60:
        return f'{seconds}s'
    return f'{seconds // 60}:{seconds % 60:02d}'


def heartbeat(c, detail='idle'):
    c.execute(
        '''INSERT INTO service_heartbeat(service,heartbeat,detail)
           VALUES(?,?,?)
           ON CONFLICT(service) DO UPDATE SET heartbeat=excluded.heartbeat,detail=excluded.detail''',
        ('worker', time.time(), detail),
    )
    c.commit()


def download_nhaccuatui(row, c, track_id):
    logger.info('NCT download start track=%s source=%s', track_id, row['source_url'])
    stream_url = nct_get_stream_url(row['source_url'])
    stream_host = urlparse(stream_url).hostname or ''
    logger.debug('NCT signed stream resolved track=%s host=%s', track_id, stream_host)

    Path(MUSIC_DIR).mkdir(parents=True, exist_ok=True)
    artist = (row['artists'] or 'NhacCuaTui').replace('/', '_')
    album = (row['album'] or 'NhacCuaTui').replace('/', '_')
    title = (row['title'] or 'Unknown Title').replace('/', '_')
    custom_folder = (row['download_folder'] or '').strip()
    folder = Path(MUSIC_DIR) / custom_folder if custom_folder else Path(MUSIC_DIR) / artist / album
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f'{title}.mp3'
    temp = output.with_suffix('.mp3.part')

    req = urllib.request.Request(
        stream_url,
        headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': row['source_url'],
            'Accept': 'audio/mpeg,audio/*;q=0.9,*/*;q=0.5',
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as response, open(temp, 'wb') as fp:
            status = getattr(response, 'status', None)
            content_type = (response.headers.get('Content-Type') or '').lower()
            total = int(response.headers.get('Content-Length') or 0)
            if status != 200:
                raise RuntimeError(f'NhacCuaTui stream HTTP {status}')

            downloaded = 0
            last_update = 0.0
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                fp.write(chunk)
                downloaded += len(chunk)
                now = time.time()
                if now - last_update >= 1:
                    percent = int(downloaded * 100 / total) if total else 1
                    c.execute(
                        'UPDATE tracks SET progress=?,downloaded_bytes=?,total_bytes=?,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?',
                        (max(1, min(99, percent)), downloaded, total, track_id),
                    )
                    c.commit()
                    heartbeat(c, 'downloading:' + track_id)
                    last_update = now

        if downloaded < 10240:
            raise RuntimeError(
                f'NhacCuaTui audio response is unexpectedly small ({downloaded} bytes)'
            )

        with open(temp, 'rb') as fp:
            header = fp.read(16)

        if not (
            header.startswith(b'ID3')
            or header[:2] in (b'\\xff\\xfb', b'\\xff\\xf3', b'\\xff\\xf2')
        ):
            raise RuntimeError(
                'NhacCuaTui response does not look like MP3 audio: '
                f'content_type={content_type!r}, header={header[:16]!r}'
            )

        temp.replace(output)
        logger.info(
            'NCT download complete track=%s bytes=%d output=%s',
            track_id, downloaded, output
        )
        return str(output.resolve())
    except Exception:
        logger.exception('NCT download failed track=%s', track_id)
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass
        raise
def download_zingmp3(row, c, track_id):
    Path(MUSIC_DIR).mkdir(parents=True, exist_ok=True)
    artist = (row['artists'] or 'Zing MP3').replace('/', '_')
    album = (row['album'] or 'Zing MP3').replace('/', '_')
    title = (row['title'] or 'Unknown Title').replace('/', '_')
    custom_folder = (row['download_folder'] or '').strip()
    folder = Path(MUSIC_DIR) / custom_folder if custom_folder else Path(MUSIC_DIR) / artist / album
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f'{title}.mp3'

    zing_debug = []
    wg_before = manager.debug_status()
    zing_debug.append({
        'step': 'wireguard_before_zing_download',
        'status': wg_before.get('status'),
        'detail': wg_before,
    })
    try:
        stream_url = zing_get_stream_url(row['source_url'], debug=zing_debug)
        zing_debug.append({
            'step': 'zing_stream_url',
            'status': 'ok',
            'url_host': urlparse(stream_url).hostname or '',
        })
    except Exception as exc:
        zing_debug.append({
            'step': 'zing_stream_url',
            'status': 'error',
            'error': f'{type(exc).__name__}: {exc}',
        })
        raise RuntimeError(
            'Zing MP3 download diagnostics:\\n' +
            '\\n'.join(str(step) for step in zing_debug) +
            '\\nOriginal error: ' + f'{type(exc).__name__}: {exc}'
        ) from exc

    wg_after_api = manager.debug_status()
    zing_debug.append({
        'step': 'wireguard_after_zing_api',
        'status': wg_after_api.get('status'),
        'detail': wg_after_api,
    })

    req = urllib.request.Request(stream_url, headers={
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://zingmp3.vn/',
    })
    with urllib.request.urlopen(req, timeout=60) as response, open(output, 'wb') as fp:
        zing_debug.append({
            'step': 'zing_stream_download',
            'status': 'http_ok',
            'http_status': getattr(response, 'status', None),
            'content_type': response.headers.get('Content-Type', ''),
            'content_length': response.headers.get('Content-Length', ''),
        })
        total = int(response.headers.get('Content-Length') or 0)
        downloaded = 0
        last_update = 0.0
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            fp.write(chunk)
            downloaded += len(chunk)
            now = time.time()
            if now - last_update >= 1:
                percent = int(downloaded * 100 / total) if total else 1
                c.execute(
                    'UPDATE tracks SET progress=?,downloaded_bytes=?,total_bytes=?,download_speed=?,eta=?,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?',
                    (max(1, min(99, percent)), downloaded, total, format_speed(
                        downloaded / max(now - last_update, 1)
                    ), '', track_id),
                )
                c.commit()
                heartbeat(c, 'downloading:' + track_id)
                last_update = now

    wg_after_download = manager.debug_status()
    zing_debug.append({
        'step': 'wireguard_after_zing_download',
        'status': wg_after_download.get('status'),
        'detail': wg_after_download,
        'downloaded_bytes': downloaded,
    })

    if downloaded < 10240:
        raise RuntimeError(
            'Zing MP3 download is unexpectedly small. Diagnostics: ' +
            '\\n'.join(str(step) for step in zing_debug)
        )
    with open(output, 'rb') as fp:
        header = fp.read(12)
    if not (header.startswith(b'ID3') or header[:2] in (b'\xff\xfb', b'\xff\xf3', b'\xff\xf2')):
        raise RuntimeError('Zing MP3 download does not look like MP3 audio')
    return str(output.resolve())


def _safe_filename_component(value, fallback='Unknown Title'):
    value = re.sub(r'[\x00-\x1f\x7f]+', ' ', str(value or '')).strip()
    value = re.sub(r'[\\\\/:*?"<>|]+', '_', value)
    value = re.sub(r'\s+', ' ', value).strip(' .')
    return (value or fallback)[:200]


def resolve_direct_link_metadata(row, c, track_id, url):
    """Use yt-dlp metadata for raw links so filenames are based on media title."""
    if (row['source_type'] or '') != 'url' or int(row['title_override'] or 0):
        return row['title'], row['artists'], row['album']
    try:
        opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'noplaylist': True,
            'extract_flat': False,
            'js_runtimes': {'deno': {'path': '/usr/local/bin/deno'}},
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        title = info.get('track') or info.get('title') or row['title'] or 'Unknown Title'
        artists = info.get('artist') or info.get('uploader') or info.get('channel') or row['artists'] or 'Unknown Artist'
        album = info.get('album') or row['album'] or 'YouTube'
        title = _safe_filename_component(title)
        artists = _safe_filename_component(artists, 'Unknown Artist')
        album = _safe_filename_component(album, 'YouTube')
        c.execute(
            'UPDATE tracks SET title=?,artists=?,album=?,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?',
            (title, artists, album, track_id),
        )
        c.commit()
        logger.info('direct-link metadata resolved track=%s title=%r artist=%r album=%r', track_id, title, artists, album)
        return title, artists, album
    except Exception as exc:
        logger.warning('direct-link metadata lookup failed track=%s url=%s error=%s', track_id, url, exc)
        return row['title'], row['artists'], row['album']


def download(row, c, track_id, download_started_at=0):
    Path(MUSIC_DIR).mkdir(parents=True, exist_ok=True)
    source_mode = row['source_mode'] or 'single'
    url = normalize_youtube_url(row['source_url'], source_mode)
    if is_zingmp3(url):
        return download_zingmp3(row, c, track_id)
    if is_nhaccuatui(url):
        return download_nhaccuatui(row, c, track_id)
    if not url:
        raise RuntimeError('No download source selected')

    title, artists, album = resolve_direct_link_metadata(row, c, track_id, url)
    artist = _safe_filename_component(artists, 'Unknown Artist')
    album = _safe_filename_component(album, 'YouTube')
    title = _safe_filename_component(title)
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
    last_stats_update = 0.0

    def progress_hook(data):
        nonlocal last_progress, last_heartbeat, last_stats_update
        now = time.time()
        status = data.get('status')

        if status == 'downloading':
            total = data.get('total_bytes') or data.get('total_bytes_estimate')
            downloaded = data.get('downloaded_bytes', 0)
            percent = int(downloaded * 100 / total) if total else 1
            percent = max(1, min(99, percent))
            speed = data.get('speed') or 0
            eta_seconds = data.get('eta')
            if percent != last_progress or now - last_stats_update >= 1:
                c.execute(
                    'UPDATE tracks SET progress=?,downloaded_bytes=?,total_bytes=?,download_speed=?,eta=?,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?',
                    (percent, downloaded, int(total or 0), format_speed(speed), format_eta(eta_seconds), track_id),
                )
                c.commit()
                last_progress = percent
                last_stats_update = now

            if now - last_heartbeat >= 5:
                heartbeat(c, 'downloading:' + track_id)
                last_heartbeat = now

        elif status == 'finished':
            clear_download_progress(c, track_id)
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
        # YouTube is actively changing the default player clients. Keep the
        # normal client first, but add web_embedded as a non-PO-token fallback
        # for videos whose default client only exposes SABR/blocked formats.
        'extractor_args': {
            'youtube': {
                'player_client': YOUTUBE_PLAYER_CLIENTS,
            },
        },
        'js_runtimes': {'deno': {'path': '/usr/local/bin/deno'}},
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

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.download([url])
    except Exception as exc:
        raise DownloadDebugError(
            f'{type(exc).__name__}: {exc}',
            '\\n'.join(log_lines),
        ) from exc

    if result not in (None, 0):
        raise DownloadDebugError(
            f'yt-dlp exited with code {result}',
            '\\n'.join(log_lines),
        )


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


def run_worker():
    threading.Thread(target=serve_search_api, name='search-api', daemon=True).start()

    while True:
            c = None
            try:
                c = conn()
                init(c)
                heartbeat(c)

                row = c.execute(
                    "SELECT * FROM tracks WHERE status='queued' AND source_url IS NOT NULL ORDER BY priority DESC, created_at LIMIT 1"
                ).fetchone()

                if not row:
                    c.close()
                    time.sleep(3)
                    continue

                track_id = row['spotify_id']
                logger.info('download job picked track=%s source=%s title=%r', track_id, row['source_url'], row['title'])
                c.execute(
                    "UPDATE tracks SET status='downloading',progress=1,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",
                    (track_id,),
                )
                c.commit()
                heartbeat(c, 'starting:' + track_id)

                use_wireguard = bool(row['wireguard'])
                download_started_at = time.time()
                try:
                    result_path = manager.run_download(
                        use_wireguard,
                        lambda: download(row, c, track_id, download_started_at),
                    )
                    file_path = str(Path(result_path).resolve()) if result_path else resolve_downloaded_file(row, MUSIC_DIR, download_started_at)
                    if file_path and not Path(file_path).is_file():
                        logger.warning('download returned missing path track=%s path=%s; falling back to scan', track_id, file_path)
                        file_path = resolve_downloaded_file(row, MUSIC_DIR, download_started_at)
                    logger.info('download output resolved track=%s path=%s', track_id, file_path or '<missing>')
                    if not file_path:
                        recent = describe_recent_media(MUSIC_DIR, download_started_at)
                        logger.error(
                            'download returned success but no media output was found track=%s recent_media=%s',
                            track_id, recent,
                        )
                        raise RuntimeError(
                            'download completed but output media file was not found; '
                            f'recent_media={recent}'
                        )
                    c.execute(
                        "UPDATE tracks SET status='completed',progress=100,error=NULL,file_path=?,download_speed='',eta='',updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",
                        (file_path, track_id),
                    )
                except Exception as e:
                    logger.exception('download job failed track=%s', track_id)
                    err = format_error(e, getattr(e, 'logs', ''))
                    c.execute(
                        "UPDATE tracks SET status='failed',progress=0,error=?,download_speed='',eta='',updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",
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



if __name__ == "__main__":
    run_worker()
