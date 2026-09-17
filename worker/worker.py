import os, re, sqlite3, subprocess, time
from pathlib import Path
DB_PATH=os.getenv('DB_PATH','/state/app.db'); MUSIC_DIR=os.getenv('MUSIC_DIR','/music'); FMT=os.getenv('AUDIO_FORMAT','mp3'); BITRATE=os.getenv('AUDIO_BITRATE','320k')

def conn():
    c=sqlite3.connect(DB_PATH,timeout=30); c.row_factory=sqlite3.Row; return c

def init(c):
    c.execute('''CREATE TABLE IF NOT EXISTS tracks(spotify_id TEXT PRIMARY KEY,title TEXT NOT NULL,artists TEXT NOT NULL,album TEXT,spotify_url TEXT,status TEXT NOT NULL DEFAULT 'queued',progress INTEGER DEFAULT 0,error TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)'''); c.commit()

def run(row):
    Path(MUSIC_DIR).mkdir(parents=True,exist_ok=True)
    cmd=['spotdl','download',row['spotify_url'],'--output',f'{MUSIC_DIR}/{{artist}}/{{album}}/{{track-number}} - {{title}}.{{output-ext}}','--format',FMT]
    if FMT in ('mp3','m4a','opus','flac'): cmd += ['--bitrate',BITRATE]
    return subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)

def pct(line):
    m=re.search(r'(\d{1,3})%',line)
    return max(0,min(100,int(m.group(1)))) if m else None

while True:
    try:
        c=conn(); init(c); row=c.execute("SELECT * FROM tracks WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row: c.close(); time.sleep(5); continue
        c.execute("UPDATE tracks SET status='downloading',progress=1,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",(row['spotify_id'],)); c.commit()
        try:
            p=run(row); output=[]
            for line in p.stdout:
                output.append(line)
                n=pct(line)
                if n is not None:
                    c.execute('UPDATE tracks SET progress=?,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?',(n,row['spotify_id'])); c.commit()
            rc=p.wait(timeout=3600)
            if rc==0: c.execute("UPDATE tracks SET status='completed',progress=100,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",(row['spotify_id'],))
            else: c.execute("UPDATE tracks SET status='failed',error=?,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",(''.join(output)[-4000:],row['spotify_id']))
        except Exception as e: c.execute("UPDATE tracks SET status='failed',error=?,updated_at=CURRENT_TIMESTAMP WHERE spotify_id=?",(str(e),row['spotify_id']))
        c.commit(); c.close()
    except Exception: time.sleep(10)
