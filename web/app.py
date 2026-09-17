import os, re, sqlite3, secrets, time
import httpx
from fastapi import FastAPI, Form, Request, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from spotify import authorize_url, exchange, access_token, public_search, playlist_items, playlist_info
from youtube import search as youtube_search

DB_PATH=os.getenv('DB_PATH','/state/app.db'); METUBE_PUBLIC_URL=os.getenv('METUBE_PUBLIC_URL','http://localhost:8081'); SYNC_TOKEN=os.getenv('SYNC_TOKEN','')
app=FastAPI(title='Music Downloader v3'); templates=Jinja2Templates(directory='templates')

def db():
    c=sqlite3.connect(DB_PATH,timeout=30); c.row_factory=sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS tracks(spotify_id TEXT PRIMARY KEY,title TEXT NOT NULL,artists TEXT NOT NULL,album TEXT,spotify_url TEXT,status TEXT NOT NULL DEFAULT 'pending_source',progress INTEGER DEFAULT 0,error TEXT,source_type TEXT DEFAULT 'spotify',source_url TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS playlists(spotify_id TEXT PRIMARY KEY,name TEXT NOT NULL,url TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,last_sync TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS service_heartbeat(service TEXT PRIMARY KEY,heartbeat REAL NOT NULL,detail TEXT)''')
    cols={r[1] for r in c.execute('PRAGMA table_info(tracks)').fetchall()}
    if 'source_type' not in cols: c.execute("ALTER TABLE tracks ADD COLUMN source_type TEXT DEFAULT 'spotify'")
    if 'source_url' not in cols: c.execute('ALTER TABLE tracks ADD COLUMN source_url TEXT')
    if 'progress' not in cols: c.execute('ALTER TABLE tracks ADD COLUMN progress INTEGER DEFAULT 0')
    c.commit(); return c

def pid(url):
    m=re.search(r'playlist/([A-Za-z0-9]+)',url); return m.group(1) if m else url.rstrip('/').split('/')[-1].split('?')[0]

def worker_state(c, service):
    r=c.execute('SELECT heartbeat,detail FROM service_heartbeat WHERE service=?',(service,)).fetchone()
    if not r:return {'status':'offline','detail':''}
    age=time.time()-r['heartbeat'];return {'status':'online' if age<30 else 'offline','age':round(age,1),'detail':r['detail'] or ''}

@app.on_event('startup')
def startup(): db().close()

@app.get('/',response_class=HTMLResponse)
def index(request:Request):
    c=db(); tracks=c.execute('SELECT * FROM tracks ORDER BY created_at DESC LIMIT 100').fetchall(); playlists=c.execute('SELECT * FROM playlists ORDER BY name').fetchall(); connected=bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='spotify_tokens'").fetchone() and c.execute('SELECT 1 FROM spotify_tokens WHERE id=1').fetchone()); c.close()
    return templates.TemplateResponse('index.html',{'request':request,'tracks':tracks,'playlists':playlists,'spotify_connected':connected})

@app.get('/health')
async def health(): return {'ok':True}

@app.get('/api/health')
async def api_health():
    c=db(); w=worker_state(c,'worker'); s=worker_state(c,'scheduler'); c.close(); return {'ok':True,'spotify_connected':bool(await access_token()),'worker':w,'scheduler':s}

@app.get('/api/services')
def services():
    c=db(); result={k:worker_state(c,k) for k in ('worker','scheduler')}; c.close(); return result

@app.get('/api/spotify/login')
def spotify_login(): return RedirectResponse(authorize_url())

@app.get('/api/spotify/callback')
async def spotify_callback(code:str=None,state:str=None,error:str=None):
    if error:return RedirectResponse('/?spotify_error='+error)
    try: await exchange(code,state)
    except Exception:return RedirectResponse('/?spotify_error=oauth')
    return RedirectResponse('/?spotify=connected')

@app.post('/api/spotify/logout')
def spotify_logout():
    c=db(); c.execute('DELETE FROM spotify_tokens'); c.commit(); c.close(); return RedirectResponse('/',303)

@app.get('/api/search/spotify')
async def search_spotify(q:str=''):
    if not q.strip(): return {'items':[]}
    try: result=await public_search(q)
    except Exception as e: return {'items':[],'error':str(e)}
    return {'items':[{'id':t['id'],'title':t['name'],'artists':', '.join(a['name'] for a in t['artists']),'album':t['album']['name'],'url':t['external_urls']['spotify'],'image':t['album']['images'][-1]['url'] if t['album']['images'] else ''} for t in result.get('tracks',{}).get('items',[])]}

@app.get('/api/search/youtube')
def search_youtube(q:str=''):
    if not q.strip(): return {'items':[]}
    try:return {'items':youtube_search(q)}
    except Exception as e:return {'items':[],'error':str(e)}

@app.post('/api/download')
def download(source_url:str=Form(...),title:str=Form(...),artists:str=Form(''),album:str=Form(''),youtube_id:str=Form('')):
    key='yt:'+youtube_id if youtube_id else 'url:'+secrets.token_hex(12)
    c=db(); c.execute('''INSERT INTO tracks(spotify_id,title,artists,album,spotify_url,status,progress,error,source_type,source_url) VALUES(?,?,?,?,?,'queued',0,NULL,'youtube',?) ON CONFLICT(spotify_id) DO UPDATE SET title=excluded.title,artists=excluded.artists,album=excluded.album,source_type='youtube',source_url=excluded.source_url,status=CASE WHEN tracks.status='completed' THEN tracks.status ELSE 'queued' END,progress=CASE WHEN tracks.status='completed' THEN tracks.progress ELSE 0 END,error=NULL,updated_at=CURRENT_TIMESTAMP''',(key,title,artists,album,source_url,source_url)); c.commit(); c.close(); return RedirectResponse('/',303)

@app.post('/api/retry/{track_id}')
def retry(track_id:str):
    c=db(); c.execute("UPDATE tracks SET status='queued',progress=0,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=? AND status='failed'",(track_id,)); c.commit(); c.close(); return {'ok':True}

@app.delete('/api/queue/{track_id}')
def delete_queue(track_id:str):
    c=db(); cur=c.execute("DELETE FROM tracks WHERE spotify_id=? AND status IN ('queued','failed','pending_source')",(track_id,)); c.commit(); c.close(); return {'ok':cur.rowcount>0}

@app.post('/api/playlists')
async def add_playlist(url:str=Form(...)):
    try:
        p=await playlist_info(pid(url))
        if not p:return RedirectResponse('/?playlist_error=auth',303)
        c=db(); c.execute('INSERT OR REPLACE INTO playlists(spotify_id,name,url,enabled) VALUES(?,?,?,1)',(p['id'],p['name'],p['external_urls']['spotify'])); c.commit(); c.close(); return RedirectResponse('/',303)
    except Exception:return RedirectResponse('/?playlist_error=1',303)

async def _sync_playlist(playlist_id):
    items=await playlist_items(playlist_id); c=db(); added=0
    for t in items:
        cur=c.execute("INSERT OR IGNORE INTO tracks(spotify_id,title,artists,album,spotify_url,status,progress,source_type,source_url) VALUES(?,?,?,?,?,'pending_source',0,'spotify',NULL)",(t['id'],t['name'],', '.join(a['name'] for a in t['artists']),t['album']['name'],t['external_urls']['spotify'])); added+=cur.rowcount
    c.execute("UPDATE playlists SET last_sync=CURRENT_TIMESTAMP WHERE spotify_id=?",(playlist_id,)); c.commit(); c.close(); return {'ok':True,'added':added,'total':len(items)}

@app.post('/api/playlists/{playlist_id}/sync')
async def sync_playlist_internal(playlist_id:str,x_sync_token:str=Header(default='')):
    if not SYNC_TOKEN or not secrets.compare_digest(x_sync_token,SYNC_TOKEN):raise HTTPException(403,'internal sync authorization required')
    try:return await _sync_playlist(playlist_id)
    except PermissionError as e:return {'ok':False,'error':str(e)}
    except Exception as e:return {'ok':False,'error':str(e)}

@app.post('/api/playlists/{playlist_id}/sync-manual')
async def sync_playlist_manual(playlist_id:str):
    try:return await _sync_playlist(playlist_id)
    except PermissionError as e:return {'ok':False,'error':str(e)}
    except Exception as e:return {'ok':False,'error':str(e)}

@app.delete('/api/playlists/{playlist_id}')
def delete_playlist(playlist_id:str):
    c=db(); cur=c.execute('DELETE FROM playlists WHERE spotify_id=?',(playlist_id,)); c.commit(); c.close(); return {'ok':cur.rowcount>0}

@app.post('/api/playlists/{playlist_id}/toggle')
def toggle_playlist(playlist_id:str):
    c=db(); c.execute('UPDATE playlists SET enabled=CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE spotify_id=?',(playlist_id,)); row=c.execute('SELECT enabled FROM playlists WHERE spotify_id=?',(playlist_id,)).fetchone(); c.commit(); c.close(); return {'ok':row is not None,'enabled':bool(row['enabled']) if row else False}

@app.get('/api/playlists')
def playlists():
    c=db(); rows=c.execute('SELECT * FROM playlists ORDER BY name').fetchall(); c.close(); return {'items':[dict(r) for r in rows]}

@app.get('/api/jobs')
def jobs():
    c=db(); rows=c.execute("SELECT spotify_id,title,artists,album,status,progress,error,source_type,source_url,updated_at,created_at FROM tracks ORDER BY CASE status WHEN 'downloading' THEN 0 WHEN 'queued' THEN 1 WHEN 'pending_source' THEN 2 WHEN 'failed' THEN 3 ELSE 4 END,updated_at DESC LIMIT 100").fetchall(); c.close(); return {'items':[dict(r) for r in rows]}

@app.get('/api/history')
def history():
    c=db(); rows=c.execute("SELECT spotify_id,title,artists,album,status,progress,error,source_type,source_url,updated_at,created_at FROM tracks WHERE status IN ('completed','failed') ORDER BY updated_at DESC LIMIT 200").fetchall(); c.close(); return {'items':[dict(r) for r in rows]}

@app.get('/metube')
def metube_redirect(): return RedirectResponse(METUBE_PUBLIC_URL)
