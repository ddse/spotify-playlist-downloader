import os
import sqlite3
from contextlib import contextmanager
from urllib.parse import urlencode

import httpx
import spotipy
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from spotipy.oauth2 import SpotifyOAuth

DB_PATH = os.getenv("DB_PATH", "/state/app.db")
METUBE_URL = os.getenv("METUBE_URL", "http://metube:8081")
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8088/api/spotify/callback")

app = FastAPI(title="Spotify Playlist Downloader")
templates = Jinja2Templates(directory="templates")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS tracks (
        spotify_id TEXT PRIMARY KEY, title TEXT NOT NULL, artists TEXT NOT NULL,
        album TEXT, spotify_url TEXT, status TEXT NOT NULL DEFAULT 'queued',
        error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS playlists (
        spotify_id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1, last_sync TEXT
    )""")
    conn.commit()
    return conn


def spotify_client():
    if not CLIENT_ID or not CLIENT_SECRET:
        return None
    return spotipy.Spotify(auth_manager=spotipy.oauth2.SpotifyClientCredentials(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET
    ))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    conn = db()
    tracks = conn.execute("SELECT * FROM tracks ORDER BY created_at DESC LIMIT 50").fetchall()
    playlists = conn.execute("SELECT * FROM playlists ORDER BY name").fetchall()
    conn.close()
    return templates.TemplateResponse("index.html", {"request": request, "tracks": tracks, "playlists": playlists})


@app.get("/api/search")
def search(q: str = ""):
    sp = spotify_client()
    if not sp or not q.strip():
        return {"items": [], "error": "Spotify Client ID/Secret not configured" if not sp else None}
    result = sp.search(q=q, type="track", limit=20)
    items = []
    for t in result["tracks"]["items"]:
        items.append({
            "id": t["id"], "title": t["name"],
            "artists": ", ".join(a["name"] for a in t["artists"]),
            "album": t["album"]["name"], "url": t["external_urls"]["spotify"],
            "image": t["album"]["images"][2]["url"] if t["album"]["images"] else ""
        })
    return {"items": items}


@app.post("/api/download")
def download(spotify_id: str = Form(...), title: str = Form(...), artists: str = Form(...), album: str = Form(""), url: str = Form(...)):
    conn = db()
    conn.execute("""INSERT INTO tracks(spotify_id,title,artists,album,spotify_url,status)
        VALUES(?,?,?,?,?,'queued')
        ON CONFLICT(spotify_id) DO UPDATE SET title=excluded.title,artists=excluded.artists,
        album=excluded.album,spotify_url=excluded.spotify_url,status='queued',error=NULL,
        updated_at=CURRENT_TIMESTAMP""", (spotify_id, title, artists, album, url))
    conn.commit()
    conn.close()
    return RedirectResponse("/", status_code=303)


@app.post("/api/playlists")
def add_playlist(url: str = Form(...)):
    sp = spotify_client()
    if not sp:
        return RedirectResponse("/?error=spotify", status_code=303)
    playlist_id = url.rstrip("/").split("/")[-1].split("?")[0]
    p = sp.playlist(playlist_id, fields="id,name,external_urls.spotify")
    conn = db()
    conn.execute("INSERT OR REPLACE INTO playlists(spotify_id,name,url,enabled) VALUES(?,?,?,1)",
                 (p["id"], p["name"], p["external_urls"]["spotify"]))
    conn.commit(); conn.close()
    return RedirectResponse("/", status_code=303)


@app.post("/api/metube")
async def metube(url: str = Form(...)):
    # MeTube accepts its normal web API endpoint; keep this action intentionally separate
    # from Spotify state so direct YouTube downloads remain managed by MeTube.
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(f"{METUBE_URL}/add", json={"url": url})
            if r.status_code >= 400:
                return RedirectResponse("/?metube_error=1", status_code=303)
        except httpx.HTTPError:
            return RedirectResponse("/?metube_error=1", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.get("/metube")
def metube_redirect():
    return RedirectResponse(METUBE_URL)
