# Spotify Playlist Downloader for OMV v2

Docker Compose stack for a home OMV server.

## Features

- Spotify track search
- Spotify OAuth Authorization Code + PKCE
- Private/collaborative playlist access for the connected Spotify account
- SQLite download queue and state
- Automatic playlist sync scheduler
- spotDL worker
- Realtime queue/progress UI with retry for failed jobs
- Artist/album/track folder layout under the OMV Music share
- MeTube for direct YouTube/yt-dlp downloads
- No Docker socket access

## Architecture

`Browser → FastAPI → Spotify OAuth/API → SQLite → spotDL worker → /music`

`Scheduler → FastAPI authenticated playlist sync → SQLite → worker`

`Browser → MeTube → /music`

## Spotify setup

1. Create an application in the Spotify Developer Dashboard.
2. Copy the Client ID and Client Secret to `.env`.
3. Set `SPOTIFY_REDIRECT_URI` to an address reachable by the browser, for example `http://192.168.10.20:8088/api/spotify/callback`.
4. Register that exact URI in the Spotify application settings.
5. Start the stack.
6. Open the web UI and click **Spotify Login / Connect**.
7. Authorize playlist scopes requested by the application.

The OAuth refresh token is stored in the SQLite state database mounted at `./state`. Keep that directory private and back it up securely.

## OMV setup

1. Create an OMV Music shared folder.
2. Copy `.env.example` to `.env`.
3. Set `MUSIC_DIR` to the host path of that shared folder.
4. Set Spotify credentials and the exact redirect URI.
5. Start:

```bash
docker compose up -d --build
```

Open `http://OMV-IP:8088` for the custom UI and `http://OMV-IP:8081` for MeTube.

## Queue states

`queued → downloading → completed`

Failures become `failed` and can be retried from the UI without creating a duplicate Spotify track record.

## Legal / source behavior

Spotify provides metadata and source URLs to spotDL; this project does not bypass DRM or access controls. Use the stack only for content you are legally entitled to download or store.
