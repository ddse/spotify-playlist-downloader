import re, urllib.request
from html import unescape
from urllib.parse import quote


def _clean_title(raw):
    raw = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<style[^>]*>.*?</style>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", unescape(raw)).strip()


def search(query,page=1,limit=10):
    page=max(1,int(page)); limit=max(1,min(int(limit),10))
    query=str(query or "").strip()
    if not query:
        return {"items":[],"page":page,"limit":limit,"has_more":False}
    url=f"https://www.nhaccuatui.com/tim-kiem?q={quote(query, safe='')}"
    req=urllib.request.Request(url,headers={
        "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept":"text/html,application/xhtml+xml",
        "Accept-Language":"vi-VN,vi;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req,timeout=20) as r:
        html=r.read().decode("utf-8","ignore")

    pattern=re.compile(
        r'href=["\'](https?://(?:www\.)?nhaccuatui\.com/(?:bai-hat|song)/[^"\']+)["\'][^>]*>(.*?)</a>',
        re.I|re.S
    )
    seen=set(); all_items=[]
    for url,raw in pattern.findall(html):
        url=unescape(url)
        if url in seen: continue
        seen.add(url)
        title=_clean_title(raw)
        if title and len(title) > 1:
            all_items.append({
                "id":url,
                "title":title,
                "channel":"NhacCuaTui",
                "duration":None,
                "url":url,
                "thumbnail":"",
                "source":"nhaccuatui"
            })
    start=(page-1)*limit
    return {
        "items":all_items[start:start+limit],
        "page":page,
        "limit":limit,
        "has_more":len(all_items)>start+limit
    }
