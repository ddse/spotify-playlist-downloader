import os, re, sqlite3, secrets, time, json, logging, asyncio
from pathlib import Path
from urllib.parse import urlparse
import urllib.parse
import httpx
from fastapi import FastAPI, Form, Request, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from database import db
from spotify import authorize_url, exchange, access_token, public_search, playlist_items, playlist_info
from youtube import search as youtube_search

DEBUG_MODE=os.getenv('DEBUG','0').lower() in {'1','true','yes','on','debug'}
LOG_LEVEL='DEBUG' if DEBUG_MODE else os.getenv('LOG_LEVEL','INFO').upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format='%(asctime)s %(levelname)s [web] %(message)s')
logger=logging.getLogger('web')
logger.info('Web logging initialized debug=%s log_level=%s', DEBUG_MODE, LOG_LEVEL)
DB_PATH=os.getenv('DB_PATH','/state/app.db'); SYNC_TOKEN=os.getenv('SYNC_TOKEN','')
WIREGUARD_ENV_DEFAULT=os.getenv('WIREGUARD_DEFAULT','0') in {'1','true','yes','on'}

async def wireguard_enabled():
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response=await client.get(f"{os.getenv('WORKER_ENDPOINT','http://worker:8090')}/api/wireguard")
            response.raise_for_status()
            return bool(response.json().get('enabled'))
    except Exception:
        return False

app=FastAPI(title='Music Downloader v3'); templates=Jinja2Templates(directory='templates')
app.state.wireguard_clients=set(); app.state.wireguard_last=None
# Keep module imports usable for unit tests and tooling that do not build the
# frontend assets. The production image still mounts the directory when it is
# present.
_assets_dir = Path('static/assets')
if _assets_dir.is_dir():
    app.mount('/assets', StaticFiles(directory=str(_assets_dir)), name='assets')


def detect_source_type(url):
    """Classify the source URL for the worker and history UI."""
    try:
        host = (urlparse(url).hostname or '').lower()
    except Exception:
        host = ''
    if host == 'spotify.com' or host.endswith('.spotify.com'):
        return 'spotify'
    if host == 'youtube.com' or host.endswith('.youtube.com') or host == 'youtu.be':
        return 'youtube'
    if host == 'zingmp3.vn' or host.endswith('.zingmp3.vn'):
        return 'zingmp3'
    if host == 'nhaccuatui.com' or host.endswith('.nhaccuatui.com'):
        return 'nhaccuatui'
    if host == 'soundcloud.com' or host.endswith('.soundcloud.com'):
        return 'soundcloud'
    if host == 'tiktok.com' or host.endswith('.tiktok.com'):
        return 'tiktok'
    return 'url'

def normalize_track_title(value: str, fallback: str = 'Unknown Title') -> str:
    """Normalize a user/provider title for filesystem-safe media names."""
    value = re.sub(r'[\x00-\x1f\x7f]+', ' ', str(value or '')).strip()
    value = re.sub(r'[\\/:*?"<>|]+', '_', value)
    value = re.sub(r'\s+', ' ', value).strip(' .')
    if not value:
        value = fallback
    return value[:200]

def pid(url):
    m=re.search(r'playlist/([A-Za-z0-9]+)',url); return m.group(1) if m else url.rstrip('/').split('/')[-1].split('?')[0]

def worker_state(c, service):
    r=c.execute('SELECT heartbeat,detail FROM service_heartbeat WHERE service=?',(service,)).fetchone()
    if not r:return {'status':'offline','detail':''}
    age=time.time()-r['heartbeat'];return {'status':'online' if age<30 else 'offline','age':round(age,1),'detail':r['detail'] or ''}

