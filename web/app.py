import os, re, sqlite3, secrets, time, json
from pathlib import Path
import httpx
from fastapi import FastAPI, Form, Request, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from spotify import authorize_url, exchange, access_token, public_search, playlist_items, playlist_info
from youtube import search as youtube_search

DB_PATH=os.getenv('DB_PATH','/state/app.db'); SYNC_TOKEN=os.getenv('SYNC_TOKEN','')
app=FastAPI(title='Music Downloader v3'); templates=Jinja2Templates(directory='templates')
app.mount('/assets', StaticFiles(directory='static/assets'), name='assets')

from database import db

PROVIDER_DEFAULTS = {
    'spotify': {'client_id':'', 'client_secret':'', 'redirect_uri':'http://localhost:8088/api/spotify/callback'},
    'youtube': {'cookies':'', 'proxy':''},
    'soundcloud': {'cookies':'', 'proxy':''},
    'tiktok': {'cookies':'', 'proxy':''},
    'apple_music': {'developer_token':''},
    'zingmp3': {},
    'nhaccuatui': {},
}

def provider_row(c, provider):
    row=c.execute('SELECT * FROM provider_connections WHERE provider=?',(provider,)).fetchone()
    if not row:
        cfg=PROVIDER_DEFAULTS.get(provider,{})
        return {'provider':provider,'enabled':True,'config':cfg,'configured':False,'status':'not_configured','error':'','last_tested_at':None}
    try: cfg=json.loads(row['config_json'] or '{}')
    except Exception: cfg={}
    configured=any(bool(v) for v in cfg.values())
    safe=dict(cfg)
    for key in ('client_secret','developer_token'):
        if safe.get(key): safe[key]='********'
    if cfg.get('cookies'): safe['cookies']='********'
    return {'provider':provider,'enabled':bool(row['enabled']),'config':safe,'configured':configured,'status':row['status'] or 'not_configured','error':row['error'] or '','last_tested_at':row['last_tested_at']}

@app.get('/api/settings/connections')
def provider_connections():
    c=db(); items={p:provider_row(c,p) for p in PROVIDER_DEFAULTS}; c.close(); return {'items':items}

@app.put('/api/settings/connections/{provider}')
async def update_provider_connection(provider:str, request:Request):
    if provider not in PROVIDER_DEFAULTS: raise HTTPException(404,'unknown provider')
    body=await request.json(); cfg=body.get('config') or {}; enabled=1 if body.get('enabled',True) else 0
    c=db(); existing=c.execute('SELECT config_json FROM provider_connections WHERE provider=?',(provider,)).fetchone()
    old=json.loads(existing['config_json']) if existing and existing['config_json'] else {}
    for k,v in list(cfg.items()):
        if v=='********': cfg[k]=old.get(k,'')
    c.execute('INSERT INTO provider_connections(provider,enabled,config_json,status,error,updated_at) VALUES(?,?,?,CASE WHEN ? THEN \'configured\' ELSE \'disabled\' END,\'\',CURRENT_TIMESTAMP) ON CONFLICT(provider) DO UPDATE SET enabled=excluded.enabled,config_json=excluded.config_json,status=excluded.status,error=\'\',updated_at=CURRENT_TIMESTAMP',(provider,enabled,json.dumps(cfg),enabled))
    c.commit(); result=provider_row(c,provider); c.close(); return result

@app.post('/api/settings/connections/{provider}/test')
async def test_provider_connection(provider:str):
    if provider not in PROVIDER_DEFAULTS: raise HTTPException(404,'unknown provider')
    c=db(); row=c.execute('SELECT enabled,config_json FROM provider_connections WHERE provider=?',(provider,)).fetchone()
    cfg=json.loads(row['config_json']) if row and row['config_json'] else PROVIDER_DEFAULTS[provider]
    if not row or not row['enabled']:
        c.close(); return {'ok':False,'status':'disabled','error':'Provider is disabled'}
    ok=True; error=''
    try:
        if provider=='spotify':
            from spotify import spotify_credentials
            cid,secret,redirect=spotify_credentials()
            if not cid: raise ValueError('Client ID is required')
            if not redirect: raise ValueError('Redirect URI is required')
            if secret:
                async with httpx.AsyncClient(timeout=15) as x:
                    r=await x.post('https://accounts.spotify.com/api/token',data={'grant_type':'client_credentials'},headers={'Authorization':'Basic '+__import__('base64').b64encode(f'{cid}:{secret}'.encode()).decode()})
                    if r.status_code>=400: raise RuntimeError(f'Spotify token test failed: HTTP {r.status_code}')
        elif provider=='apple_music':
            if not cfg.get('developer_token'): raise ValueError('Developer Token is required')
        else:
            # Providers without mandatory credentials can be tested by enabling them.
            pass
    except Exception as e:
        ok=False; error=str(e)
    c.execute('UPDATE provider_connections SET status=?,error=?,last_tested_at=CURRENT_TIMESTAMP WHERE provider=?',('connected' if ok else 'error',error,provider)); c.commit(); c.close()
    return {'ok':ok,'status':'connected' if ok else 'error','error':error}


