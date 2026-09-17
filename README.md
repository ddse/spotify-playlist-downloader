# Spotify Playlist Downloader for OMV

Docker Compose stack for a home OMV server.

## v2 features

- Spotify OAuth Authorization Code + PKCE
- Private and collaborative Spotify playlist access
- Spotify track search UI
- SQLite-backed download queue
- Realtime progress polling
- Retry failed downloads
- Delete queued/failed items
- Download history
- Playlist management: add, sync, enable/disable, delete
- Worker and scheduler status indicators
- spotDL worker for Spotify URLs
- Artist/album/track folder layout under the OMV Music share
- MeTube for direct YouTube/yt-dlp downloads
- Healthchecks for all four containers
- No Docker socket access

## Architecture

`Browser → FastAPI UI → SQLite queue → spotDL worker → /music`

`Browser → FastAPI UI → MeTube /add API → yt-dlp → /music`

`Scheduler → FastAPI private sync endpoint → Spotify OAuth → SQLite queue`

## MeTube API

The stack uses the official MeTube `/add` JSON API. The current MeTube source validates a request containing at least `url`, `download_type`, `codec`, `format`, and `quality`; this project sends those fields explicitly and sets `auto_start=true`.

Example payload:

```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "download_type": "audio",
  "codec": "auto",
  "format": "mp3",
  "quality": "best",
  "auto_start": true
}
```

The Compose file currently uses `ghcr.io/alexta69/metube:latest`, so the image is intentionally floating rather than pinned to a release. The `/add` contract was checked against the current upstream source. If reproducible deployments are required, pin the image to a tested MeTube release or digest.

The browser-facing MeTube URL is configured separately with `METUBE_PUBLIC_URL`; `METUBE_URL` remains the Docker-internal address used by FastAPI.

## OMV setup

1. Create a Music shared folder in OMV.
2. Copy `.env.example` to `.env`.
3. Set `MUSIC_DIR` to the host path of the shared folder.
4. Set `METUBE_PUBLIC_URL` to the OMV LAN address, for example `http://192.168.10.20:8081`.
5. Set `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`.
6. Configure the Spotify redirect URI to exactly match `SPOTIFY_REDIRECT_URI`.
7. Generate a long random `SYNC_TOKEN`.
8. Start:

```bash
docker compose up -d --build
```

Open `http://OMV-IP:8088` for the custom UI and `http://OMV-IP:8081` for MeTube.

## Healthchecks

Check container health:

```bash
docker compose ps
docker inspect --format='{{.Name}} {{.State.Health.Status}}' $(docker compose ps -q)
```

The web container checks `/health`; MeTube checks its HTTP root; worker and scheduler check their SQLite heartbeat. Worker/scheduler heartbeats are refreshed continuously, including while a download or playlist sync is active.

## Spotify OAuth

Use **Spotify Login / Connect** in the UI before adding a private playlist. The refresh token is stored in the local SQLite state database and is not committed to Git.

Only download audio/video that you are legally entitled to access or store. This project does not bypass DRM or access controls.
