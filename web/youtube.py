import json, subprocess

def search(query: str, limit: int = 10):
    query = query.strip()
    if not query:
        return []
    cmd = ['yt-dlp', f'ytsearch{max(1, min(limit, 10))}:{query}', '--flat-playlist', '--dump-single-json', '--no-warnings', '--skip-download']
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout)[-2000:])
    data = json.loads(p.stdout)
    items = []
    for e in data.get('entries') or []:
        if not e or not e.get('id'):
            continue
        url = e.get('webpage_url') or e.get('original_url') or f"https://www.youtube.com/watch?v={e['id']}"
        items.append({'id': e['id'], 'title': e.get('title') or '', 'channel': e.get('channel') or e.get('uploader') or '', 'duration': e.get('duration'), 'url': url, 'thumbnail': e.get('thumbnail') or f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg"})
    return items