def detect_source_type(url):
    try:
        host = re.sub(r'^www\\.', '', (httpx.URL(url).host or '').lower())
        if host == 'zingmp3.vn' or host.endswith('.zingmp3.vn'):
            return 'zingmp3'
        if host == 'nhaccuatui.com' or host.endswith('.nhaccuatui.com'):
            return 'nhaccuatui'
    except Exception:
        pass
    return 'youtube'

def pid(url):
    m=re.search(r'playlist/([A-Za-z0-9]+)',url); return m.group(1) if m else url.rstrip('/').split('/')[-1].split('?')[0]

def worker_state(c, service):
    r=c.execute('SELECT heartbeat,detail FROM service_heartbeat WHERE service=?',(service,)).fetchone()
    if not r:return {'status':'offline','detail':''}
    age=time.time()-r['heartbeat'];return {'status':'online' if age<30 else 'offline','age':round(age,1),'detail':r['detail'] or ''}

@app.on_event('startup')
def startup(): db().close()

@app.get('/',response_class=HTMLResponse)
def index():
    return FileResponse('static/index.html')

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
    if not q.strip(): return {'items':[],'available':True}

    try:
        result=await public_search(q)
    except PermissionError as e:
        return {
            'items':[],
            'available':False,
            'error_code':'SPOTIFY_403',
            'error':str(e),
        }
    except Exception as e:
        return {
            'items':[],
            'available':False,
            'error_code':'SPOTIFY_ERROR',
            'error':str(e),
        }

    return {
        'items':[
            {
                'id':t['id'],
                'title':t['name'],
                'artists':', '.join(a['name'] for a in t['artists']),
                'album':t['album']['name'],
                'url':t['external_urls']['spotify'],
                'image':t['album']['images'][-1]['url'] if t['album']['images'] else '',
            }
            for t in result.get('tracks',{}).get('items',[])
        ],
        'available':True,
    }

@app.get('/api/search/youtube')
async def search_youtube(q:str='',page:int=1,limit:int=10):
    if not q.strip(): return {'items':[],'page':page,'limit':limit,'has_more':False}
    try:return await youtube_search(q,page=page,limit=limit)
    except Exception as e:return {'items':[],'page':page,'limit':limit,'has_more':False,'error':str(e)}

@app.get('/api/search/zingmp3')
async def search_zingmp3(q:str='',page:int=1,limit:int=10):
    if not q.strip(): return {'items':[],'page':page,'limit':limit,'has_more':False}
    try:return await youtube_search(q,page=page,limit=limit,source='zingmp3')
    except Exception as e:return {'items':[],'page':page,'limit':limit,'has_more':False,'error':str(e)}

@app.get('/api/search/nhaccuatui')
async def search_nhaccuatui(q:str='',page:int=1,limit:int=10):
    if not q.strip(): return {'items':[],'page':page,'limit':limit,'has_more':False}
    try:return await youtube_search(q,page=page,limit=limit,source='nhaccuatui')
    except Exception as e:return {'items':[],'page':page,'limit':limit,'has_more':False,'error':str(e)}

