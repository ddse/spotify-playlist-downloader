import React, {useEffect, useState} from 'react';
import {Settings2, X, KeyRound, Network, CheckCircle2} from 'lucide-react';

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
  const [wireguard,setWireguard]=useState(null);
  const [wireguardConfig,setWireguardConfig]=useState('');
  const [savingWireguard,setSavingWireguard]=useState(false);
  const [togglingWireguard,setTogglingWireguard]=useState(false);
  useEffect(()=>{
    api('/api/settings/connections').then(d=>setItems(d.items||{})).catch(e=>setMessage(e.message));
    const refreshWireguard=()=>api('/api/settings/wireguard').then(setWireguard).catch(e=>setWireguard(x=>({...x,status:'unavailable',detail:e.message})));
    refreshWireguard();
    const timer=setInterval(refreshWireguard,3000);
    return()=>clearInterval(timer);
  },[]);
  const p=PROVIDERS.find(x=>x.id===selected);
  const item=items[selected]||{provider:selected,enabled:true,configured:false,config:{}};
  const config={...(item.config||{})};
  const set=(k,v)=>setItems(x=>({...x,[selected]:{...item,config:{...(x[selected]?.config||{}),[k]:v}}}));
  const save=async()=>{setSaving(true);setMessage('');try{const d=await api('/api/settings/connections/'+selected,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:item.enabled,config})});setItems(x=>({...x,[selected]:d}));setMessage('Saved. No restart required.')}catch(e){setMessage(e.message)}finally{setSaving(false)}};
  const saveWireguard=async()=>{setSavingWireguard(true);setMessage('');try{const d=await api('/api/settings/wireguard',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({config:wireguardConfig})});setWireguard(x=>({...x,...d,config_exists:d.configured}));setWireguardConfig('');setMessage('WireGuard configuration saved. It will not be shown again.')}catch(e){setMessage(e.message)}finally{setSavingWireguard(false)}};
  const toggleWireguard=async(enabled)=>{setTogglingWireguard(true);setMessage('');setWireguard(x=>({...x,requested_enabled:enabled}));try{const fd=new FormData();fd.append('enabled',enabled?'1':'0');const d=await api('/api/settings/wireguard',{method:'POST',body:fd});setWireguard(x=>({...x,...d,requested_enabled:enabled}));localStorage.setItem('music-wireguard',enabled?'1':'0');window.dispatchEvent(new Event('wireguard-setting'));setMessage(enabled?'WireGuard connecting...':'WireGuard disconnecting...')}catch(e){setWireguard(x=>({...x,requested_enabled:!enabled}));setMessage(e.message)}finally{setTogglingWireguard(false)}};
  const test=async()=>{setTesting(true);setMessage('');try{const d=await api('/api/settings/connections/'+selected+'/test',{method:'POST'});setMessage(d.ok?'Connection test successful':(d.error||'Connection test failed'));setItems(x=>({...x,[selected]:{...x[selected],status:d.status,error:d.error||''}}));}catch(e){setMessage(e.message)}finally{setTesting(false)}};
  const currentConfigured=!!item.configured;
  const statusTone=item.status==='connected'||item.status==='ok'?'text-emerald-300':item.status==='error'?'text-red-300':'text-zinc-400';
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-3 backdrop-blur-md md:p-6" role="dialog" aria-modal="true" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}>
    <div className="flex max-h-[92vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-white/10 bg-zinc-950 shadow-2xl shadow-black/50">
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-3 md:px-5">
        <div className="flex items-center gap-3"><div className="rounded-lg bg-violet-600/15 p-2 text-violet-300"><Settings2 size={18}/></div><div><h2 className="font-semibold">Settings</h2><p className="text-[11px] text-zinc-500">Provider connections and network</p></div></div>
        <button type="button" onClick={onClose} className="rounded-lg p-2 text-zinc-400 hover:bg-white/5 hover:text-white" aria-label="Close settings"><X size={18}/></button>
      </div>
      <div className="overflow-x-auto border-b border-white/10 bg-black/20 px-3 pt-2"><div className="flex min-w-max gap-1">
        <button type="button" onClick={()=>{setSelected('spotify');setMessage('')}} className="flex items-center gap-2 rounded-t-lg px-3 py-2 text-xs text-zinc-400 hover:text-zinc-200"><KeyRound size={14}/> Providers</button>
        <button type="button" onClick={()=>{setSelected('wireguard');setMessage('')}} className="flex items-center gap-2 rounded-t-lg px-3 py-2 text-xs text-zinc-400 hover:text-zinc-200"><Network size={14}/> WireGuard</button>
      </div></div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-5">
        {selected==='wireguard' ? <div className="space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-medium">WireGuard</h3><p className="mt-1 text-xs text-zinc-500">Network tunnel configuration and connection status.</p></div><div className="flex items-center gap-2"><span className="rounded-full border border-white/10 px-2.5 py-1 text-[11px] text-zinc-300">{wireguard?.status||'loading'}</span><button type="button" onClick={()=>toggleWireguard(!wireguard?.requested_enabled)} disabled={togglingWireguard||wireguard===null||(!wireguard?.requested_enabled&&!wireguard?.configured)} className={'rounded-lg px-3 py-1.5 text-xs font-medium text-white '+(wireguard?.requested_enabled?'bg-red-600 hover:bg-red-500':'bg-emerald-600 hover:bg-emerald-500')}>{togglingWireguard?'Applying...':wireguard?.requested_enabled?'Disable':'Enable'}</button></div></div>
          <div className="grid gap-2 sm:grid-cols-3">{[['Interface',wireguard?.interface||'wg0'],['Config',wireguard?.configured?'Saved':'Missing'],['Route',wireguard?.route_active?'Active':'Inactive'],['Handshake',wireguard?.handshake_recent?'Recent':'Not recent'],['Public IP',wireguard?.public_ip||'—'],['Peers',wireguard?.peer_count??0]].map(([label,value])=><div key={label} className="rounded-xl border border-white/10 bg-white/[.02] p-3"><div className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</div><div className="mt-1 text-sm text-zinc-200">{value}</div></div>)}</div>
          <div className="rounded-xl border border-white/10 bg-black/20 p-3"><label><span className="mb-2 block text-xs font-medium text-zinc-400">Client configuration</span><textarea value={wireguardConfig} onChange={e=>setWireguardConfig(e.target.value)} placeholder={wireguard?.configured?'Configuration is saved and hidden. Paste a new config to replace it.':'Paste the contents of wg0.conf here'} className="min-h-32 w-full rounded-lg border border-white/10 bg-black/20 p-3 font-mono text-[11px] outline-none focus:border-violet-500"/></label><div className="mt-2 flex items-center gap-2"><button onClick={saveWireguard} disabled={savingWireguard||!wireguardConfig.trim()} className="rounded-lg bg-violet-600 px-3 py-2 text-xs font-medium text-white disabled:opacity-40">{savingWireguard?'Saving...':'Save configuration'}</button>{wireguard?.configured&&<span className="text-[11px] text-emerald-300">Saved (write-only)</span>}</div></div>
          {(wireguard?.status_detail||wireguard?.error||wireguard?.detail)&&<div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-xs text-amber-200">{wireguard.status_detail||wireguard.error||wireguard.detail}</div>}
        </div> : <div>
          <div className="mb-4 flex items-center gap-2 overflow-x-auto pb-1">{PROVIDERS.map(x=><button key={x.id} type="button" onClick={()=>{setSelected(x.id);setMessage('')}} className={'flex shrink-0 items-center gap-2 rounded-lg border px-3 py-2 text-xs '+(selected===x.id?'border-violet-500/40 bg-violet-500/10 text-violet-200':'border-white/5 text-zinc-400 hover:bg-white/5')}><span className={items[x.id]?.enabled===false?'h-1.5 w-1.5 rounded-full bg-zinc-600':'h-1.5 w-1.5 rounded-full bg-emerald-400'}/>{x.name}</button>)}</div>
          <div className="rounded-xl border border-white/10 bg-white/[.02] p-4">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/5 pb-4"><div><h3 className="font-medium">{p.name}</h3><p className="mt-1 text-xs text-zinc-500">{currentConfigured?'Configuration saved':'No configuration saved yet'}</p></div><label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-300"><input type="checkbox" checked={item.enabled!==false} onChange={e=>setItems(x=>({...x,[selected]:{...item,enabled:e.target.checked}}))}/> Enabled</label></div>
            <div className="mt-4 grid gap-3 md:grid-cols-2">{p.fields.map(([k,label,type])=><label key={k} className={type==='textarea'?'md:col-span-2':''}><span className="mb-1.5 block text-[11px] text-zinc-500">{label}</span>{type==='textarea'?<textarea value={config[k]||''} onChange={e=>set(k,e.target.value)} className="min-h-28 w-full rounded-lg border border-white/10 bg-black/20 p-2.5 text-xs outline-none focus:border-violet-500"/>:<input type={type||'text'} value={config[k]||''} onChange={e=>set(k,e.target.value)} className="w-full rounded-lg border border-white/10 bg-black/20 p-2.5 text-sm outline-none focus:border-violet-500"/>}</label>)}</div>
            {p.fields.length===0&&<div className="mt-4 rounded-lg border border-dashed border-white/10 p-3 text-xs text-zinc-500">No credentials are required for this provider.</div>}
            <div className="mt-5 flex flex-wrap items-center gap-2"><button onClick={save} disabled={saving} className="rounded-lg bg-violet-600 px-3.5 py-2 text-xs font-medium text-white disabled:opacity-40">{saving?'Saving...':'Save changes'}</button><button onClick={test} disabled={testing} className="rounded-lg border border-white/10 px-3.5 py-2 text-xs">{testing?'Testing...':'Test connection'}</button>{message&&<span className="text-xs text-zinc-400">{message}</span>}{item.status&&<span className={'ml-auto flex items-center gap-1 text-[11px] '+statusTone}><CheckCircle2 size={13}/> {item.status}</span>}</div>
          </div>
        </div>}
      </div>
    </div>
  </div>;
}