@app.on_event('startup')
async def startup():
    db().close()
    async def broadcaster():
        while True:
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    response=await client.get(f"{os.getenv('WORKER_ENDPOINT','http://worker:8090')}/api/wireguard")
                    response.raise_for_status(); wg=response.json()
                payload={'type':'wireguard',**wg,'requested_enabled':bool(wg.get('requested_enabled', wg.get('enabled')))}
                fingerprint=json.dumps(payload,sort_keys=True)
                if fingerprint != app.state.wireguard_last:
                    app.state.wireguard_last=fingerprint
                    for ws in list(app.state.wireguard_clients):
                        try: await ws.send_json(payload)
                        except Exception: app.state.wireguard_clients.discard(ws)
            except Exception:
                payload={'type':'wireguard','enabled':False,'requested_enabled':False,'status':'unavailable','status_detail':'WireGuard worker status unavailable'}
                fingerprint=json.dumps(payload,sort_keys=True)
                if fingerprint != app.state.wireguard_last:
                    app.state.wireguard_last=fingerprint
                    for ws in list(app.state.wireguard_clients):
                        try: await ws.send_json(payload)
                        except Exception: app.state.wireguard_clients.discard(ws)
            await asyncio.sleep(1)
    asyncio.create_task(broadcaster())

@app.websocket('/ws/wireguard')
async def wireguard_socket(websocket: WebSocket):
    await websocket.accept()
    app.state.wireguard_clients.add(websocket)
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response=await client.get(f"{os.getenv('WORKER_ENDPOINT','http://worker:8090')}/api/wireguard")
            response.raise_for_status(); wg=response.json()
        await websocket.send_json({'type':'wireguard',**wg,'requested_enabled':bool(wg.get('requested_enabled', wg.get('enabled')))})
        while True: await websocket.receive_text()
    except WebSocketDisconnect:
        app.state.wireguard_clients.discard(websocket)
    except Exception:
        app.state.wireguard_clients.discard(websocket)



@app.get('/',response_class=HTMLResponse)
def index():
    response = FileResponse('static/index.html')
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.get('/health')
async def health(): return {'ok':True}

@app.get('/api/debug')
async def debug_status():
    return {'debug': DEBUG_MODE, 'log_level': LOG_LEVEL}


@app.get('/api/health')
async def api_health():
    logger.debug('Health check requested')
    c=db(); w=worker_state(c,'worker'); s=worker_state(c,'scheduler'); c.close(); return {'ok':True,'spotify_connected':bool(await access_token()),'worker':w,'scheduler':s,'wireguard':await wireguard_enabled()}

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

def wireguard_configured():
    c = db()
    row = c.execute("SELECT value FROM app_settings WHERE key='wireguard_config'").fetchone()
    c.close()
    return bool(row and (row["value"] or "").strip())


@app.get('/api/settings/wireguard')
async def wireguard_settings():
    """Return WireGuard state without ever returning the saved configuration."""
    configured = wireguard_configured()
    result = {
        'enabled': False,
        'requested_enabled': False,
        'interface': os.getenv('WG_INTERFACE', 'wg0'),
        'config_path': 'database://wireguard_config',
        'config_exists': configured,
        'configured': configured,
        'status': 'disconnected' if configured else 'unavailable',
    }
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(
                f"{os.getenv('WORKER_ENDPOINT','http://worker:8090')}/api/wireguard"
            )
            response.raise_for_status()
            wg = response.json()
            result.update({
                'enabled': bool(wg.get('enabled')),
                'requested_enabled': bool(wg.get('requested_enabled', wg.get('enabled'))),
                'interface': wg.get('interface', 'wg0'),
                'config_path': 'database://wireguard_config',
                'config_exists': configured,
                'configured': configured,
                'status': wg.get('status') or ('connected' if wg.get('vpn_route') else ('connecting' if wg.get('enabled') else 'disconnected')),
                'status_detail': wg.get('status_detail', ''),
                'vpn_route': bool(wg.get('vpn_route')),
                'route_active': bool(wg.get('route_active')),
                'handshake_recent': bool(wg.get('handshake_recent')),
                'public_ip': wg.get('public_ip', ''),
                'receive_bytes': int(wg.get('receive_bytes', 0)),
                'send_bytes': int(wg.get('send_bytes', 0)),
                'peer_count': int(wg.get('peer_count', 0)),
            })
    except Exception as e:
        # Do not overwrite the last known requested target just because the
        # worker status endpoint is temporarily unavailable.
        result['status'] = 'unavailable'
        result['status_detail'] = 'WireGuard worker status unavailable'
        result['detail'] = str(e)
    return result


