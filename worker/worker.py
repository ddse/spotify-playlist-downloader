import os, sqlite3, time, traceback
from pathlib import Path

import yt_dlp
from yt_dlp.utils import DownloadError

DB_PATH = os.getenv('DB_PATH', '/state/app.db')
MUSIC_DIR = os.getenv('MUSIC_DIR', '/music')
FMT = os.getenv('AUDIO_FORMAT', 'mp3')
BITRATE = os.getenv('AUDIO_BITRATE', '320K')


def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init(c):
    c.execute('''CREATE TABLE IF NOT EXISTS tracks(
        spotify_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        artists TEXT NOT NULL,
        album TEXT,
        status TEXT NOT NULL DEFAULT 'pending_source',
        progress INTEGER DEFAULT 0,
        error TEXT,
        source_type TEXT DEFAULT 'youtube',
        source_url TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS service_heartbeat(
        service TEXT PRIMARY KEY, heartbeat REAL NOT NULL, detail TEXT
    )''')
    cols = {r[1] for r in c.execute('PRAGMA table_info(tracks)').fetchall()}
    if 'source_type' not in cols:
        c.execute("ALTER TABLE tracks ADD COLUMN source_type TEXT DEFAULT 'spotify'")
    if 'source_url' not in cols:
        c.execute("ALTER TABLE tracks ADD COLUMN source_url TEXT")
    if 'progress' not in cols:
        c.execute("ALTER TABLE tracks ADD COLUMN progress INTEGER DEFAULT 0")

    c.execute("""
        UPDATE tracks
        SET status='queued', progress=0, error=NULL, updated_at=CURRENT_TIMESTAMP
        WHERE status='downloading' AND source_url IS NOT NULL
    """)
    c.commit()


def heartbeat(c, detail='idle'):
    c.execute(
        '''INSERT INTO service_heartbeat(service,heartbeat,detail)
           VALUES(?,?,?)
           ON CONFLICT(service) DO UPDATE SET heartbeat=excluded.heartbeat,detail=excluded.detail''',
        ('worker', time.time(), detail),
    )
    c.commit()


def download(row, c, track_id):
    Path(MUSIC_DIR).mkdir(parents=True, exist_ok=True)
    url = row['source_url']
    if not url:
        raise RuntimeError('No download source selected')

    artist = (row['artists'] or 'Unknown Artist').replace('/', '_')
    album = (row['album'] or 'YouTube').replace('/', '_')
    title = (row['title'] or 'Unknown Title').replace('/', '_')
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

    download_type = row['source_type'] or 'audio'
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
        # Do not force bestaudio/best here. YouTube can expose different
        # format sets depending on the player client; yt-dlp's own default
        # selector has the correct fallback behavior.
        'outtmpl': output,
        'noplaylist': True,
        'quiet': False,
        'no_warnings': False,
        'logger': YTDLPLogger(),
        'verbose': True,
        'progress_hooks': [progress_hook],
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': FMT,
            'preferredquality': BITRATE,
        }],
        'writethumbnail': False,
        'embedmetadata': True,
        'overwrites': True,
    }

    if download_type == 'audio':
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': FMT,
            'preferredquality': BITRATE,
        }]
    else:
        opts['format'] = 'bestvideo+bestaudio/best'
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


while True:
    c = None
    try:
        c = conn()
        init(c)
        heartbeat(c)

        row = c.execute(
            "SELECT * FROM tracks WHERE status='queued' AND source_url IS NOT NULL ORDER BY created_at LIMIT 1"
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
