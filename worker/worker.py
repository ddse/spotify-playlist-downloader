import os, time, traceback, logging
from pathlib import Path

import yt_dlp
import threading
try:
    from .search import serve as serve_search_api
except ImportError:  # pragma: no cover
    from search import serve as serve_search_api
from yt_dlp.utils import DownloadError

try:
    from .jellyfin import refresh_library_sync as jellyfin_refresh_library, update_downloaded_item
except ImportError:  # pragma: no cover
    from jellyfin import refresh_library_sync as jellyfin_refresh_library, update_downloaded_item

try:
    from . import wireguard as manager
    from .providers.zingmp3 import get_stream_url as zing_get_stream_url
    from .providers.nhaccuatui import get_stream_url as nct_get_stream_url
except ImportError:  # pragma: no cover
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


try:
    from .output import describe_recent_media, resolve_downloaded_file
    from .media_validation import CURRENT_VALIDATION_VERSION, validate_media_file, file_fingerprint
    from .media_repair import repair_media_file
    from .progress import clear_download_progress
except ImportError:  # pragma: no cover
    from output import describe_recent_media, resolve_downloaded_file
    from media_validation import CURRENT_VALIDATION_VERSION, validate_media_file, file_fingerprint
    from media_repair import repair_media_file
    from progress import clear_download_progress

MEDIA_MAX_ATTEMPTS = int(os.getenv('MEDIA_MAX_ATTEMPTS', '3'))
MEDIA_REPAIR_MAX_ATTEMPTS = int(os.getenv('MEDIA_REPAIR_MAX_ATTEMPTS', '1'))

def _set_media_state(c, track_id, status, error=None, attempts=None, repair_attempts=None):
    fields = ["media_validation_status=?", "media_validation_error=?", "updated_at=CURRENT_TIMESTAMP"]
    values = [status, error]
    if attempts is not None:
        fields.append("media_validation_attempts=?"); values.append(attempts)
    if repair_attempts is not None:
        fields.append("media_repair_attempts=?"); values.append(repair_attempts)
    values.append(track_id)
    c.execute(f"UPDATE tracks SET {','.join(fields)} WHERE spotify_id=?", values)

def validate_and_record(c, track_id, file_path, media_type):
    _set_media_state(c, track_id, 'checking', None)
    c.commit()
    result = validate_media_file(file_path, media_type)
    row = c.execute("SELECT media_validation_attempts FROM tracks WHERE spotify_id=?", (track_id,)).fetchone()
    attempts = int((row[0] if row else 0) or 0) + 1
    if result.valid:
        size, mtime_ns = file_fingerprint(file_path)
        c.execute("""UPDATE tracks SET media_validation_status='valid',media_validation_at=CURRENT_TIMESTAMP,
            media_validation_version=?,media_validation_error=NULL,media_validation_attempts=?,
            media_validation_size=?,media_validation_mtime_ns=?,media_validation_sha256=NULL,
            updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?""",
            (CURRENT_VALIDATION_VERSION, attempts, size, mtime_ns, track_id))
    else:
        _set_media_state(c, track_id, 'invalid', result.reason, attempts=attempts)
    c.commit()
    return result

def reconcile_media_validation(c):
    rows = c.execute("""SELECT spotify_id,file_path,media_validation_status,media_validation_version,
        media_validation_size,media_validation_mtime_ns,status FROM tracks
        WHERE status='completed' OR media_validation_status IN ('checking','repairing')""").fetchall()
    for row in rows:
        if row['media_validation_status'] in ('checking', 'repairing'):
            _set_media_state(c, row['spotify_id'], 'unchecked', 'worker restarted before validation completed')
            continue
        path = Path(row['file_path'] or '')
        if row['media_validation_status'] == 'valid' and path.is_file():
            try:
                size, mtime_ns = file_fingerprint(path)
                if (int(row['media_validation_version'] or 0) == CURRENT_VALIDATION_VERSION and
                    row['media_validation_size'] is not None and row['media_validation_mtime_ns'] is not None and
                    int(row['media_validation_size']) == size and int(row['media_validation_mtime_ns']) == mtime_ns):
                    continue
            except OSError:
                pass
        _set_media_state(c, row['spotify_id'], 'unchecked', 'media file changed or validation metadata is stale')
    c.commit()

