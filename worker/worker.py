import os
import sqlite3
import subprocess
import time
from pathlib import Path

DB_PATH=os.getenv('DB_PATH','/state/app.db')
MUSIC_DIR=os.getenv('MUSIC_DIR','/music')
FMT=os.getenv('AUDIO_FORMAT','mp3')
BITRATE=os.getenv('AUDIO_BITRATE','320k')


def conn():
    c=sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory=sqlite3.Row
    return c


def init(c):
    c.execute('''CREATE TABLE IF NOT EXISTS tracks(
      spotify_id TEXT PRIMARY KEY,title TEXT NOT NULL,artists TEXT NOT NULL,album TEXT,
      spotify_url TEXT,status TEXT NOT NULL DEFAULT 'queued',error TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.commit()


def run(row):
    Path(MUSIC_DIR).mkdir(parents=True,exist_ok=True)
    # spotDL uses Spotify metadata and searches a source service for the audio.
    cmd=['spotdl','download',row['spotify_url'],
         '--output',f'{MUSIC_DIR}/{{artist}}/{{album}}/{{track-number}} - {{title}}.{{output-ext}}',
         '--format',FMT]
    if FMT in ('mp3','m4a','opus','flac'):
        cmd += ['--bitrate',BITRATE]
    return subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=3600)


while True:
    try:
        c=conn(); init(c)
        row=c.execute("SELECT * FROM tracks WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            c.close(); time.sleep(5); continue
        c.execute("UPDATE tracks SET status='downloading',updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",(row['spotify_id'],)); c.commit()
        try:
            p=run(row)
            if p.returncode==0:
                c.execute("UPDATE tracks SET status='completed',error=NULL,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",(row['spotify_id'],))
            else:
                c.execute("UPDATE tracks SET status='failed',error=?,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",(p.stdout[-4000:],row['spotify_id']))
        except Exception as e:
            c.execute("UPDATE tracks SET status='failed',error=?,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",(str(e),row['spotify_id']))
        c.commit(); c.close()
    except Exception:
        time.sleep(10)
