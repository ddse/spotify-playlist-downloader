import re, urllib.request
from html import unescape
from urllib.parse import quote

def search(query,page=1,limit=10):
    page=max(1,int(page)); limit=max(1,min(int(limit),10))
    url=f"https://www.nhaccuatui.com/tim-kiem?q={quote(query)}"
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=20) as r: html=r.read().decode("utf-8","ignore")
    pattern=re.compile(r'href=["\'](https?://(?:www\.)?nhaccuatui\.com/bai-hat/[^"\']+\.html)["\'][^>]*>(.*?)</a>',re.I|re.S)
    seen=set(); all_items=[]
    for url,raw in pattern.findall(html):
        if url in seen: continue
        seen.add(url); title=re.sub(r"<[^>]+>"," ",raw); title=re.sub(r"\s+"," ",unescape(title)).strip()
        if title: all_items.append({"id":url,"title":title,"channel":"NhacCuaTui","duration":None,"url":url,"thumbnail":"","source":"nhaccuatui"})
    start=(page-1)*limit
    return {"items":all_items[start:start+limit],"page":page,"limit":limit,"has_more":len(all_items)>start+limit or len(all_items)==page*limit}
