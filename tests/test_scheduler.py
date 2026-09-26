import importlib
import sqlite3
import sys

def load_scheduler(tmp_path):
    import database
    database.DB_PATH = str(tmp_path / 'scheduler.db')
    sys.modules.pop('worker.scheduler', None)
    return importlib.import_module('worker.scheduler')

def seed_playlist(tmp_path):
    import database
    database.DB_PATH = str(tmp_path / 'scheduler.db')
    c = database.db()
    c.execute("INSERT INTO playlists(spotify_id,name,url,enabled) VALUES(?,?,?,1)",
              ('pl-1', 'Test playlist', 'https://open.spotify.com/playlist/pl-1'))
    c.commit(); c.close()

def test_schedule_defaults_enabled(tmp_path):
    scheduler = load_scheduler(tmp_path)
    assert scheduler.schedule_enabled() is True

def test_schedule_setting_persists_off_and_on(tmp_path):
    scheduler = load_scheduler(tmp_path)
    scheduler.set_schedule_enabled(False)
    assert scheduler.schedule_enabled() is False
    scheduler.set_schedule_enabled(True)
    assert scheduler.schedule_enabled() is True

def test_run_once_skips_all_subscription_sync_when_disabled(tmp_path, monkeypatch):
    scheduler = load_scheduler(tmp_path)
    seed_playlist(tmp_path)
    scheduler.set_schedule_enabled(False)
    called = []
    monkeypatch.setattr(scheduler, 'sync', lambda: called.append(True))
    assert scheduler.run_once() is False
    assert called == []
    c = sqlite3.connect(str(tmp_path / 'scheduler.db'))
    detail = c.execute("SELECT detail FROM service_heartbeat WHERE service='scheduler'").fetchone()[0]
    c.close()
    assert detail == 'disabled'

def test_run_once_syncs_enabled_subscriptions_when_enabled(tmp_path, monkeypatch):
    scheduler = load_scheduler(tmp_path)
    seed_playlist(tmp_path)
    called = []
    monkeypatch.setattr(scheduler, 'sync', lambda: called.append(True))
    assert scheduler.run_once() is True
    assert called == [True]

def test_sync_only_processes_enabled_subscriptions(tmp_path, monkeypatch):
    scheduler = load_scheduler(tmp_path)
    seed_playlist(tmp_path)
    import database
    c = database.db()
    c.execute("INSERT INTO playlists(spotify_id,name,url,enabled) VALUES(?,?,?,0)",
              ('off-1', 'Disabled', 'https://open.spotify.com/playlist/off-1'))
    c.commit(); c.close()
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"ok":true}'
    def fake_urlopen(req, timeout):
        requests.append((req.full_url, req.get_header('X-sync-token')))
        return Response()
    monkeypatch.setattr(scheduler.urllib.request, 'urlopen', fake_urlopen)
    scheduler.sync()
    assert requests == [('http://web:8080/api/playlists/pl-1/sync', '')]
