import importlib
import sys
from fastapi.testclient import TestClient

def load_app(tmp_path):
    import database
    database.DB_PATH = str(tmp_path / 'web.db')
    sys.modules.pop('web.app', None)
    return importlib.import_module('web.app')

def test_schedule_setting_api_and_subscription_manual_sync(tmp_path):
    app_module = load_app(tmp_path)
    async def fake_sync(playlist_id):
        return {'ok': True, 'playlist_id': playlist_id, 'added': 1, 'total': 1}
    app_module._sync_playlist = fake_sync
    with TestClient(app_module.app) as client:
        assert client.get('/api/settings/schedule').json() == {'enabled': True}
        invalid = client.put('/api/settings/schedule', json={'enabled': 'false'})
        assert invalid.status_code == 400
        response = client.put('/api/settings/schedule', json={'enabled': False})
        assert response.status_code == 200
        assert response.json() == {'ok': True, 'enabled': False}
        assert client.get('/api/settings/schedule').json() == {'enabled': False}
        db = app_module.db()
        db.execute("INSERT INTO playlists(spotify_id,name,url,enabled) VALUES(?,?,?,1)",
                   ('pl-1', 'Test playlist', 'https://open.spotify.com/playlist/pl-1'))
        db.commit(); db.close()
        response = client.post('/api/playlists/pl-1/sync-manual')
        assert response.status_code == 200
        assert response.json()['ok'] is True
        response = client.put('/api/settings/schedule', json={'enabled': True})
        assert response.status_code == 200
        assert client.get('/api/services').json()['scheduler']['enabled'] is True

def test_subscription_toggle_remains_independent_from_global_schedule(tmp_path):
    app_module = load_app(tmp_path)
    with TestClient(app_module.app) as client:
        db = app_module.db()
        db.execute("INSERT INTO playlists(spotify_id,name,url,enabled) VALUES(?,?,?,1)",
                   ('pl-1', 'Test playlist', 'https://open.spotify.com/playlist/pl-1'))
        db.commit(); db.close()
        client.put('/api/settings/schedule', json={'enabled': False})
        response = client.post('/api/playlists/pl-1/toggle')
        assert response.status_code == 200
        assert response.json() == {'ok': True, 'enabled': False}
        assert client.get('/api/playlists').json()['items'][0]['enabled'] == 0
        client.post('/api/playlists/pl-1/toggle')
        assert client.get('/api/settings/schedule').json()['enabled'] is False
