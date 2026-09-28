"""Jellyfin REST client used by the downloader.

The client prepares downloaded MP3 metadata before requesting a library refresh,
then waits for Jellyfin to index the file.  File-path matching is tolerant of
Docker/NAS mount-path differences and falls back to an exact filename match.
"""
import subprocess
import time
from pathlib import Path
from urllib.parse import quote, urljoin

import httpx


class JellyfinError(RuntimeError):
    pass


def _base_url(server_url: str) -> str:
    value = str(server_url or "").strip()
    if not value:
        raise ValueError("Jellyfin server URL is required")
    if not value.startswith(("http://", "https://")):
        value = "http://" + value
    return value.rstrip("/") + "/"


def _headers(token: str) -> dict:
    token = str(token or "").strip()
    if not token:
        raise ValueError("Jellyfin API token is required")
    return {
        "X-Emby-Authorization": (
            'MediaBrowser Client="Music Downloader", '
            'Device="Music Downloader", DeviceId="music-downloader", '
            'Version="1.0", Token="' + token.replace('"', "") + '"'
        ),
        "Accept": "application/json",
    }


def _write_mp3_metadata(file_path: str, title: str, artists: str, album: str) -> None:
    """Write stable ID3 metadata so Jellyfin can identify the track reliably."""
    path = Path(file_path)
    if path.suffix.lower() != ".mp3" or not path.is_file():
        return

    temp = path.with_name(path.name + ".jellyfin.tmp.mp3")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(path),
        "-map",
        "0",
        "-c",
        "copy",
        "-id3v2_version",
        "3",
        "-write_id3v1",
        "1",
        "-metadata",
        f"title={title or path.stem}",
        "-metadata",
        f"artist={artists or 'Unknown Artist'}",
        "-metadata",
        f"album_artist={artists or 'Unknown Artist'}",
        "-metadata",
        f"album={album or 'Unknown Album'}",
        str(temp),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
        temp.replace(path)
    except (OSError, subprocess.SubprocessError) as exc:
        temp.unlink(missing_ok=True)
        raise JellyfinError(f"MP3 metadata update failed: {exc}") from exc


async def test_connection(server_url: str, token: str) -> dict:
    """Validate the configured server and token without mutating the library."""
    base = _base_url(server_url)
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        response = await client.get(urljoin(base, "System/Info"), headers=_headers(token))
    if response.status_code >= 400:
        raise JellyfinError(f"Jellyfin connection failed: HTTP {response.status_code}")
    payload = response.json()
    return {
        "server_name": payload.get("ServerName") or payload.get("ProductName") or "Jellyfin",
        "version": payload.get("Version", ""),
        "id": payload.get("Id", ""),
    }


def refresh_library(server_or_connection, token: str = ""):
    """Refresh Jellyfin, with backward-compatible settings-database support."""
    if hasattr(server_or_connection, "execute"):
        rows = server_or_connection.execute(
            "SELECT key, value FROM app_settings WHERE key IN "
            "('jellyfin_enabled','jellyfin_url','jellyfin_api_key')"
        ).fetchall()
        settings = {row["key"]: row["value"] for row in rows}
        enabled = str(settings.get("jellyfin_enabled", "0")).lower() in {
            "1", "true", "yes", "on"
        }
        server_url = (settings.get("jellyfin_url") or "").strip()
        token = (settings.get("jellyfin_api_key") or "").strip()
        if not enabled or not server_url or not token:
            return {"ok": False, "skipped": True, "reason": "not_configured"}
        try:
            refresh_library_sync(server_url, token)
            return {"ok": True, "skipped": False, "status_code": 200}
        except Exception as exc:
            return {"ok": False, "skipped": False, "reason": str(exc)}

    refresh_library_sync(server_or_connection, token)
    return {"ok": True, "skipped": False, "status_code": 200}


async def refresh_library_async(server_url: str, token: str) -> None:
    base = _base_url(server_url)
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        response = await client.post(
            urljoin(base, "Library/Refresh"),
            headers=_headers(token),
        )
    if response.status_code >= 400:
        raise JellyfinError(f"Jellyfin library refresh failed: HTTP {response.status_code}")


def refresh_library_sync(server_url: str, token: str) -> None:
    base = _base_url(server_url)
    response = httpx.post(
        urljoin(base, "Library/Refresh"),
        headers=_headers(token),
        timeout=10,
    )
    if response.status_code >= 400:
        raise JellyfinError(f"Jellyfin library refresh failed: HTTP {response.status_code}")


def _find_item(client, base: str, headers: dict, file_path: str, title: str):
    """Find an indexed audio item without assuming identical container paths."""
    wanted_path = str(Path(file_path)).rstrip("/").casefold()
    wanted_name = Path(file_path).name.casefold()
    start_index = 0

    for _ in range(10):
        response = client.get(
            urljoin(base, "Items"),
            params={
                "Recursive": "true",
                "IncludeItemTypes": "Audio",
                "StartIndex": start_index,
                "Limit": 1000,
                "Fields": "Path,ProviderIds,Name,Album,Artists,AlbumArtist",
                "SearchTerm": title or Path(file_path).stem,
            },
            headers=headers,
        )
        if response.status_code >= 400:
            raise JellyfinError(
                f"Jellyfin item lookup failed: HTTP {response.status_code}"
            )

        payload = response.json()
        items = payload.get("Items", [])
        exact = next(
            (
                item for item in items
                if str(item.get("Path", "")).rstrip("/").casefold() == wanted_path
            ),
            None,
        )
        if exact:
            return exact

        same_name = [
            item for item in items
            if Path(str(item.get("Path", ""))).name.casefold() == wanted_name
        ]
        if len(same_name) == 1:
            return same_name[0]

        total = int(payload.get("TotalRecordCount") or len(items))
        start_index += len(items)
        if not items or start_index >= total:
            break

    return None


def update_downloaded_item(
    server_url: str,
    token: str,
    file_path: str,
    title: str,
    artists: str,
    album: str,
    provider_id: str = "",
) -> str:
    """Tag the MP3, wait for Jellyfin indexing, then update the indexed item."""
    _write_mp3_metadata(file_path, title, artists, album)

    base = _base_url(server_url)
    headers = _headers(token)
    with httpx.Client(timeout=10, follow_redirects=True) as client:
        for attempt in range(15):
            item = _find_item(client, base, headers, file_path, title)
            if item:
                item_id = item.get("Id")
                if not item_id:
                    raise JellyfinError("Jellyfin returned an item without an Id")

                metadata = {
                    "Name": title,
                    "Album": album or None,
                    "AlbumArtist": artists or None,
                    "Artists": [artists] if artists else [],
                }
                if provider_id:
                    metadata["ProviderIds"] = {
                        **(item.get("ProviderIds") or {}),
                        "MusicDownloader": provider_id,
                    }

                # Jellyfin's ItemUpdate API updates an item with POST /Items/{id}.
                update = client.post(
                    urljoin(base, f"Items/{quote(str(item_id), safe='')}"),
                    json=metadata,
                    headers={**headers, "Content-Type": "application/json"},
                )
                if update.status_code >= 400:
                    raise JellyfinError(
                        f"Jellyfin metadata update failed: HTTP {update.status_code}"
                    )
                return str(item_id)

            if attempt < 14:
                time.sleep(1)

    raise JellyfinError(
        f"Jellyfin did not index the downloaded audio after refresh: {file_path}"
    )