@app.put('/api/settings/wireguard')
async def wireguard_save(request: Request):
    """Save a WireGuard client config to DB; the response never contains its contents."""
    body = await request.json()
    config = str(body.get('config') or '').strip()
    enabled = body.get('enabled')
    c = db()
    if config:
        c.execute(
            "INSERT INTO app_settings(key,value) VALUES('wireguard_config',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (config,),
        )
    c.commit()
    c.close()
    return {
        'ok': True,
        'configured': wireguard_configured(),
        'requested_enabled': False,
        'config_path': 'database://wireguard_config',
        'message': 'WireGuard configuration saved. The configuration is write-only from the UI.',
    }


@app.post('/api/settings/wireguard')
async def wireguard_toggle(enabled: bool = Form(...)):
    """Persist and apply the WireGuard preference through the worker."""
    value = '1' if enabled else '0'
    configured = wireguard_configured()
    if enabled and not configured:
        raise HTTPException(400, 'Save a WireGuard configuration before enabling it')
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{os.getenv('WORKER_ENDPOINT','http://worker:8090')}/api/wireguard",
                data={'enabled': value},
            )
            response.raise_for_status()
            result = response.json()
            result['requested_enabled'] = enabled
            result['config_exists'] = configured
            result['configured'] = configured
            result.pop('config_path', None)
            result['config_path'] = 'database://wireguard_config'
            return result
    except Exception as e:
        # The worker may be restarting while wg-quick changes the interface.
        # Return the requested target explicitly so the client can keep the
        # transition state until the next authoritative status update.
        return {
            'ok': False,
            'enabled': False,
            'requested_enabled': enabled,
            'configured': configured,
            'config_exists': configured,
            'config_path': 'database://wireguard_config',
            'operation': 'connecting' if enabled else 'disconnecting',
            'status': 'connecting' if enabled else 'disconnecting',
            'status_detail': 'WireGuard transition is still in progress; waiting for worker status.' ,
            'error': str(e),
        }


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
    except Exception as e:
        ok=False; error=str(e)
    c.execute('UPDATE provider_connections SET status=?,error=?,last_tested_at=CURRENT_TIMESTAMP WHERE provider=?',('connected' if ok else 'error',error,provider)); c.commit(); c.close()
    return {'ok':ok,'status':'connected' if ok else 'error','error':error}

@app.get('/api/services')
async def services():
    c=db(); result={k:worker_state(c,k) for k in ('worker','scheduler')}; c.close()
    result['wireguard']={
        'enabled': await wireguard_enabled(),
        'interface': os.getenv('WG_INTERFACE','wg0'),
        'config_path': os.getenv('WG_CONFIG','/etc/wireguard/wg0.conf'),
        'config_exists': False,
        'status': 'unknown',
    }
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response=await client.get(f"{os.getenv('WORKER_ENDPOINT','http://worker:8090')}/api/wireguard")
            response.raise_for_status()
            wg=response.json()
            result['wireguard']={
                'enabled':bool(wg.get('enabled')),
                'interface':wg.get('interface','wg0'),
                'config_path':wg.get('config_path',os.getenv('WG_CONFIG','/etc/wireguard/wg0.conf')),
                'config_exists':bool(wg.get('config_exists')),
                'status':wg.get('status') or ('connected' if wg.get('vpn_route') else ('connecting' if await wireguard_enabled() else 'disconnected')),
                'vpn_route':bool(wg.get('vpn_route')),
                'route_active':bool(wg.get('route_active')),
                'handshake_recent':bool(wg.get('handshake_recent')),
                'public_ip':wg.get('public_ip',''),
                'receive_bytes':int(wg.get('receive_bytes',0)),
                'send_bytes':int(wg.get('send_bytes',0)),
                'peer_count':int(wg.get('peer_count',0)),
                'status_detail':wg.get('status_detail',''),
            }
    except Exception as e:
        result['wireguard']['status']='unavailable'
        result['wireguard']['detail']=str(e)
    return result

