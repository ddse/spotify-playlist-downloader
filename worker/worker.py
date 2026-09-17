import os, re, sqlite3, subprocess, time
from pathlib import Path

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
    c.commit()


def heartbeat(c, detail='idle'):
    c.execute(
        '''INSERT INTO service_heartbeat(service,heartbeat,detail)
           VALUES(?,?,?)
           ON CONFLICT(service) DO UPDATE SET heartbeat=excluded.heartbeat,detail=excluded.detail''',
        ('worker', time.time(), detail),
    )
    c.commit()


def run(row):
    Path(MUSIC_DIR).mkdir(parents=True, exist_ok=True)
    url = row['source_url']
    if not url:
        raise RuntimeError('No download source selected')

    # Keep the user's Spotify metadata in the filename when available.
    artist = (row['artists'] or 'Unknown Artist').replace('/', '_')
    album = (row['album'] or 'YouTube').replace('/', '_')
    title = (row['title'] or 'Unknown Title').replace('/', '_')
    folder = Path(MUSIC_DIR) / artist / album
    folder.mkdir(parents=True, exist_ok=True)
    output = str(folder / f'{title}.%(ext)s')

    cmd = [
        'yt-dlp',
        '--newline',
        '--no-part',
        '--no-warnings',
        '--progress',
        '--progress-delta', '2',
        '--extractor-args', 'youtube:player_client=web',
        '-x',
        '--audio-format', FMT,
        '--audio-quality', BITRATE,
        '--embed-metadata',
        '--force-overwrites',
        '-o', output,
        url,
    ]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def pct(line):
    m = re.search(r'(\d{1,3}(?:\.\d+)?)%', line)
    return max(0, min(100, int(float(m.group(1))))) if m else None


while True:
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
            p = run(row)
            output = []
            last_hb = time.time()
            while True:
                line = p.stdout.readline()
                if line:
                    output.append(line)
                    n = pct(line)
                    if n is not None:
                        c.execute(
                            'UPDATE tracks SET progress=?,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?',
                            (n, track_id),
                        )
                        c.commit()
                    heartbeat(c, 'downloading:' + track_id)
                elif p.poll() is not None:
                    break
                else:
                    time.sleep(0.1)

                if time.time() - last_hb >= 5:
                    heartbeat(c, 'downloading:' + track_id)
                    last_hb = time.time()

            rc = p.wait(timeout=30)
            if rc == 0:
                c.execute(
                    "UPDATE tracks SET status='completed',progress=100,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",
                    (track_id,),
                )
            else:
                err = ''.join(output)[-6000:] or f'yt-dlp exited with code {rc}'
                c.execute(
                    "UPDATE tracks SET status='failed',error=?,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",
                    (err, track_id),
                )
        except Exception as e:
            c.execute(
                "UPDATE tracks SET status='failed',error=?,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",
                (str(e), track_id),
            )

        c.commit()
        heartbeat(c, 'idle')
        c.close()
    except Exception:
        time.sleep(10)
