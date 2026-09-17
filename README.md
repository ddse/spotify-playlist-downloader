# Spotify Playlist Downloader for OMV

Docker Compose stack for a home OMV server.

## Features

- Spotify track search UI
- SQLite-backed download queue
- spotDL worker for Spotify URLs
- Artist/album/track folder layout under the OMV Music share
- MeTube for direct YouTube/yt-dlp downloads
- Playlist registration UI
- No Docker socket access

## Architecture

`Browser → FastAPI UI → SQLite queue → spotDL worker → /music`

`Browser → MeTube → /music`

## OMV setup

1. Create a Music shared folder in OMV.
2. Copy `.env.example` to `.env`.
3. Set `MUSIC_DIR` to the host path of the shared folder.
4. Set `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`.
5. Configure the Spotify redirect URI to match `SPOTIFY_REDIRECT_URI`.
6. Start:

```bash
docker compose up -d --build
```

Open `http://OMV-IP:8088` for the custom UI and `http://OMV-IP:8081` for MeTube.

## Notes

Spotify catalog search uses the Client Credentials flow. The first version keeps Spotify user/private-playlist authorization out of the default configuration; public playlist access should be used until OAuth user authorization is added.

Only download audio/video that you are legally entitled to access or store. This project does not bypass DRM or access controls.
