import React, {useEffect, useState} from 'react';

async function api(path, opts={}) {
  const r=await fetch(path, opts);
  const d=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(d.detail || d.error || 'Request failed');
  return d;
}

const PROVIDERS = [
  {id:'spotify', name:'Spotify', fields:[['client_id','Client ID'],['client_secret','Client Secret','password'],['redirect_uri','Redirect URI']]},
  {id:'youtube', name:'YouTube', fields:[['cookies','Cookies','textarea'],['proxy','Proxy']]},
  {id:'soundcloud', name:'SoundCloud', fields:[['cookies','Cookies','textarea'],['proxy','Proxy']]},
  {id:'tiktok', name:'TikTok', fields:[['cookies','Cookies','textarea'],['proxy','Proxy']]},
  {id:'apple_music', name:'Apple Music', fields:[['developer_token','Developer Token','password']]},
  {id:'zingmp3', name:'Zing MP3', fields:[]},
  {id:'nhaccuatui', name:'NhacCuaTui', fields:[]},
];

export default function Settings({onClose}) {
  const [items,setItems]=useState({});
  const [selected,setSelected]=useState('spotify');
  const [saving,setSaving]=useState(false);
  const [testing,setTesting]=useState(false);
  const [message,setMessage]=useState('');
  useEffect(()=>{api('/api/settings/connections').then(d=>setItems(d.items||{})).catch(e=>setMessage(e.message));},[]);
  const p=PROVIDERS.find(x=>x.id===selected);
  const item=items[selected]||{provider:selected,enabled:true,configured:false,config:{}};
  const config={...(item.config||{})};
  const set=(k,v)=>setItems(x=>({...x,[selected]:{...item,config:{...(x[selected]?.config||{}),[k]:v}}}));
  const save=async()=>{setSaving(true);setMessage('');try{const d=await api('/api/settings/connections/'+selected,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:item.enabled,config})});setItems(x=>({...x,[selected]:d}));setMessage('Saved. No restart required.')}catch(e){setMessage(e.message)}finally{setSaving(false)}};
  const test=async()=>{setTesting(true);setMessage('');try{const d=await api('/api/settings/connections/'+selected+'/test',{method:'POST'});setMessage(d.ok?'Connection test successful':(d.error||'Connection test failed'));setItems(x=>({...x,[selected]:{...x[selected],status:d.status,error:d.error||''}}));}catch(e){setMessage(e.message)}finally{setTesting(false)}};
  return <div className="glass mt-4 rounded-2xl p-5">
    <div className="flex items-center justify-between border-b border-white/10 pb-4"><div><h2 className="text-lg font-semibold">Provider Connections</h2><p className="text-xs text-zinc-500">Settings are stored in the application database and applied at runtime.</p></div><button onClick={onClose} className="rounded-lg border border-white/10 px-3 py-2 text-sm">Close</button></div>
    <div className="mt-5 grid gap-5 md:grid-cols-[220px_1fr]">
      <div className="space-y-1">{PROVIDERS.map(x=><button key={x.id} onClick={()=>{setSelected(x.id);setMessage('')}} className={'w-full rounded-lg px-3 py-2 text-left text-sm '+(selected===x.id?'bg-violet-600/20 text-violet-200':'text-zinc-400 hover:bg-white/5')}>{x.name}<span className="float-right text-xs">{items[x.id]?.configured?'●':'○'}</span></button>)}</div>
      <div>
        <div className="flex items-center justify-between"><h3 className="font-medium">{p.name}</h3><label className="flex items-center gap-2 text-sm text-zinc-400"><input type="checkbox" checked={item.enabled!==false} onChange={e=>setItems(x=>({...x,[selected]:{...item,enabled:e.target.checked}}))}/> Enabled</label></div>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          {p.fields.map(([k,label,type])=><label key={k} className={type==='textarea'?'md:col-span-2':''}><span className="mb-1 block text-xs text-zinc-500">{label}</span>{type==='textarea'?<textarea value={config[k]||''} onChange={e=>set(k,e.target.value)} className="min-h-28 w-full rounded-lg border border-white/10 bg-black/20 p-3 text-sm outline-none"/>:<input type={type||'text'} value={config[k]||''} onChange={e=>set(k,e.target.value)} className="w-full rounded-lg border border-white/10 bg-black/20 p-2.5 text-sm outline-none"/>}</label>)}
        </div>
        {p.fields.length===0&&<p className="mt-4 text-sm text-zinc-500">No credentials are required for this provider.</p>}
        <div className="mt-5 flex items-center gap-2"><button onClick={save} disabled={saving} className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white disabled:opacity-40">{saving?'Saving...':'Save'}</button><button onClick={test} disabled={testing} className="rounded-lg border border-white/10 px-4 py-2 text-sm">{testing?'Testing...':'Test connection'}</button>{message&&<span className="text-sm text-zinc-400">{message}</span>}</div>
        {item.status&&<div className="mt-3 text-xs text-zinc-500">Status: {item.status}{item.error?' · '+item.error:''}</div>}
      </div>
    </div>
  </div>;
}
