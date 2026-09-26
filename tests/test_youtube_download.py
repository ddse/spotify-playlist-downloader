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
    assert FakeYDL.captured["js_runtimes"] == {"deno": {"path": "/usr/local/bin/deno"}}


def test_youtube_player_clients_can_be_overridden(monkeypatch):
    monkeypatch.setattr(worker, "YOUTUBE_PLAYER_CLIENTS", ["default", "web_safari"])

    assert worker.YOUTUBE_PLAYER_CLIENTS == ["default", "web_safari"]


def test_direct_link_metadata_uses_provider_title_and_updates_track(monkeypatch):
    row = {
        "source_type": "url",
        "title_override": 0,
        "title": "track-123",
        "artists": "example.com",
        "album": "YouTube",
    }

    class FakeCursor:
        def __init__(self):
            self.updated = None
        def execute(self, sql, params):
            self.updated = (sql, params)
        def commit(self):
            pass

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def extract_info(self, url, download=False):
            assert url == "https://example.com/audio/track-123"
            assert download is False
            return {"title": "Real Song Title", "artist": "Real Artist", "album": "Real Album"}

    c = FakeCursor()
    monkeypatch.setattr(worker.yt_dlp, "YoutubeDL", FakeYDL)
    title, artist, album = worker.resolve_direct_link_metadata(
        row, c, "url:test", "https://example.com/audio/track-123"
    )
    assert (title, artist, album) == ("Real Song Title", "Real Artist", "Real Album")
    assert c.updated[1] == ("Real Song Title", "Real Artist", "Real Album", "url:test")



def _video_row(download_format="any", download_quality="1080"):
    return {
        "source_url": "https://www.youtube.com/watch?v=eR_RNVgewSc",
        "source_mode": "single",
        "artists": "YouTube",
        "album": "YouTube",
        "title": "Video",
        "download_folder": "",
        "download_type": "video",
        "source_type": "youtube",
        "download_format": download_format,
        "download_quality": download_quality,
        "video_codec": "auto",
        "thumbnail": 0,
        "subtitle": 0,
        "subtitle_lang": "ja,en",
        "subtitle_mode": "prefer_manual",
        "split_chapters": 0,
    }


def test_video_download_never_falls_back_to_audio_only_best_selector(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "MUSIC_DIR", str(tmp_path))

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
            return 0

    with patch.object(worker.yt_dlp, "YoutubeDL", FakeYDL):
        result = worker.download(_video_row(), None, "yt:video")

    assert result is None
    fmt = FakeYDL.captured["format"]
    assert "bestvideo" in fmt
    assert "+bestaudio" in fmt
    assert "/best[" not in fmt
    assert "/best" not in fmt.replace("/bestvideo", "")
    assert FakeYDL.captured["merge_output_format"] == "mp4"


def test_mp4_video_download_requires_mp4_video_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "MUSIC_DIR", str(tmp_path))

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
            return 0

    with patch.object(worker.yt_dlp, "YoutubeDL", FakeYDL):
        worker.download(_video_row("mp4", "1080"), None, "yt:video-mp4")

    fmt = FakeYDL.captured["format"]
    assert "bestvideo[ext=mp4][height<=1080]" in fmt
    assert "bestaudio[ext=m4a]" in fmt
    assert "best[" not in fmt
    assert "bestvideo" in fmt


def test_youtube_audio_download_keeps_audio_only_postprocessor(tmp_path, monkeypatch):
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
            return 0

    with patch.object(worker.yt_dlp, "YoutubeDL", FakeYDL):
        result = worker.download(row, None, "yt:eR_RNVgewSc")

    assert result is None
    assert FakeYDL.captured["format"] == "bestaudio/best"
    assert FakeYDL.captured["postprocessors"][0]["key"] == "FFmpegExtractAudio"
    assert "merge_output_format" not in FakeYDL.captured


def test_youtube_video_download_requests_video_plus_audio_and_mp4(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "MUSIC_DIR", str(tmp_path))
    row = {
        "source_url": "https://www.youtube.com/watch?v=eR_RNVgewSc",
        "source_mode": "single",
        "artists": "YouTube",
        "album": "YouTube",
        "title": "Video",
        "download_folder": "",
        "download_type": "video",
        "source_type": "youtube",
        "download_format": "mp4",
        "download_quality": "1080",
        "video_codec": "h264",
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
            return 0

    with patch.object(worker.yt_dlp, "YoutubeDL", FakeYDL):
        result = worker.download(row, None, "yt:eR_RNVgewSc")

    assert result is None
    opts = FakeYDL.captured
    assert "bestvideo" in opts["format"]
    assert "bestaudio" in opts["format"]
    assert "height<=1080" in opts["format"]
    assert "ext=mp4" in opts["format"]
    assert opts["merge_output_format"] == "mp4"
    assert not any(
        pp.get("key") == "FFmpegExtractAudio" for pp in opts.get("postprocessors", [])
    )

def test_edited_title_is_applied_to_filename_after_download(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "MUSIC_DIR", str(tmp_path))
    media = tmp_path / "Artist" / "Album" / "Original Title.mp3"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"fake media")

    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE tracks (spotify_id TEXT PRIMARY KEY, title TEXT)")
    c.execute("INSERT INTO tracks(spotify_id,title) VALUES(?,?)", ("yt:test", "Edited Title"))
    c.commit()

    result = worker.rename_download_to_current_title(c, "yt:test", str(media))

    assert result == str(media.parent / "Edited Title.mp3")
    assert (media.parent / "Edited Title.mp3").read_bytes() == b"fake media"
    assert not media.exists()


def test_edited_title_does_not_overwrite_existing_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "MUSIC_DIR", str(tmp_path))
    media = tmp_path / "Original.mp3"
    target = tmp_path / "Edited.mp3"
    media.write_bytes(b"original")
    target.write_bytes(b"existing")

    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE tracks (spotify_id TEXT PRIMARY KEY, title TEXT)")
    c.execute("INSERT INTO tracks(spotify_id,title) VALUES(?,?)", ("yt:test", "Edited"))
    c.commit()

    result = worker.rename_download_to_current_title(c, "yt:test", str(media))

    assert result == str(media)
    assert media.read_bytes() == b"original"
    assert target.read_bytes() == b"existing"
