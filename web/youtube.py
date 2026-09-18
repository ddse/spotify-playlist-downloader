import json
import subprocess

PAGE_SIZE = 10
MAX_SEARCH_RESULTS = 50


def search(query: str, page: int = 1, limit: int = PAGE_SIZE):
    query = query.strip()
    page = max(1, int(page))
    limit = max(1, min(int(limit), PAGE_SIZE))
    if not query:
        return {'items': [], 'page': page, 'limit': limit, 'has_more': False}

    end = min(MAX_SEARCH_RESULTS, page * limit)
    cmd = [
        'yt-dlp',
        f'ytsearch{end}:{query}',
        '--flat-playlist',
        '--dump-single-json',
        '--no-warnings',
        '--skip-download',
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout)[-4000:])

    data = json.loads(p.stdout)
    all_items = []
    for e in data.get('entries') or []:
        if not e or not e.get('id'):
            continue
        url = (
            e.get('webpage_url')
            or e.get('original_url')
            or f"https://www.youtube.com/watch?v={e['id']}"
        )
        all_items.append({
            'id': e['id'],
            'title': e.get('title') or '',
            'channel': e.get('channel') or e.get('uploader') or '',
            'duration': e.get('duration'),
            'url': url,
            'thumbnail': e.get('thumbnail')
                or f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg",
        })

    start = (page - 1) * limit
    items = all_items[start:start + limit]
    has_more = len(all_items) > start + limit or (
        page * limit < MAX_SEARCH_RESULTS and len(all_items) == page * limit
    )

    return {
        'items': items,
        'page': page,
        'limit': limit,
        'has_more': has_more,
        'max_pages': MAX_SEARCH_RESULTS // limit,
    }
