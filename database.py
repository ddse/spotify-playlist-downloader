import os
import sqlite3

DB_PATH = os.getenv('DB_PATH', '/state/app.db')

TRACK_BASE_COLUMNS = {
    'spotify_url': 'TEXT',
    'source_type': "TEXT DEFAULT 'youtube'",
    'source_url': 'TEXT',
    'progress': 'INTEGER DEFAULT 0',
    'download_type': "TEXT DEFAULT 'audio'",
    'download_format': "TEXT DEFAULT 'mp3'",
    'download_quality': "TEXT DEFAULT 'best'",
    'video_codec': "TEXT DEFAULT 'auto'",
    'download_folder': "TEXT DEFAULT ''",
    'thumbnail': 'INTEGER DEFAULT 1',
    'subtitle': 'INTEGER DEFAULT 0',
    'subtitle_lang': "TEXT DEFAULT 'ja,en'",
    'subtitle_mode': "TEXT DEFAULT 'prefer_manual'",
    'split_chapters': 'INTEGER DEFAULT 0',
    'auto_start': 'INTEGER DEFAULT 1',
    'priority': 'INTEGER DEFAULT 0',
    'source_mode': "TEXT DEFAULT 'single'",
    'playlist_item_limit': 'INTEGER DEFAULT 0',
    'downloaded_bytes': 'INTEGER DEFAULT 0',
    'total_bytes': 'INTEGER DEFAULT 0',
    'download_speed': "TEXT DEFAULT ''",
    'eta': "TEXT DEFAULT ''",
}


def connect():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_db(c=None):
    own = c is None
    if own:
        c = connect()
    c.execute('''CREATE TABLE IF NOT EXISTS tracks(
        spotify_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        artists TEXT NOT NULL,
        album TEXT,
        spotify_url TEXT,
        status TEXT NOT NULL DEFAULT 'pending_source',
        progress INTEGER DEFAULT 0,
        error TEXT,
        source_type TEXT DEFAULT 'youtube',
        source_url TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        download_type TEXT DEFAULT 'audio',
        download_format TEXT DEFAULT 'mp3',
        download_quality TEXT DEFAULT 'best',
        video_codec TEXT DEFAULT 'auto',
        download_folder TEXT DEFAULT '',
        thumbnail INTEGER DEFAULT 1,
        subtitle INTEGER DEFAULT 0,
        subtitle_lang TEXT DEFAULT 'ja,en',
        subtitle_mode TEXT DEFAULT 'prefer_manual',
        split_chapters INTEGER DEFAULT 0,
        auto_start INTEGER DEFAULT 1,
        priority INTEGER DEFAULT 0,
        source_mode TEXT DEFAULT 'single',
        playlist_item_limit INTEGER DEFAULT 0,
        downloaded_bytes INTEGER DEFAULT 0,
        total_bytes INTEGER DEFAULT 0,
        download_speed TEXT DEFAULT '',
        eta TEXT DEFAULT ''
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS playlists(
        spotify_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_sync TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS service_heartbeat(
        service TEXT PRIMARY KEY,
        heartbeat REAL NOT NULL,
        detail TEXT
    )''')
    cols = {r[1] for r in c.execute('PRAGMA table_info(tracks)').fetchall()}
    for name, definition in TRACK_BASE_COLUMNS.items():
        if name not in cols:
            c.execute(f'ALTER TABLE tracks ADD COLUMN {name} {definition}')
    c.commit()
    if own:
        c.close()


def db():
    c = connect()
    init_db(c)
    return c
