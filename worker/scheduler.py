import os, sqlite3, time, urllib.request
DB_PATH=os.getenv('DB_PATH','/state/app.db'); ENDPOINT=os.getenv('SYNC_ENDPOINT','http://web:8080'); TOKEN=os.getenv('SYNC_TOKEN',''); INTERVAL=max(1,int(os.getenv('SYNC_INTERVAL_MINUTES','30')))*60

def db():
    c=sqlite3.connect(DB_PATH,timeout=30); c.row_factory=sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS service_heartbeat(service TEXT PRIMARY KEY,heartbeat REAL NOT NULL,detail TEXT)'); c.commit(); return c

def heartbeat(detail='idle'):
    c=db(); c.execute('INSERT INTO service_heartbeat(service,heartbeat,detail) VALUES(?,?,?) ON CONFLICT(service) DO UPDATE SET heartbeat=excluded.heartbeat,detail=excluded.detail',('scheduler',time.time(),detail)); c.commit(); c.close()

def sync():
    c=db(); rows=c.execute("SELECT spotify_id FROM playlists WHERE enabled=1").fetchall(); c.close()
    for r in rows:
        heartbeat('sync:'+r['spotify_id'])
        try:
            req=urllib.request.Request(f"{ENDPOINT}/api/playlists/{r['spotify_id']}/sync",headers={'X-Sync-Token':TOKEN},method='POST')
            with urllib.request.urlopen(req,timeout=120) as x: print(x.read().decode(),flush=True)
        except Exception as e: print(f"playlist {r['spotify_id']} sync failed: {e}",flush=True)
    heartbeat('idle')

while True:
    try: heartbeat('starting'); sync()
    except Exception as e: print(f'scheduler error: {e}',flush=True); heartbeat('error:'+str(e)[:100])
    for _ in range(INTERVAL): heartbeat('waiting'); time.sleep(1)
