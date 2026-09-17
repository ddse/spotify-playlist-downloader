import os, re, sqlite3
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from spotify import authorize_url, exchange, access_token, public_search, playlist_items, playlist_info

DB_PATH=os.getenv('DB_PATH','/state/app.db'); METUBE_URL=os.getenv('METUBE_URL','http://metube:8081')
app=FastAPI(title='Spotify Playlist Downloader v2'); templates=Jinja2Templates(directory='templates')

def db():
    c=sqlite3.connect(DB_PATH,timeout=30); c.row_factory=sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS tracks(spotify_id TEXT PRIMARY KEY,title TEXT NOT NULL,artists TEXT NOT NULL,album TEXT,spotify_url TEXT,status TEXT NOT NULL DEFAULT 'queued',progress INTEGER DEFAULT 0,error TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS playlists(spotify_id TEXT PRIMARY KEY,name TEXT NOT NULL,url TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,last_sync TEXT)''')
    c.commit(); return c

def pid(url):
    m=re.search(r'playlist/([A-Za-z0-9]+)',url)
    return m.group(1) if m else url.rstrip('/').split('/')[-1].split('?')[0]

@app.on_event('startup')
def startup(): db().close()

@app.get('/',response_class=HTMLResponse)
def index(request:Request):
    c=db(); tracks=c.execute('SELECT * FROM tracks ORDER BY created_at DESC LIMIT 100').fetchall(); playlists=c.execute('SELECT * FROM playlists ORDER BY name').fetchall(); c.close()
    return templates.TemplateResponse('index.html',{'request':request,'tracks':tracks,'playlists':playlists,'spotify_connected':False})

@app.get('/api/health')
async def health(): return {'ok':True,'spotify_connected':bool(await access_token())}

@app.get('/api/spotify/login')
def spotify_login(): return RedirectResponse(authorize_url())

@app.get('/api/spotify/callback')
async def spotify_callback(code:str=None,state:str=None,error:str=None):
    if error: return RedirectResponse('/?spotify_error='+error)
    try: await exchange(code,state)
    except Exception: return RedirectResponse('/?spotify_error=oauth')
    return RedirectResponse('/?spotify=connected')

@app.post('/api/spotify/logout')
def spotify_logout():
    c=db(); c.execute('DELETE FROM spotify_tokens'); c.commit(); c.close(); return RedirectResponse('/',303)

@app.get('/api/search')
async def search(q:str=''):
    if not q.strip(): return {'items':[]}
    try: result=await public_search(q)
    except Exception as e: return {'items':[],'error':str(e)}
    return {'items':[{'id':t['id'],'title':t['name'],'artists':', '.join(a['name'] for a in t['artists']),'album':t['album']['name'],'url':t['external_urls']['spotify'],'image':t['album']['images'][-1]['url'] if t['album']['images'] else ''} for t in result.get('tracks',{}).get('items',[])]}

@app.post('/api/download')
def download(spotify_id:str=Form(...),title:str=Form(...),artists:str=Form(...),album:str=Form(''),url:str=Form(...)):
    c=db(); c.execute('''INSERT INTO tracks(spotify_id,title,artists,album,spotify_url,status,progress,error) VALUES(?,?,?,?,?,'queued',0,NULL) ON CONFLICT(spotify_id) DO UPDATE SET title=excluded.title,artists=excluded.artists,album=excluded.album,spotify_url=excluded.spotify_url,status=CASE WHEN tracks.status='completed' THEN tracks.status ELSE 'queued' END,progress=CASE WHEN tracks.status='completed' THEN tracks.progress ELSE 0 END,error=NULL,updated_at=CURRENT_TIMESTAMP''',(spotify_id,title,artists,album,url)); c.commit(); c.close(); return RedirectResponse('/',303)

@app.post('/api/retry/{spotify_id}')
def retry(spotify_id:str):
    c=db(); c.execute("UPDATE tracks SET status='queued',progress=0,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=? AND status IN ('failed','cancelled')",(spotify_id,)); c.commit(); c.close(); return {'ok':True}

@app.post('/api/playlists')
async def add_playlist(url:str=Form(...)):
    try:
        p=await playlist_info(pid(url))
        if not p: return RedirectResponse('/?playlist_error=auth',303)
        c=db(); c.execute('INSERT OR REPLACE INTO playlists(spotify_id,name,url,enabled) VALUES(?,?,?,1)',(p['id'],p['name'],p['external_urls']['spotify'])); c.commit(); c.close(); return RedirectResponse('/',303)
    except Exception: return RedirectResponse('/?playlist_error=1',303)

@app.post('/api/playlists/{playlist_id}/sync')
async def sync_playlist(playlist_id:str):
    try:
        tracks=await playlist_items(playlist_id); c=db()
        for t in tracks:
            c.execute("INSERT OR IGNORE INTO tracks(spotify_id,title,artists,album,spotify_url,status,progress) VALUES(?,?,?,?,?,'queued',0)",(t['id'],t['name'],', '.join(a['name'] for a in t['artists']),t['album']['name'],t['external_urls']['spotify']))
        c.execute("UPDATE playlists SET last_sync=CURRENT_TIMESTAMP WHERE spotify_id=?",(playlist_id,)); c.commit(); c.close(); return {'ok':True,'added':len(tracks)}
    except PermissionError as e: return {'ok':False,'error':str(e)}
    except Exception as e: return {'ok':False,'error':str(e)}

@app.get('/api/jobs')
def jobs():
    c=db(); rows=c.execute('SELECT spotify_id,title,artists,status,progress,error,updated_at FROM tracks ORDER BY updated_at DESC LIMIT 100').fetchall(); c.close(); return {'items':[dict(r) for r in rows]}

@app.get('/metube')
def metube_redirect(): return RedirectResponse(METUBE_URL)
