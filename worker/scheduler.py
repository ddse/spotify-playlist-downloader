import os, sqlite3, time, urllib.request
DB_PATH=os.getenv('DB_PATH','/state/app.db'); ENDPOINT=os.getenv('SYNC_ENDPOINT','http://web:8080'); INTERVAL=max(1,int(os.getenv('SYNC_INTERVAL_MINUTES','30')))*60

def sync():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row
    rows=c.execute("SELECT spotify_id FROM playlists WHERE enabled=1").fetchall(); c.close()
    for r in rows:
        try:
            req=urllib.request.Request(f"{ENDPOINT}/api/playlists/{r['spotify_id']}/sync",method='POST')
            with urllib.request.urlopen(req,timeout=120) as x: print(x.read().decode(),flush=True)
        except Exception as e: print(f"playlist {r['spotify_id']} sync failed: {e}",flush=True)

while True:
    try: sync()
    except Exception as e: print(f'scheduler error: {e}',flush=True)
    time.sleep(INTERVAL)
