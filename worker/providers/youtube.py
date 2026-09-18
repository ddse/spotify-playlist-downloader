import yt_dlp
MAX_SEARCH_RESULTS = 50

def search(query, page=1, limit=10):
    page=max(1,int(page)); limit=max(1,min(int(limit),10)); end=min(MAX_SEARCH_RESULTS,page*limit)
    with yt_dlp.YoutubeDL({"extract_flat":True,"skip_download":True,"quiet":True,"no_warnings":True,"noplaylist":False}) as ydl:
        data=ydl.extract_info(f"ytsearch{end}:{query}",download=False)
    items=[]
    for e in data.get("entries") or []:
        if not e or not e.get("id"): continue
        vid=e["id"]
        items.append({"id":vid,"title":e.get("title") or "","channel":e.get("channel") or e.get("uploader") or "",
                      "duration":e.get("duration"),"url":e.get("webpage_url") or e.get("original_url") or f"https://www.youtube.com/watch?v={vid}",
                      "thumbnail":e.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg","source":"youtube"})
    start=(page-1)*limit
    return {"items":items[start:start+limit],"page":page,"limit":limit,
            "has_more":len(items)>start+limit or (page*limit<MAX_SEARCH_RESULTS and len(items)==page*limit)}
