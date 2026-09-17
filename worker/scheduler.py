import os
import sqlite3
import time
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

DB_PATH=os.getenv('DB_PATH','/state/app.db')
CLIENT_ID=os.getenv('SPOTIFY_CLIENT_ID','')
CLIENT_SECRET=os.getenv('SPOTIFY_CLIENT_SECRET','')
INTERVAL=int(os.getenv('SYNC_INTERVAL_MINUTES','30'))*60


def db():
    c=sqlite3.connect(DB_PATH,timeout=30)
    c.row_factory=sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS tracks(
      spotify_id TEXT PRIMARY KEY,title TEXT NOT NULL,artists TEXT NOT NULL,album TEXT,
      spotify_url TEXT,status TEXT NOT NULL DEFAULT 'queued',error TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS playlists(
      spotify_id TEXT PRIMARY KEY,name TEXT NOT NULL,url TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,last_sync TEXT)''')
    c.commit(); return c


def sync():
    if not CLIENT_ID or not CLIENT_SECRET: return
    sp=spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=CLIENT_ID,client_secret=CLIENT_SECRET))
    c=db()
    for p in c.execute('SELECT * FROM playlists WHERE enabled=1').fetchall():
        try:
            offset=0
            while True:
                page=sp.playlist_items(p['spotify_id'],offset=offset,limit=100,
                    fields='items(track(id,name,artists(name),album(name),external_urls)),next')
                for item in page.get('items',[]):
                    t=item.get('track') or {}
                    if not t.get('id') or not t.get('external_urls'): continue
                    artists=', '.join(a['name'] for a in t.get('artists',[]))
                    c.execute('''INSERT INTO tracks(spotify_id,title,artists,album,spotify_url,status)
                      VALUES(?,?,?,?,?,'queued') ON CONFLICT(spotify_id) DO NOTHING''',
                      (t['id'],t['name'],artists,t.get('album',{}).get('name',''),t['external_urls']['spotify']))
                if not page.get('next'): break
                offset += 100
            c.execute("UPDATE playlists SET last_sync=CURRENT_TIMESTAMP WHERE spotify_id=?",(p['spotify_id'],))
            c.commit()
        except Exception as e:
            print(f"playlist {p['spotify_id']} sync failed: {e}",flush=True)
    c.close()

while True:
    try: sync()
    except Exception as e: print(f'scheduler error: {e}',flush=True)
    time.sleep(INTERVAL)
