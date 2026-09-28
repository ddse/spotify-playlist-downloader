"""Small Jellyfin REST client used by the downloader.

The integration deliberately stores only the Jellyfin access token and never
returns it to the UI.  The token is sent using Jellyfin's current
X-Emby-Authorization header format.
"""
import json
from urllib.parse import urljoin

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


async def refresh_library(server_url: str, token: str) -> None:
    """Ask Jellyfin to rescan its libraries after a new media file is ready."""
    base = _base_url(server_url)
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        response = await client.post(urljoin(base, "Library/Refresh"), headers=_headers(token))
    if response.status_code >= 400:
        raise JellyfinError(f"Jellyfin library refresh failed: HTTP {response.status_code}")

def refresh_library_sync(server_url: str, token: str) -> None:
    """Synchronous variant for the background worker."""
    base = _base_url(server_url)
    response = httpx.post(
        urljoin(base, "Library/Refresh"),
        headers=_headers(token),
        timeout=10,
    )
    if response.status_code >= 400:
        raise JellyfinError(f"Jellyfin library refresh failed: HTTP {response.status_code}")

def update_downloaded_item(
    server_url: str,
    token: str,
    file_path: str,
    title: str,
    artists: str,
    album: str,
    provider_id: str = "",
) -> str:
    """Find the scanned media file and update its metadata.

    Jellyfin library scans are asynchronous, so this polls for the item by
    exact file path before applying metadata. Returns the Jellyfin item id.
    """
    import time
    from urllib.parse import quote

    base = _base_url(server_url)
    headers = _headers(token)
    metadata = {
        "Name": title,
        "Album": album or None,
        "AlbumArtist": artists or None,
        "Artists": [artists] if artists else [],
        "Path": file_path,
    }
    if provider_id:
        metadata["ProviderIds"] = {"MusicDownloader": provider_id}

    with httpx.Client(timeout=10, follow_redirects=True) as client:
        for attempt in range(10):
            response = client.get(
                urljoin(base, "Items"),
                params={
                    "Recursive": "true",
                    "Limit": 200,
                    "Fields": "Path,ProviderIds",
                },
                headers=headers,
            )
            if response.status_code >= 400:
                raise JellyfinError(
                    f"Jellyfin item lookup failed: HTTP {response.status_code}"
                )
            items = response.json().get("Items", [])
            item = next(
                (x for x in items if str(x.get("Path", "")).rstrip("/") == file_path.rstrip("/")),
                None,
            )
            if item:
                item_id = item.get("Id")
                if not item_id:
                    raise JellyfinError("Jellyfin returned an item without an Id")
                update = client.put(
                    urljoin(base, f"Items/{quote(str(item_id), safe='')}"),
                    json=metadata,
                    headers={**headers, "Content-Type": "application/json"},
                )
                if update.status_code >= 400:
                    raise JellyfinError(
                        f"Jellyfin metadata update failed: HTTP {update.status_code}"
                    )
                return str(item_id)
            if attempt < 9:
                time.sleep(1)
    raise JellyfinError(f"Jellyfin item was not found after library refresh: {file_path}")
