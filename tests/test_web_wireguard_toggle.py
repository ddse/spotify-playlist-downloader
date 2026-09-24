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