OUTLINK_ALLOWED_HOSTS = {
    'zingmp3.vn', 'www.zingmp3.vn', 'nhaccuatui.com', 'www.nhaccuatui.com',
    'nct.vn', 'www.nct.vn', 'image-cdn.nct.vn',
    'spotify.com', 'open.spotify.com',
    'youtube.com', 'www.youtube.com', 'youtu.be',
}

def _is_allowed_outlink_host(host: str) -> bool:
    host = (host or '').lower().rstrip('.')
    return host in OUTLINK_ALLOWED_HOSTS or any(
        host.endswith('.' + suffix)
        for suffix in ('zingmp3.vn', 'nhaccuatui.com', 'nct.vn', 'spotify.com', 'youtube.com', 'ytimg.com', 'zmdcdn.me', 'scdn.co')
    )


def _restore_proxied_outlink(value: str) -> str:
    """Restore an internal outlink proxy URL before handing it to the worker."""
    value = str(value or '').strip()
    if not value:
        return ''
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return value
    if parsed.path != '/api/outlink':
        return value
    target = urllib.parse.parse_qs(parsed.query).get('url', [''])[0].strip()
    return target or value


def _outlink_url(value: str) -> str:
    value = str(value or '').strip()
    if not value:
        return ''
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return ''
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return ''
    if not _is_allowed_outlink_host(parsed.hostname or ''):
        return ''
    return '/api/outlink?url=' + urllib.parse.quote(value, safe='')


def _image_proxy_url(value: str) -> str:
    value = str(value or '').strip()
    if not value:
        return ''
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return ''
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return ''
    if not _is_allowed_outlink_host(parsed.hostname or ''):
        return ''
    return '/api/image-proxy?url=' + urllib.parse.quote(value, safe='')


def _rewrite_outlinks(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == 'url' and isinstance(item, str):
                result['source_url'] = item
                result[key] = _outlink_url(item) or item
            elif key in {'image', 'thumbnail', 'thumbnailUrl', 'thumbnailM', 'imageUrl', 'image_url', 'cover', 'coverUrl', 'cover_url', 'avatar', 'avatarUrl', 'avatar_url'} and isinstance(item, str):
                # Image URLs are never exposed directly to the UI. Allowed remote
                # images are streamed through /api/image-proxy; unsupported image
                # hosts are blanked instead of leaking a direct URL.
                result[key] = _image_proxy_url(item) if urllib.parse.urlsplit(item).scheme in {'http', 'https'} else item

            elif key in {'streamURL', 'streamUrl', 'stream_url'} and isinstance(item, str):
                result[key] = _outlink_url(item) or item
            else:
                result[key] = _rewrite_outlinks(item)
        return result
    if isinstance(value, list):
        return [_rewrite_outlinks(item) for item in value]
    return value

@app.get('/api/image-proxy')
async def image_proxy(url: str):
    """Stream an allowlisted remote image without storing it on the server."""
    target = str(url or '').strip()
    try:
        parsed = urllib.parse.urlsplit(target)
    except ValueError:
        raise HTTPException(400, 'invalid image url')
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise HTTPException(400, 'invalid image url')
    if not _is_allowed_outlink_host(parsed.hostname or ''):
        raise HTTPException(403, 'image host is not allowed')

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=False,
        headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        },
    )
    response = None
    current = target
    try:
        for _ in range(4):
            parsed_current = urllib.parse.urlsplit(current)
            if parsed_current.scheme not in {'http', 'https'} or not _is_allowed_outlink_host(parsed_current.hostname or ''):
                raise HTTPException(403, 'image redirect host is not allowed')

            request = client.build_request('GET', current)
            response = await client.send(request, stream=True)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get('location')
                await response.aclose()
                response = None
                if not location:
                    raise HTTPException(502, 'image redirect missing location')
                current = urllib.parse.urljoin(current, location)
                continue
            break
        else:
            raise HTTPException(502, 'too many image redirects')

        if response is None:
            raise HTTPException(502, 'image response unavailable')
        if response.status_code != 200:
            await response.aclose()
            raise HTTPException(502, f'image fetch failed: HTTP {response.status_code}')

        content_type = (response.headers.get('content-type') or '').split(';', 1)[0].strip().lower()
        if not content_type.startswith('image/'):
            await response.aclose()
            raise HTTPException(415, 'upstream response is not an image')

        async def stream():
            try:
                async for chunk in response.aiter_bytes(64 * 1024):
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return StreamingResponse(
            stream(),
            media_type=content_type,
            headers={
                'Cache-Control': 'private, max-age=300',
                'X-Content-Type-Options': 'nosniff',
            },
        )
    except HTTPException:
        if response is not None:
            await response.aclose()
        await client.aclose()
        raise
    except httpx.HTTPError as exc:
        if response is not None:
            await response.aclose()
        await client.aclose()
        raise HTTPException(502, f'image fetch failed: {exc}')


