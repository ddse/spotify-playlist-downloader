from pathlib import Path


SETTINGS = Path(__file__).resolve().parents[1] / "web" / "frontend" / "src" / "Settings.jsx"


def test_wireguard_toggle_waits_for_authoritative_transition_state():
    text = SETTINGS.read_text(encoding="utf-8")
    marker = "<button type=\"button\" onClick={()=>toggleWireguard(!wireguard?.requested_enabled)}"
    start = text.index(marker)
    button = text[start:text.index("</button>", start) + len("</button>")]
    assert "!!wireguard?.operation" in button
    assert "wireguard?.operation==='connecting'" in button
    assert "wireguard?.operation==='disconnecting'" in button
    assert "(!wireguard?.requested_enabled&&!wireguard?.configured)" in button


def test_wireguard_toggle_preserves_requested_intent_after_async_response():
    text = SETTINGS.read_text(encoding="utf-8")
    assert "setWireguard(x=>({...x,requested_enabled:enabled}))" in text
    assert "setWireguard(x=>({...x,...d,requested_enabled:enabled}))" in text
    assert "setWireguard(x=>({...x,requested_enabled:!enabled}))" in text


def test_settings_does_not_render_debug_logging_panel():
    text = SETTINGS.read_text(encoding="utf-8")
    assert "Debug logging" not in text
    assert "api('/api/debug')" not in text


def test_search_state_is_persisted_for_reload():
    app = (Path(__file__).resolve().parents[1] / "web" / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "music-search-state" in app
    assert "JSON.parse(localStorage.getItem('music-search-state')" in app
    assert "JSON.stringify({q,source,debugMode,results,searchErrors,searchDebug,page})" in app


def test_wireguard_ui_uses_websocket_instead_of_settings_polling():
    settings = SETTINGS.read_text(encoding="utf-8")
    app = (Path(__file__).resolve().parents[1] / "web" / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "new WebSocket(proto+'://'+location.host+'/ws/wireguard')" in settings
    assert "new WebSocket(proto+'://'+location.host+'/ws/wireguard')" in app
    assert "setInterval(refreshWireguard,3000)" not in settings


def test_wireguard_enable_state_is_not_persisted_to_local_storage():
    settings = SETTINGS.read_text(encoding="utf-8")
    app = (Path(__file__).resolve().parents[1] / "web" / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "music-wireguard" not in settings
    assert "music-wireguard" not in app

def test_wireguard_websocket_reconnects_after_disconnect():
    text = SETTINGS.read_text(encoding="utf-8")
    assert "ws.onclose=()=>{if(!stopped)retry=setTimeout(connect,1000)}" in text
    assert "let stopped=false" in text
    assert "clearTimeout(retry)" in text


def test_wireguard_toggle_keeps_transition_state_until_terminal_status():
    text = SETTINGS.read_text(encoding="utf-8")
    assert "operation:enabled?'connecting':'disconnecting'" in text
    assert "setTogglingWireguard(true)" in text
    assert "next.operation" in text


def test_wireguard_toggle_unlocks_when_transition_operation_is_finished_even_if_status_is_connecting():
    text = SETTINGS.read_text(encoding="utf-8")
    assert "if(next.operation) setTogglingWireguard(true); else setTogglingWireguard(false);" in text