@app.post('/api/download')
def download(source_url:str=Form(...),title:str=Form(...),artists:str=Form(''),album:str=Form(''),youtube_id:str=Form(''),source_mode:str=Form('single'),download_type:str=Form('audio'),download_format:str=Form('mp3'),download_quality:str=Form('best'),video_codec:str=Form('auto'),download_folder:str=Form(''),thumbnail:str=Form('1'),subtitle:str=Form('0'),subtitle_lang:str=Form('ja,en'),subtitle_mode:str=Form('prefer_manual'),playlist_item_limit:str=Form('0'),split_chapters:str=Form('0'),auto_start:str=Form('1')):
    download_type = download_type if download_type in ('audio', 'video', 'captions', 'thumbnail') else 'audio'
    source_mode = source_mode if source_mode in {'single','playlist','channel'} else 'single'
    audio_formats = {'m4a','mp3','opus','wav','flac'}
    video_formats = {'any','mp4','ios'}
    audio_quality = {'0','128','192','256','320','best'}
    video_quality = {'best','2160','1440','1080','720','480','360'}
    codecs = {'auto','h264','h265','av1','vp9'}
    download_format = download_format.lower()
    if download_type == 'audio':
        download_format = download_format if download_format in audio_formats else 'mp3'
        download_quality = download_quality if download_quality in audio_quality else '320'
        video_codec = 'auto'
    elif download_type == 'video':
        download_format = download_format if download_format in video_formats else 'any'
        download_quality = download_quality if download_quality in video_quality else 'best'
        video_codec = video_codec if video_codec in codecs else 'auto'
    else:
        download_format, download_quality, video_codec = 'any', 'best', 'auto'
    try:
        folder = os.path.normpath(download_folder.strip()) if download_folder.strip() else ''
        if folder in ('.','/','..') or folder.startswith('../') or folder.startswith('..\\') or folder.startswith('/') or folder.startswith('\\'):
            folder = ''
        item_limit = max(0, min(int(playlist_item_limit or 0), 5000))
    except ValueError:
        folder, item_limit = '', 0
    subtitle_lang = re.sub(r'[^A-Za-z0-9,._-]', '', subtitle_lang)[:100] or 'ja,en'
    subtitle_mode = subtitle_mode if subtitle_mode in {'auto_only','manual_only','prefer_manual','prefer_auto'} else 'prefer_manual'
    thumb = 1 if str(thumbnail).lower() in {'1','true','on','yes'} else 0
    subs = 1 if str(subtitle).lower() in {'1','true','on','yes'} else 0
    chapters = 1 if str(split_chapters).lower() in {'1','true','on','yes'} else 0
    start = 1 if str(auto_start).lower() in {'1','true','on','yes'} else 0
    key=('yt:'+youtube_id if youtube_id else 'url:'+secrets.token_hex(12))+':'+download_type+':'+download_format+':'+download_quality+':'+video_codec+':'+folder+':'+str(item_limit)
    c=db()
    # Use named parameters here so adding/removing a column cannot silently
    # create a positional-binding mismatch.
    params = {
        'spotify_id': key,
        'title': title,
        'artists': artists,
        'album': album,
        'spotify_url': source_url,
        'source_type': detect_source_type(source_url),
        'source_url': source_url,
        'download_type': download_type,
        'download_format': download_format,
        'download_quality': download_quality,
        'video_codec': video_codec,
        'download_folder': folder,
        'source_mode': source_mode,
        'thumbnail': thumb,
        'subtitle': subs,
        'subtitle_lang': subtitle_lang,
        'subtitle_mode': subtitle_mode,
        'playlist_item_limit': item_limit,
        'split_chapters': chapters,
        'auto_start': start,
    }
    c.execute('''INSERT INTO tracks(
        spotify_id,title,artists,album,spotify_url,status,progress,error,
        source_type,source_url,download_type,download_format,download_quality,
        video_codec,download_folder,source_mode,thumbnail,subtitle,
        subtitle_lang,subtitle_mode,playlist_item_limit,split_chapters,auto_start,priority
    ) VALUES(
        :spotify_id,:title,:artists,:album,:spotify_url,'queued',0,NULL,
        :source_type,:source_url,:download_type,:download_format,:download_quality,
        :video_codec,:download_folder,:source_mode,:thumbnail,:subtitle,
        :subtitle_lang,:subtitle_mode,:playlist_item_limit,:split_chapters,:auto_start,0
    )
    ON CONFLICT(spotify_id) DO UPDATE SET
        title=excluded.title, artists=excluded.artists, album=excluded.album,
        source_type=excluded.source_type, source_url=excluded.source_url,
        download_type=excluded.download_type, download_format=excluded.download_format,
        download_quality=excluded.download_quality, video_codec=excluded.video_codec,
        download_folder=excluded.download_folder, source_mode=excluded.source_mode,
        thumbnail=excluded.thumbnail, subtitle=excluded.subtitle,
        subtitle_lang=excluded.subtitle_lang, subtitle_mode=excluded.subtitle_mode,
        playlist_item_limit=excluded.playlist_item_limit,
        split_chapters=excluded.split_chapters, auto_start=excluded.auto_start,
        status=CASE WHEN tracks.status='completed' THEN tracks.status ELSE 'queued' END,
        progress=CASE WHEN tracks.status='completed' THEN tracks.progress ELSE 0 END,
        error=NULL, updated_at=CURRENT_TIMESTAMP''', params)
    c.commit(); c.close()
    return {'ok': True, 'id': key, 'status': 'queued'}

@app.post('/api/queue/{track_id}/start')
def start_queue(track_id:str):
    c=db(); c.execute("UPDATE tracks SET auto_start=1,status=CASE WHEN status IN ('paused','queued') THEN 'queued' ELSE status END,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=? AND status IN ('paused','queued')",(track_id,)); c.commit(); c.close(); return {'ok':True}

