# Music Downloader for OMV v3

Docker Compose stack for a home OMV server.

## v3 architecture

Spotify is **metadata only**. It is not used as a download source.

```text
Spotify Search / Playlist metadata
             │
             ▼
          Web UI
             │
      user selects a source
             │
             ▼
      YouTube / YouTube Music
             │
             ▼
        yt-dlp worker
             │
             ▼
          /Music on OMV
             │
             ▼
          Jellyfin
```

The custom application owns the web UI, YouTube search, download queue, worker, and playlist scheduler. MeTube is no longer a runtime dependency; its UI/logic is used only as a reference for features and interaction patterns.

## Features

- Spotify OAuth Authorization Code + PKCE
- Private/collaborative playlist metadata sync
- Spotify track search
- YouTube search powered by yt-dlp
- User selects the YouTube source before a download is queued
- SQLite queue/state database
- Progress polling
- Retry failed downloads
- Download history
- Playlist scheduler
- Worker and scheduler heartbeats
- Healthchecks for the three application containers
- No Docker socket access

## Spotify boundary

Spotify API is used for catalog and playlist metadata. The application does not pass Spotify audio URLs to the downloader and does not implement Spotify audio downloading or stream ripping.

Spotify Development Mode currently requires the app owner/developer account to have an active Premium subscription. The application does not check or require Premium for end users; end users still have to satisfy Spotify's current authorization/allowlist rules when using Spotify features. See Spotify's current migration guide for the limits and endpoint changes.

## Download flow

1. Search Spotify by song/artist/album, or sync a Spotify playlist.
2. Select **Find YouTube** for a track.
3. Review the YouTube results.
4. Select **Download audio** for the user-selected YouTube URL.
5. The SQLite queue is processed by the yt-dlp worker.
6. Audio is written to the OMV Music share.

Playlist synchronization creates `pending_source` metadata records. It deliberately does **not** download Spotify tracks automatically.

## OMV setup

1. Create a Music shared folder in OMV.
2. Copy `.env.example` to `.env`.
3. Set `MUSIC_DIR` to the host path of the shared folder.
4. Set Spotify credentials if Spotify search/private playlists are wanted.
5. Set `SPOTIFY_REDIRECT_URI` to the exact HTTPS redirect URI registered in Spotify Developer Dashboard.
6. Generate a long random `SYNC_TOKEN` and keep it only in `.env`.
7. Start:

```bash
docker compose up -d --build
```

Open `http://OMV-IP:8088` for the custom UI.

## Healthchecks

```bash
docker compose ps
docker inspect --format='{{.Name}} {{.State.Health.Status}}' $(docker compose ps -q)
```

The web container checks `/health`; worker and scheduler check SQLite heartbeats.

## Security

- Do not commit `.env`.
- Treat the Spotify OAuth refresh token in `./state` as a credential.
- Do not expose `SYNC_TOKEN` to browser JavaScript.
- Prefer HTTPS for Spotify OAuth callbacks.

## Legal

Only download content you are legally entitled to access or store. The project does not bypass DRM or access controls.


## Optional WireGuard routing

The application uses **one worker container**. WireGuard is enabled or disabled dynamically inside that worker:

- **WireGuard OFF**: YouTube search/download traffic uses the normal Internet route.
- **WireGuard ON**: the same worker brings up `wg0`, and YouTube traffic uses the WireGuard tunnel.

Spotify API traffic remains on the normal web container network.

The UI Settings dialog contains **Use WireGuard for YouTube**. Saving the setting immediately switches the worker route. New downloads store the selected route in the queue, so changing the global setting does not move an already queued job between routes.

### WireGuard setup

1. Copy `wireguard/wg0.conf.example` to `wireguard/wg0.conf`.
2. Put the WireGuard **client** configuration in `wg0.conf`.
3. For a full-tunnel setup, keep `AllowedIPs = 0.0.0.0/0`.
4. Make sure the WireGuard server (for example, an ASUS router) forwards/NATs the client subnet to the Internet.
5. Start the stack:

```bash
docker compose up -d --build
docker compose ps
```

The real `wireguard/wg0.conf` is ignored by Git. The worker needs `NET_ADMIN` (and `SYS_MODULE` when the host kernel module must be loaded) to manage the WireGuard interface.

When the setting is changed while a download is running, the toggle waits for that download to finish before changing the route, preventing a route switch in the middle of a download.

### Real PC WireGuard integration test

Để kiểm tra thực tế trên PC với WireGuard thật, không mock provider:

```bash
export RUN_WIREGUARD_LIVE=1
python -m pytest -q -m wireguard_live --timeout=30
```

Test sẽ:
1. Kiểm tra `wg0.conf` tồn tại.
2. Bật `wg0` bằng `worker.wireguard`.
3. Kiểm tra interface, route, handshake và public IP.
4. Search thật Zing MP3 qua tunnel.
5. Search thật NhacCuaTui qua tunnel.
6. Kiểm tra kết quả chứa URL thật của provider.
7. Nếu WireGuard ban đầu OFF, tắt lại sau test.

Test này không chạy trong CI thông thường vì cần quyền quản trị mạng, `wg-quick` và WireGuard peer thật trên máy chạy test.
