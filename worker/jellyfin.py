import logging
import os
from urllib.parse import urljoin

import httpx

logger = logging.getLogger('worker.jellyfin')


def _settings(c):
    rows = c.execute(
        "SELECT key, value FROM app_settings WHERE key IN ('jellyfin_url','jellyfin_api_key','jellyfin_enabled')"
    ).fetchall()
    values = {row['key']: row['value'] for row in rows}
    return values


def refresh_library(c):
    """Ask Jellyfin to rescan configured libraries after a validated download.

    Refresh failures are deliberately non-fatal: the media file is already
    validated and completed locally, so Jellyfin being unavailable must not
    turn a successful download into a failed job.
    """
    settings = _settings(c)
    enabled = str(settings.get('jellyfin_enabled', '0')).lower() in {'1', 'true', 'yes', 'on'}
    base_url = (settings.get('jellyfin_url') or os.getenv('JELLYFIN_URL', '')).strip().rstrip('/')
    api_key = (settings.get('jellyfin_api_key') or os.getenv('JELLYFIN_API_KEY', '')).strip()
    if not enabled or not base_url or not api_key:
        return {'ok': False, 'skipped': True, 'reason': 'not_configured'}

    endpoint = urljoin(base_url + '/', 'Library/Refresh')
    try:
        response = httpx.post(
            endpoint,
            headers={'X-Emby-Token': api_key},
            timeout=float(os.getenv('JELLYFIN_REFRESH_TIMEOUT', '10')),
        )
        response.raise_for_status()
        logger.info('jellyfin library refresh requested endpoint=%s', endpoint)
        return {'ok': True, 'skipped': False, 'status_code': response.status_code}
    except Exception as exc:
        logger.warning('jellyfin library refresh failed endpoint=%s error=%s', endpoint, exc)
        return {'ok': False, 'skipped': False, 'reason': str(exc)}