def validate_with_repair(c, track_id, file_path, media_type, output_format):
    result = validate_and_record(c, track_id, file_path, media_type)
    if result.valid:
        return file_path
    if result.infrastructure_error:
        raise RuntimeError(result.reason or 'media validation infrastructure failure')
    for repair_attempt in range(1, MEDIA_REPAIR_MAX_ATTEMPTS + 1):
        _set_media_state(c, track_id, 'repairing', result.reason, repair_attempts=repair_attempt)
        c.commit()
        repair_path = Path(file_path).with_name(Path(file_path).name + '.repair')
        try:
            repair_media_file(file_path, repair_path, media_type=media_type,
                              audio_format=output_format if media_type == 'audio' else None,
                              bitrate=BITRATE)
            repair_result = validate_and_record(c, track_id, repair_path, media_type)
            if repair_result.valid:
                Path(repair_path).replace(file_path)
                final_result = validate_and_record(c, track_id, file_path, media_type)
                if final_result.valid:
                    return file_path
        except Exception as exc:
            logger.warning('media repair failed track=%s attempt=%s error=%s', track_id, repair_attempt, exc)
        finally:
            Path(repair_path).unlink(missing_ok=True)
    raise RuntimeError(f'media validation failed after repair: {result.reason or "unknown media error"}')


def validate_downloaded_output(c, row, track_id, result_path, download_started_at):
    """Resolve, fingerprint and validate a newly downloaded media file before completion."""
    file_path = str(Path(result_path).resolve()) if result_path else resolve_downloaded_file(row, MUSIC_DIR, download_started_at)
    if file_path and not Path(file_path).is_file():
        file_path = resolve_downloaded_file(row, MUSIC_DIR, download_started_at)
    if not file_path:
        recent = describe_recent_media(MUSIC_DIR, download_started_at)
        raise RuntimeError('download completed but output media file was not found; recent_media=' + recent)
    file_path = rename_download_to_current_title(c, track_id, file_path)
    media_type = 'video' if (row['download_type'] or 'audio') == 'video' else 'audio'
    output_format = row['download_format'] or ('mp4' if media_type == 'video' else FMT)
    logger.info('media validation start track=%s path=%s type=%s', track_id, file_path, media_type)
    validate_with_repair(c, track_id, file_path, media_type, output_format)
    row_after = c.execute('SELECT media_validation_status FROM tracks WHERE spotify_id=?', (track_id,)).fetchone()
    if not row_after or row_after['media_validation_status'] != 'valid':
        raise RuntimeError('downloaded media validation did not produce valid state')
    logger.info('media validation passed track=%s path=%s', track_id, file_path)
    return file_path


def format_eta(seconds):
    if seconds is None or seconds < 0:
        return ''
    seconds = int(seconds)
    if seconds < 60:
        return f'{seconds}s'
    return f'{seconds // 60}:{seconds % 60:02d}'



def jellyfin_config(c):
    row = c.execute("SELECT enabled,config_json FROM provider_connections WHERE provider='jellyfin'").fetchone()
    if not row or not row['enabled']:
        return None
    try:
        import json
        cfg = json.loads(row['config_json'] or '{}')
    except Exception:
        return None
    return cfg if cfg.get('server_url') and cfg.get('api_token') else None


def jellyfin_media_path(file_path):
    source = Path(file_path).resolve()
    music_root = Path(MUSIC_DIR).resolve()
    prefix = os.getenv('JELLYFIN_MEDIA_PREFIX', '/media').strip().rstrip('/') or '/media'
    try:
        relative = source.relative_to(music_root)
    except ValueError:
        return str(source)
    return f"{prefix}/{relative.as_posix()}"


def notify_jellyfin(c, track_id, file_path):
    cfg = jellyfin_config(c)
    if not cfg:
        return
    c.execute("UPDATE tracks SET jellyfin_status='syncing',jellyfin_error='',jellyfin_updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?", (track_id,))
    c.commit()
    row = c.execute("SELECT title,artists,album FROM tracks WHERE spotify_id=?", (track_id,)).fetchone()
    try:
        jellyfin_refresh_library(cfg['server_url'], cfg['api_token'])
        item_id = update_downloaded_item(cfg['server_url'], cfg['api_token'], jellyfin_media_path(file_path), row['title'], row['artists'], row['album'], track_id)
        c.execute("UPDATE tracks SET jellyfin_status='synced',jellyfin_item_id=?,jellyfin_error='',jellyfin_updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?", (item_id, track_id))
        c.commit()
    except Exception as exc:
        c.execute("UPDATE tracks SET jellyfin_status='failed',jellyfin_error=?,jellyfin_updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?", (str(exc)[-4000:], track_id))
        c.commit()
        logger.warning('Jellyfin metadata sync failed track=%s error=%s', track_id, exc)

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


