import os, time, urllib.request
from database import db

ENDPOINT = os.getenv('SYNC_ENDPOINT', 'http://web:8080')
TOKEN = os.getenv('SYNC_TOKEN', '')
INTERVAL = max(1, int(os.getenv('SYNC_INTERVAL_MINUTES', '30'))) * 60
SCHEDULE_SETTING_KEY = 'schedule_enabled'

def schedule_enabled():
    c = db()
    row = c.execute('SELECT value FROM app_settings WHERE key=?', (SCHEDULE_SETTING_KEY,)).fetchone()
    c.close()
    if row is None:
        return True
    return str(row['value']).strip().lower() in {'1', 'true', 'yes', 'on'}

def set_schedule_enabled(enabled):
    c = db()
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
              (SCHEDULE_SETTING_KEY, '1' if enabled else '0'))
    c.commit(); c.close()

def heartbeat(detail='idle'):
    c = db()
    c.execute('INSERT INTO service_heartbeat(service,heartbeat,detail) VALUES(?,?,?) ON CONFLICT(service) DO UPDATE SET heartbeat=excluded.heartbeat,detail=excluded.detail',
              ('scheduler', time.time(), detail))
    c.commit(); c.close()

def sync():
    c = db(); rows = c.execute("SELECT spotify_id FROM playlists WHERE enabled=1").fetchall(); c.close()
    for r in rows:
        heartbeat('sync:' + r['spotify_id'])
        try:
            req = urllib.request.Request(f"{ENDPOINT}/api/playlists/{r['spotify_id']}/sync", headers={'X-Sync-Token': TOKEN}, method='POST')
            with urllib.request.urlopen(req, timeout=120) as x: print(x.read().decode(), flush=True)
        except Exception as e:
            print(f"playlist {r['spotify_id']} sync failed: {e}", flush=True)
    heartbeat('idle')

def run_once():
    if not schedule_enabled():
        heartbeat('disabled')
        return False
    heartbeat('starting')
    try: sync()
    except Exception as e:
        print(f'scheduler error: {e}', flush=True); heartbeat('error:' + str(e)[:100])
    return True

def main():
    while True:
        run_once()
        for _ in range(min(INTERVAL, 5)):
            heartbeat('disabled' if not schedule_enabled() else 'waiting')
            time.sleep(1)
        if INTERVAL > 5:
            for _ in range(INTERVAL - 5):
                if not schedule_enabled(): break
                heartbeat('waiting'); time.sleep(1)

if __name__ == '__main__':
    main()
