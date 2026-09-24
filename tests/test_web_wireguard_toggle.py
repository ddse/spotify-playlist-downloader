from pathlib import Path


SETTINGS = Path(__file__).resolve().parents[1] / "web" / "frontend" / "src" / "Settings.jsx"


def test_wireguard_toggle_does_not_disable_during_connecting_or_disconnecting():
    text = SETTINGS.read_text(encoding="utf-8")
    marker = "<button type=\"button\" onClick={()=>toggleWireguard(!wireguard?.requested_enabled)}"
    start = text.index(marker)
    button = text[start:text.index("</button>", start) + len("</button>")]
    assert "wireguard?.status==='connecting'" not in button
    assert "wireguard?.status==='disconnecting'" not in button
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
    assert "localStorage.setItem('music-wireguard'" not in settings
    assert "localStorage.setItem('music-wireguard'" not in app
