from unittest.mock import patch

from worker import worker


def test_youtube_download_uses_current_client_fallback_and_deno_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "MUSIC_DIR", str(tmp_path))
    row = {
        "source_url": "https://www.youtube.com/watch?v=eR_RNVgewSc",
        "source_mode": "single",
        "artists": "YouTube",
        "album": "YouTube",
        "title": "Faded",
        "download_folder": "",
        "download_type": "audio",
        "source_type": "youtube",
        "download_format": "mp3",
        "download_quality": "320",
        "video_codec": "auto",
        "thumbnail": 0,
        "subtitle": 0,
        "subtitle_lang": "ja,en",
        "subtitle_mode": "prefer_manual",
        "split_chapters": 0,
    }

    class FakeYDL:
        captured = None

        def __init__(self, opts):
            self.opts = opts
            FakeYDL.captured = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def download(self, urls):
            assert urls == ["https://www.youtube.com/watch?v=eR_RNVgewSc"]
            return 0

    with patch.object(worker.yt_dlp, "YoutubeDL", FakeYDL):
        result = worker.download(row, None, "yt:eR_RNVgewSc")

    assert result is None
    assert FakeYDL.captured["extractor_args"] == {
        "youtube": {"player_client": ["default", "web_embedded"]}
    }
    assert FakeYDL.captured["js_runtimes"] == {"deno": "/usr/local/bin/deno"}


def test_youtube_player_clients_can_be_overridden(monkeypatch):
    monkeypatch.setattr(worker, "YOUTUBE_PLAYER_CLIENTS", ["default", "web_safari"])

    assert worker.YOUTUBE_PLAYER_CLIENTS == ["default", "web_safari"]