@app.post('/api/queue/{track_id}/pause')
def pause_queue(track_id:str):
    c=db(); cur=c.execute("UPDATE tracks SET auto_start=0,status='paused',updated_at=CURRENT_TIMESTAMP WHERE spotify_id=? AND status='queued'",(track_id,)); c.commit(); c.close(); return {'ok':cur.rowcount>0}

@app.post('/api/queue/prioritize/{track_id}')
def prioritize_queue(track_id:str):
    c=db(); c.execute("UPDATE tracks SET priority=priority+1,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=? AND status IN ('queued','paused')",(track_id,)); c.commit(); c.close(); return {'ok':True}

@app.post('/api/import/youtube')
def import_youtube(source_url:str=Form(...),source_mode:str=Form('playlist'),download_type:str=Form('audio'),download_format:str=Form('mp3'),download_quality:str=Form('320'),download_folder:str=Form('YouTube'),playlist_item_limit:str=Form('0')):
    title = source_url.rstrip('/').split('/')[-1].split('?')[0] or 'YouTube import'
    return download(source_url=source_url,title=title,artists='YouTube',album=source_mode,youtube_id='',source_mode=source_mode,download_type=download_type,download_format=download_format,download_quality=download_quality,video_codec='auto',download_folder=download_folder,thumbnail='1',subtitle='0',subtitle_lang='ja,en',subtitle_mode='prefer_manual',playlist_item_limit=playlist_item_limit,split_chapters='0',auto_start='1')

@app.post('/api/queue/bulk')
def queue_bulk(action:str=Form(...),ids:str=Form('')):
    valid={'clear_selected','clear_completed','clear_failed','retry_failed','download_selected'}
    if action not in valid: raise HTTPException(400,'invalid action')
    selected=[x for x in ids.split(',') if x]
    c=db()
    if action == 'clear_selected' and selected:
        c.executemany("DELETE FROM tracks WHERE spotify_id=?",( (x,) for x in selected ))
    elif action == 'clear_completed':
        c.execute("DELETE FROM tracks WHERE status='completed'")
    elif action == 'clear_failed':
        c.execute("DELETE FROM tracks WHERE status='failed'")
    elif action == 'retry_failed':
        c.execute("UPDATE tracks SET status='queued',progress=0,error=NULL,download_speed='',eta='',updated_at=CURRENT_TIMESTAMP WHERE status='failed'")
    elif action == 'download_selected' and selected:
        c.executemany("UPDATE tracks SET status='queued',progress=0,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=? AND status='completed'",((x,) for x in selected))
    c.commit(); c.close()
    return {'ok':True}

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
    c=db(); rows=c.execute("SELECT * FROM tracks ORDER BY CASE status WHEN 'downloading' THEN 0 WHEN 'queued' THEN 1 WHEN 'pending_source' THEN 2 WHEN 'failed' THEN 3 ELSE 4 END,updated_at DESC LIMIT 100").fetchall(); c.close(); return {'items':[dict(r) for r in rows]}

@app.get('/api/history')
def history():
    c=db(); rows=c.execute("SELECT * FROM tracks WHERE status IN ('completed','failed') ORDER BY updated_at DESC LIMIT 200").fetchall(); c.close(); return {'items':[dict(r) for r in rows]}

@app.get('/api/files/{track_id}')
def download_file(track_id:str):
    c=db(); row=c.execute('SELECT * FROM tracks WHERE spotify_id=? AND status=\'completed\'',(track_id,)).fetchone(); c.close()
    if not row: raise HTTPException(404,'completed file not found')
    music=Path(os.getenv('MUSIC_DIR','/music')).resolve()
    artist=(row['artists'] or 'Unknown Artist').replace('/','_')
    album=(row['album'] or 'YouTube').replace('/','_')
    title=(row['title'] or 'Unknown Title').replace('/','_')
    custom=(row['download_folder'] or '').strip()
    folder=(music / custom) if custom else (music / artist / album)
    folder=folder.resolve()
    if music not in folder.parents and folder != music: raise HTTPException(400,'invalid download folder')
    preferred=[]
    fmt=(row['download_format'] or '').lower()
    if fmt in {'mp3','m4a','opus','wav','flac','mp4'}: preferred.append(fmt)
    preferred += ['mp3','m4a','opus','flac','wav','mp4','webm','mkv','mov']
    for ext in dict.fromkeys(preferred):
        p=folder / f'{title}.{ext}'
        if p.is_file(): return FileResponse(p, filename=p.name)
    matches=sorted(folder.glob(f'{title}.*'), key=lambda p:p.stat().st_mtime, reverse=True)
    for p in matches:
        if p.is_file() and p.suffix.lower() not in {'.jpg','.jpeg','.png','.webp','.vtt','.srt','.ass','.lrc'}:
            return FileResponse(p, filename=p.name)
    raise HTTPException(404,'completed file not found')