@app.get('/api/outlink')
async def outlink(url: str):
    target = str(url or '').strip()
    try:
        parsed = urllib.parse.urlsplit(target)
    except ValueError:
        raise HTTPException(400, 'invalid outlink')
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise HTTPException(400, 'invalid outlink')
    host = (parsed.hostname or '').lower()
    if not _is_allowed_outlink_host(host):
        raise HTTPException(403, 'outlink host is not allowed')
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={'User-Agent': 'Mozilla/5.0'}) as client:
            response = await client.get(target)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f'outlink fetch failed: {exc}')
    content_type = (response.headers.get('content-type') or 'application/octet-stream').split(';', 1)[0].strip().lower()
    if content_type.startswith('image/'):
        return Response(content=response.content, media_type=content_type, headers={'Cache-Control': 'public, max-age=300'})
    return RedirectResponse(str(response.url), status_code=307)

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

    return _rewrite_outlinks({
        'items':[
            {
                'id':t['id'],
                'title':t['name'],
                'artists':', '.join(a['name'] for a in t['artists']),
                'album':t['album']['name'],
                'url':t['external_urls']['spotify'],
                'source_url':t['external_urls']['spotify'],
                'image':t['album']['images'][-1]['url'] if t['album']['images'] else '',
            }
            for t in result.get('tracks',{}).get('items',[])
        ],
        'available':True,
    })

def _search_result_for_ui(result):
    """Rewrite every provider outlink before returning search data to the UI."""
    return _rewrite_outlinks(result if isinstance(result, dict) else {'items': []})


@app.get('/api/search/youtube')
async def search_youtube(q:str='',page:int=1,limit:int=10,wireguard:int=0,debug:int=0):
    if not q.strip(): return {'items':[],'page':page,'limit':limit,'has_more':False}
    use_wireguard = bool(wireguard)
    try:
        return _search_result_for_ui(
            await youtube_search(q,page=page,limit=limit,wireguard=use_wireguard,debug=bool(debug))
        )
    except Exception as e:
        payload={'items':[],'page':page,'limit':limit,'has_more':False,'error':str(e)}
        if debug:
            payload['debug']={
                'request_received': True,
                'source': 'youtube',
                'query': q.strip(),
                'wireguard_requested': use_wireguard,
                'wireguard_used': use_wireguard,
                'steps': [{'step':'web_search_error','status':'error','error':f'{type(e).__name__}: {e}'}],
            }
        return payload

