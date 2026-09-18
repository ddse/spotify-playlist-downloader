import os, time, urllib.request
from database import db

ENDPOINT = os.getenv('SYNC_ENDPOINT', 'http://web:8080')
TOKEN = os.getenv('SYNC_TOKEN', '')
INTERVAL = max(1, int(os.getenv('SYNC_INTERVAL_MINUTES', '30'))) * 60

def heartbeat(detail='idle'):
    c = db()
    c.execute('INSERT INTO service_heartbeat(service,heartbeat,detail) VALUES(?,?,?) ON CONFLICT(service) DO UPDATE SET heartbeat=excluded.heartbeat,detail=excluded.detail', ('scheduler', time.time(), detail))
    c.commit(); c.close()

def sync():
    c = db(); rows = c.execute("SELECT spotify_id FROM playlists WHERE enabled=1").fetchall(); c.close()
    for r in rows:
        heartbeat('sync:' + r['spotify_id'])
        try:
            req = urllib.request.Request(f"{ENDPOINT}/api/playlists/{r['spotify_id']}/sync", headers={'X-Sync-Token': TOKEN}, method='POST')
            with urllib.request.urlopen(req, timeout=120) as x: print(x.read().decode(), flush=True)
        except Exception as e: print(f"playlist {r['spotify_id']} sync failed: {e}", flush=True)
    heartbeat('idle')

while True:
    try: heartbeat('starting'); sync()
    except Exception as e: print(f'scheduler error: {e}', flush=True); heartbeat('error:' + str(e)[:100])
    for _ in range(INTERVAL): heartbeat('waiting'); time.sleep(1)
