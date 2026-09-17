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

MeTube remains available as a separate browser-facing downloader for direct/manual downloads.

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
- Healthchecks for all four containers
- No Docker socket access

## Important Spotify boundary

Spotify API is used for catalog/playlist metadata. The application does not pass Spotify audio URLs to the downloader and does not implement Spotify audio downloading or stream ripping.

For Development Mode, Spotify requires the app owner/developer account to satisfy Spotify's current requirements. End users are not required to upgrade their account merely to use this application's non-playback metadata workflow, subject to Spotify's current app/user authorization rules.

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
4. Set `METUBE_PUBLIC_URL` to the OMV LAN address, for example `http://192.168.10.20:8081`.
5. Set Spotify credentials if Spotify search/private playlists are wanted.
6. Set `SPOTIFY_REDIRECT_URI` to the exact HTTPS redirect URI registered in Spotify Developer Dashboard.
7. Generate a long random `SYNC_TOKEN` and keep it only in `.env`.
8. Start:

```bash
docker compose up -d --build
```

Open `http://OMV-IP:8088` for the custom UI and `http://OMV-IP:8081` for MeTube.

## Healthchecks

```bash
docker compose ps
docker inspect --format='{{.Name}} {{.State.Health.Status}}' $(docker compose ps -q)
```

The web container checks `/health`; worker and scheduler check SQLite heartbeats. MeTube is checked through its HTTP root.

## Security

- Do not commit `.env`.
- Treat the Spotify OAuth refresh token in `./state` as a credential.
- Do not expose `SYNC_TOKEN` to browser JavaScript.
- Prefer HTTPS for Spotify OAuth callbacks.

## Legal

Only download content you are legally entitled to access or store. The project does not bypass DRM or access controls.