@app.get('/api/search/zingmp3')
async def search_zingmp3(q:str='',page:int=1,limit:int=10,debug:int=0):
    if not q.strip(): return {'items':[],'page':page,'limit':limit,'has_more':False}
    try:
        return _search_result_for_ui(
            await youtube_search(q,page=page,limit=limit,source='zingmp3',wireguard=await wireguard_enabled(),debug=bool(debug))
        )
    except Exception as e:
        return {'items':[],'page':page,'limit':limit,'has_more':False,'error':str(e)}

@app.get('/api/search/nhaccuatui')
async def search_nhaccuatui(q:str='',page:int=1,limit:int=10,debug:int=0):
    if not q.strip(): return {'items':[],'page':page,'limit':limit,'has_more':False}
    try:
        return _search_result_for_ui(
            await youtube_search(q,page=page,limit=limit,source='nhaccuatui',wireguard=await wireguard_enabled(),debug=bool(debug))
        )
    except Exception as e:
        return {'items':[],'page':page,'limit':limit,'has_more':False,'error':str(e)}

@app.post('/api/download')
async def download(source_url:str=Form(...),title:str=Form(...),artists:str=Form(''),album:str=Form(''),youtube_id:str=Form(''),title_override:str=Form('0'),source_mode:str=Form('single'),download_type:str=Form('audio'),download_format:str=Form('mp3'),download_quality:str=Form('best'),video_codec:str=Form('auto'),download_folder:str=Form(''),thumbnail:str=Form('1'),subtitle:str=Form('0'),subtitle_lang:str=Form('ja,en'),subtitle_mode:str=Form('prefer_manual'),playlist_item_limit:str=Form('0'),split_chapters:str=Form('0'),auto_start:str=Form('1'),wireguard:str=Form('0')):
    source_url = _restore_proxied_outlink(source_url)
    title = normalize_track_title(title)
    title_override_value = 1 if str(title_override).lower() in {'1','true','yes','on'} else 0
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
    use_wireguard = 1 if str(wireguard).lower() in {'1','true','on','yes'} else int(await wireguard_enabled())
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
        'wireguard': use_wireguard,
        'title_override': title_override_value,
    }
    c.execute('''INSERT INTO tracks(
        spotify_id,title,artists,album,spotify_url,status,progress,error,
        source_type,source_url,download_type,download_format,download_quality,
        video_codec,download_folder,source_mode,thumbnail,subtitle,
        subtitle_lang,subtitle_mode,playlist_item_limit,split_chapters,auto_start,wireguard,title_override,priority
    ) VALUES(
        :spotify_id,:title,:artists,:album,:spotify_url,'queued',0,NULL,
        :source_type,:source_url,:download_type,:download_format,:download_quality,
        :video_codec,:download_folder,:source_mode,:thumbnail,:subtitle,
        :subtitle_lang,:subtitle_mode,:playlist_item_limit,:split_chapters,:auto_start,:wireguard,:title_override,0
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
        split_chapters=excluded.split_chapters, auto_start=excluded.auto_start, wireguard=excluded.wireguard,
        title_override=excluded.title_override,
        status=CASE WHEN tracks.status='completed' THEN tracks.status ELSE 'queued' END,
        progress=CASE WHEN tracks.status='completed' THEN tracks.progress ELSE 0 END,
        error=NULL, updated_at=CURRENT_TIMESTAMP''', params)
    c.commit(); c.close()
    return {'ok': True, 'id': key, 'status': 'queued'}

