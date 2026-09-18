import os, re, sqlite3, secrets, time
from pathlib import Path
import httpx
from fastapi import FastAPI, Form, Request, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from database import db
from spotify import authorize_url, exchange, access_token, public_search, playlist_items, playlist_info
from youtube import search as youtube_search

DB_PATH=os.getenv('DB_PATH','/state/app.db'); SYNC_TOKEN=os.getenv('SYNC_TOKEN','')
WIREGUARD_ENV_DEFAULT=os.getenv('WIREGUARD_DEFAULT','0') in {'1','true','yes','on'}

def wireguard_enabled():
    c=db(); row=c.execute("SELECT value FROM app_settings WHERE key='wireguard_enabled'").fetchone()
    if row is None:
        value=1 if WIREGUARD_ENV_DEFAULT else 0
        c.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('wireguard_enabled',?)",(str(value),)); c.commit()
    else:
        value=row['value'] == '1'
    c.close(); return value

app=FastAPI(title='Music Downloader v3'); templates=Jinja2Templates(directory='templates')
app.mount('/assets', StaticFiles(directory='static/assets'), name='assets')


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
    c=db(); w=worker_state(c,'worker'); s=worker_state(c,'scheduler'); c.close()
    return {'ok':True,'spotify_connected':bool(await access_token()),'worker':w,'scheduler':s,'wireguard':wireguard_enabled()}

@app.get('/api/services')
def services():
    c=db(); result={k:worker_state(c,k) for k in ('worker','scheduler')}
    result['wireguard']={'enabled':wireguard_enabled()}
    c.close(); return result

@app.get('/api/settings/wireguard')
def get_wireguard_setting():
    return {'enabled': wireguard_enabled()}

@app.post('/api/settings/wireguard')
def set_wireguard_setting(enabled: bool = Form(False)):
    c=db(); value='1' if enabled else '0'
    c.execute("INSERT INTO app_settings(key,value) VALUES('wireguard_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(value,))
    c.commit(); c.close()
    return {'ok': True, 'enabled': bool(enabled)}

