import base64, hashlib, os, secrets, sqlite3, time
from urllib.parse import urlencode
import httpx

DB_PATH=os.getenv('DB_PATH','/state/app.db'); CLIENT_ID=os.getenv('SPOTIFY_CLIENT_ID',''); CLIENT_SECRET=os.getenv('SPOTIFY_CLIENT_SECRET',''); REDIRECT_URI=os.getenv('SPOTIFY_REDIRECT_URI','http://localhost:8088/api/spotify/callback')
SCOPES='playlist-read-private playlist-read-collaborative user-read-private'

def db():
    c=sqlite3.connect(DB_PATH,timeout=30); c.row_factory=sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS spotify_tokens(id INTEGER PRIMARY KEY CHECK(id=1),access_token TEXT,refresh_token TEXT,expires_at INTEGER,scope TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)')
    c.execute('CREATE TABLE IF NOT EXISTS oauth_state(state TEXT PRIMARY KEY,verifier TEXT NOT NULL,created_at INTEGER NOT NULL)'); c.commit(); return c

def pkce():
    verifier=secrets.token_urlsafe(64)[:128]; challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode(); return verifier,challenge

def authorize_url():
    verifier,challenge=pkce(); state=secrets.token_urlsafe(32); c=db(); c.execute('DELETE FROM oauth_state WHERE created_at<?',(int(time.time())-900,)); c.execute('INSERT INTO oauth_state VALUES(?,?,?)',(state,verifier,int(time.time()))); c.commit(); c.close()
    q={'response_type':'code','client_id':CLIENT_ID,'scope':SCOPES,'redirect_uri':REDIRECT_URI,'state':state,'code_challenge_method':'S256','code_challenge':challenge}; return 'https://accounts.spotify.com/authorize?'+urlencode(q)

async def exchange(code,state):
    c=db(); row=c.execute('SELECT verifier FROM oauth_state WHERE state=?',(state,)).fetchone(); c.execute('DELETE FROM oauth_state WHERE state=?',(state,)); c.commit(); c.close()
    if not row: raise ValueError('Invalid or expired OAuth state')
    data={'grant_type':'authorization_code','code':code,'redirect_uri':REDIRECT_URI,'client_id':CLIENT_ID,'code_verifier':row['verifier']}; headers={}
    if CLIENT_SECRET: headers['Authorization']='Basic '+base64.b64encode(f'{CLIENT_ID}:{CLIENT_SECRET}'.encode()).decode(); data.pop('client_id',None)
    async with httpx.AsyncClient(timeout=20) as x: r=await x.post('https://accounts.spotify.com/api/token',data=data,headers=headers); r.raise_for_status(); token=r.json()
    c=db(); c.execute('INSERT OR REPLACE INTO spotify_tokens(id,access_token,refresh_token,expires_at,scope) VALUES(1,?,?,?,?)',(token['access_token'],token.get('refresh_token'),int(time.time())+token['expires_in']-60,token.get('scope',''))); c.commit(); c.close()

async def access_token():
    c=db(); row=c.execute('SELECT * FROM spotify_tokens WHERE id=1').fetchone(); c.close()
    if not row:return None
    if row['expires_at'] and row['expires_at']>int(time.time()):return row['access_token']
    if not row['refresh_token']:return None
    data={'grant_type':'refresh_token','refresh_token':row['refresh_token'],'client_id':CLIENT_ID}; headers={}
    if CLIENT_SECRET: headers['Authorization']='Basic '+base64.b64encode(f'{CLIENT_ID}:{CLIENT_SECRET}'.encode()).decode(); data.pop('client_id',None)
    async with httpx.AsyncClient(timeout=20) as x:
        r=await x.post('https://accounts.spotify.com/api/token',data=data,headers=headers)
        if r.status_code>=400:
            c=db(); c.execute('DELETE FROM spotify_tokens WHERE id=1'); c.commit(); c.close(); return None
        t=r.json()
    c=db(); c.execute('UPDATE spotify_tokens SET access_token=?,refresh_token=?,expires_at=?,scope=?,updated_at=CURRENT_TIMESTAMP WHERE id=1',(t['access_token'],t.get('refresh_token') or row['refresh_token'],int(time.time())+t['expires_in']-60,t.get('scope',row['scope']))); c.commit(); c.close(); return t['access_token']

async def api(path,params=None):
    token=await access_token()
    if not token:return None
    async with httpx.AsyncClient(timeout=30) as x:
        r=await x.get('https://api.spotify.com/v1'+path,params=params,headers={'Authorization':'Bearer '+token})
        if r.status_code==401:return None
        r.raise_for_status(); return r.json()

async def public_search(q):
    token=await access_token()
    if token:return await api('/search',{'q':q,'type':'track','limit':10})
    if not CLIENT_ID or not CLIENT_SECRET:return {'tracks':{'items':[]}}
    raw=base64.b64encode(f'{CLIENT_ID}:{CLIENT_SECRET}'.encode()).decode()
    async with httpx.AsyncClient(timeout=20) as x:
        t=await x.post('https://accounts.spotify.com/api/token',data={'grant_type':'client_credentials'},headers={'Authorization':'Basic '+raw}); t.raise_for_status(); at=t.json()['access_token']
        r=await x.get('https://api.spotify.com/v1/search',params={'q':q,'type':'track','limit':10},headers={'Authorization':'Bearer '+at}); r.raise_for_status(); return r.json()

async def playlist_items(pid):
    out=[]; offset=0
    while True:
        data=await api(f'/playlists/{pid}/items',{'limit':50,'offset':offset,'fields':'items(item(id,name,artists,album,external_urls,duration_ms,is_local,type)),next,total'})
        if data is None:raise PermissionError('Spotify authorization is required for this playlist')
        for it in data.get('items',[]):
            t=it.get('item') or {}
            if t.get('type')=='track' and t.get('id') and not t.get('is_local'):out.append(t)
        if not data.get('next'):break
        offset+=len(data.get('items',[]))
    return out

async def playlist_info(pid):
    return await api(f'/playlists/{pid}',{'fields':'id,name,description,external_urls.spotify,public,owner.display_name'})