@app.post('/api/tracks/title')
def update_track_title(track_id: str = Query(...), title: str = Form(...)):
    title = normalize_track_title(title)
    c = db()
    row = c.execute("SELECT * FROM tracks WHERE spotify_id=?", (track_id,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(404, 'track not found')
    if row['status'] == 'downloading':
        c.close()
        raise HTTPException(409, 'cannot rename a downloading track')

    old_path = (row['file_path'] or '').strip()
    new_path = ''
    if row['status'] == 'completed' and old_path:
        music = Path(os.getenv('MUSIC_DIR', '/music')).resolve()
        source = Path(old_path).resolve()
        if source.is_file() and music in source.parents:
            destination = source.with_name(title + source.suffix)
            if destination != source:
                if destination.exists():
                    c.close()
                    raise HTTPException(409, 'target filename already exists')
                source.rename(destination)
            new_path = str(destination)

    c.execute(
        "UPDATE tracks SET title=?,title_override=1,file_path=CASE WHEN ?='' THEN file_path ELSE ? END,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",
        (title, new_path, new_path, track_id),
    )
    c.commit(); c.close()
    return {'ok': True, 'title': title, 'file_path': new_path}

@app.post('/api/queue/start')
def start_queue(track_id:str=Query(...)):
    c=db(); c.execute("UPDATE tracks SET auto_start=1,status=CASE WHEN status IN ('paused','queued') THEN 'queued' ELSE status END,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=? AND status IN ('paused','queued')",(track_id,)); c.commit(); c.close(); return {'ok':True}

@app.post('/api/queue/pause')
def pause_queue(track_id:str=Query(...)):
    c=db()
    cur=c.execute(
        "UPDATE tracks SET auto_start=0,status=CASE WHEN status='downloading' THEN 'pausing' ELSE 'paused' END,updated_at=CURRENT_TIMESTAMP "
        "WHERE spotify_id=? AND status IN ('queued','downloading')",
        (track_id,),
    )
    c.commit()
    c.close()
    return {'ok':cur.rowcount>0}

@app.post('/api/queue/prioritize')
def prioritize_queue(track_id:str=Query(...)):
    c=db()
    cur=c.execute(
        "UPDATE tracks SET priority=priority+1,updated_at=CURRENT_TIMESTAMP "
        "WHERE spotify_id=? AND status IN ('queued','paused','downloading')",
        (track_id,),
    )
    c.commit()
    c.close()
    return {'ok':cur.rowcount>0}

@app.post('/api/queue/cancel')
def cancel_queue(track_id:str=Query(...)):
    c=db()
    row=c.execute("SELECT status FROM tracks WHERE spotify_id=?", (track_id,)).fetchone()
    if not row:
        c.close()
        return {'ok':False}
    status=row['status']
    if status == 'downloading':
        c.execute(
            "UPDATE tracks SET status='cancelling',auto_start=0,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",
            (track_id,),
        )
        c.commit()
        c.close()
        return {'ok':True}
    cur=c.execute(
        "DELETE FROM tracks WHERE spotify_id=? AND status IN ('queued','paused','pending_source','failed','pausing')",
        (track_id,),
    )
    c.commit()
    c.close()
    return {'ok':cur.rowcount>0}

@app.post('/api/import/youtube')
def import_youtube(source_url:str=Form(...),source_mode:str=Form('playlist'),download_type:str=Form('audio'),download_format:str=Form('mp3'),download_quality:str=Form('320'),download_folder:str=Form('YouTube'),playlist_item_limit:str=Form('0')):
    title = source_url.rstrip('/').split('/')[-1].split('?')[0] or 'YouTube import'
    return download(source_url=source_url,title=title,artists='YouTube',title_override='0',album=source_mode,youtube_id='',source_mode=source_mode,download_type=download_type,download_format=download_format,download_quality=download_quality,video_codec='auto',download_folder=download_folder,thumbnail='1',subtitle='0',subtitle_lang='ja,en',subtitle_mode='prefer_manual',playlist_item_limit=playlist_item_limit,split_chapters='0',auto_start='1')

@app.post('/api/queue/bulk')
def queue_bulk(action:str=Form(...),ids:str=Form('')):
    valid={'clear_selected','clear_completed','clear_failed','retry_failed','download_selected','remove_selected'}
    if action not in valid: raise HTTPException(400,'invalid action')
    selected=[x for x in ids.split(',') if x]
    c=db()
    if action == 'clear_selected' and selected:
        # Never delete an active worker row underneath yt-dlp. Mark it for
        # cancellation so the worker can interrupt the download safely.
        c.executemany(
            "UPDATE tracks SET status='cancelling',auto_start=0,updated_at=CURRENT_TIMESTAMP "
            "WHERE spotify_id=? AND status='downloading'",
            ((x,) for x in selected),
        )
        c.executemany(
            "DELETE FROM tracks WHERE spotify_id=? AND status IN ('queued','paused','pending_source','failed','pausing')",
            ((x,) for x in selected),
        )
    elif action == 'remove_selected' and selected:
        rows=c.execute("SELECT spotify_id,file_path,status FROM tracks WHERE spotify_id=?".replace("spotify_id=?","spotify_id IN (%s)" % ",".join("?"*len(selected))), tuple(selected)).fetchall()
        music=Path(os.getenv('MUSIC_DIR','/music')).resolve()
        for row in rows:
            path_value=(row['file_path'] or '').strip()
            if path_value:
                try:
                    p=Path(path_value).resolve()
                    if p.is_file() and music in p.parents:
                        p.unlink()
                except OSError:
                    pass
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

@app.delete('/api/files')
def remove_file(track_id:str=Query(...)):
    c=db()
    row=c.execute("SELECT * FROM tracks WHERE spotify_id=?", (track_id,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(404, 'track not found')
    music=Path(os.getenv('MUSIC_DIR','/music')).resolve()
    path_value=(row['file_path'] or '').strip() if 'file_path' in row.keys() else ''
    if path_value:
        try:
            p=Path(path_value).resolve()
            if p.is_file() and music in p.parents:
                p.unlink()
        except OSError:
            pass
    c.execute("DELETE FROM tracks WHERE spotify_id=?", (track_id,))
    c.commit()
    c.close()
    return {'ok':True}

@app.post('/api/retry')
def retry(track_id:str=Query(...)):
    c=db()
    cur=c.execute(
        "UPDATE tracks SET status='queued',progress=0,error=NULL,download_speed='',eta='',updated_at=CURRENT_TIMESTAMP "
        "WHERE spotify_id=? AND status IN ('failed','completed')",
        (track_id,),
    )
    c.commit()
    c.close()
    return {'ok':cur.rowcount>0}

@app.delete('/api/queue')
def delete_queue(track_id:str=Query(...)):
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

@app.get('/api/files')
def download_file(track_id:str=Query(...), download:bool=Query(False)):
    c=db()
    row=c.execute("SELECT * FROM tracks WHERE spotify_id=? AND status='completed'", (track_id,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404, 'completed file not found')

    music = Path(os.getenv('MUSIC_DIR', '/music')).resolve()
    path_value = (row['file_path'] or '').strip() if 'file_path' in row.keys() else ''
    candidates = [Path(path_value)] if path_value else []

    artist = (row['artists'] or 'Unknown Artist').replace('/', '_')
    album = (row['album'] or 'YouTube').replace('/', '_')
    title = (row['title'] or 'Unknown Title').replace('/', '_')
    custom = (row['download_folder'] or '').strip()
    folder = (music / custom) if custom else (music / artist / album)
    folder = folder.resolve()
    if music not in folder.parents and folder != music:
        raise HTTPException(400, 'invalid download folder')

    candidates.extend(folder.glob(f'{title}.*'))
    if folder.exists():
        candidates.extend(folder.rglob('*'))

    excluded = {'.jpg', '.jpeg', '.png', '.webp', '.vtt', '.srt', '.ass', '.lrc', '.part', '.ytdl'}
    seen = set()
    for p in candidates:
        try:
            p = p.resolve()
        except OSError:
            continue
        if str(p) in seen or not p.is_file() or p.suffix.lower() in excluded:
            continue
        seen.add(str(p))
        if music not in p.parents:
            continue
        return FileResponse(
            p,
            filename=p.name,
            content_disposition_type='attachment' if download else 'inline',
        )

    raise HTTPException(404, 'completed file not found')