def rename_download_to_current_title(c, track_id, file_path):
    """Apply a title edited while downloading to the completed media filename."""
    if not file_path:
        return file_path
    source = Path(file_path).resolve()
    music = Path(MUSIC_DIR).resolve()
    try:
        source.relative_to(music)
    except ValueError:
        logger.warning('skip title rename outside MUSIC_DIR track=%s path=%s', track_id, source)
        return str(source)
    if not source.is_file():
        return str(source)
    row = c.execute('SELECT title FROM tracks WHERE spotify_id=?', (track_id,)).fetchone()
    if not row:
        return str(source)
    title = _safe_filename_component(row['title'])
    destination = source.with_name(title + source.suffix)
    if destination == source:
        return str(source)
    if destination.exists():
        logger.warning('skip title rename because destination exists track=%s destination=%s', track_id, destination)
        return str(source)
    source.rename(destination)
    logger.info('renamed completed media track=%s from=%s to=%s', track_id, source, destination)
    return str(destination)

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
        # Googlevideo connections can stall while downloading large video
        # streams. Use explicit network resilience instead of relying on
        # yt-dlp defaults: longer socket timeout, retries, and small HTTP
        # ranges so a stalled connection does not discard the whole transfer.
        'socket_timeout': 60,
        'retries': 10,
        'fragment_retries': 10,
        'file_access_retries': 3,
        'retry_sleep_functions': {
            'http': lambda n: min(30, 2 ** max(0, n - 1)),
            'fragment': lambda n: min(30, 2 ** max(0, n - 1)),
            'file_access': lambda n: min(10, 2 ** max(0, n - 1)),
        },
        'http_chunk_size': 10 * 1024 * 1024,
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

        # Video downloads must always require a video stream. Never fall back
        # to the generic "best" selector because YouTube may expose an
        # audio-only MP4 as the best single format. The final file must contain
        # a video stream, with audio added when available.
        if download_format == 'ios':
            vsel = f"bestvideo[vcodec~='^(avc|h264)']{height}"
            fallback_vsel = f'bestvideo{height}'
            opts['format'] = f'{vsel}+bestaudio/{fallback_vsel}+bestaudio/{fallback_vsel}'
        elif download_format == 'mp4':
            vsel = f'bestvideo[ext=mp4]{height}'
            fallback_vsel = f'bestvideo{height}'
            opts['format'] = f'{vsel}+bestaudio[ext=m4a]/{vsel}+bestaudio/{fallback_vsel}+bestaudio/{fallback_vsel}'
        else:
            vsel = f'bestvideo{height}'
            opts['format'] = f'{vsel}+bestaudio/{vsel}'
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
                reconcile_media_validation(c)

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
                    last_validation_error = None
                    file_path = None
                    for attempt in range(1, MEDIA_MAX_ATTEMPTS + 1):
                        try:
                            _set_media_state(c, track_id, 'unchecked', None)
                            c.commit()
                            result_path = manager.run_download(
                                use_wireguard,
                                lambda: download(row, c, track_id, download_started_at),
                            )
                            file_path = validate_downloaded_output(c, row, track_id, result_path, download_started_at)
                            break
                        except Exception as attempt_error:
                            last_validation_error = attempt_error
                            logger.warning('download/validation attempt failed track=%s attempt=%s/%s error=%s',
                                           track_id, attempt, MEDIA_MAX_ATTEMPTS, attempt_error)
                            if attempt < MEDIA_MAX_ATTEMPTS:
                                _set_media_state(c, track_id, 'unchecked', str(attempt_error))
                                c.commit()
                                continue
                            raise last_validation_error
                    c.execute(
                        "UPDATE tracks SET status='completed',progress=100,error=NULL,file_path=?,download_speed='',eta='',updated_at=CURRENT_TIMESTAMP WHERE spotify_id=? AND media_validation_status='valid'",
                        (file_path, track_id),
                    )
                    if c.execute("SELECT changes()").fetchone()[0] != 1:
                        raise RuntimeError('media validation did not complete successfully')
                    notify_jellyfin(c, track_id, file_path)
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
