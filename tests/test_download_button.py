from pathlib import Path


def test_download_button_submits_download_options_form():
    source = Path(__file__).resolve().parents[1] / "web" / "frontend" / "src" / "App.jsx"
    text = source.read_text(encoding="utf-8")
    assert 'type="submit" loading={sending}' in text
    assert "type={type}" in text
