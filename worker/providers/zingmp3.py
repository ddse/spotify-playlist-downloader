import json, urllib.request
from urllib.parse import quote

def search(query,page=1,limit=10):
    page=max(1,int(page)); limit=max(1,min(int(limit),10))
    url=f"https://ac.zingmp3.vn/v1/web/search?num={limit}&page={page}&query={quote(query)}"
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0","Referer":"https://zingmp3.vn/","Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as r: data=json.loads(r.read().decode("utf-8","ignore"))
    items=[]
    for section in (data.get("data") or {}).get("items") or []:
        if not isinstance(section,dict): continue
        for e in section.get("song") or section.get("items") or []:
            if not isinstance(e,dict): continue
            sid=e.get("encodeId") or e.get("id")
            if not sid: continue
            items.append({"id":sid,"title":e.get("title") or "","channel":", ".join(a.get("name","") for a in e.get("artists") or []),
                          "duration":e.get("duration"),"url":f"https://zingmp3.vn/bai-hat/{e.get('alias','')}/{sid}.html",
                          "thumbnail":e.get("thumbnailM") or e.get("thumbnail") or "","source":"zingmp3"})
    return {"items":items[:limit],"page":page,"limit":limit,"has_more":len(items)>=limit}
