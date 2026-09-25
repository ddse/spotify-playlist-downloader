from worker.worker import normalize_youtube_url


def test_normalize_youtube_single_watch_url_removes_playlist_radio_params():
    source = 'https://www.youtube.com/watch?v=eR_RNVgewSc&list=RDeR_RNVgewSc&start_radio=1'
    assert normalize_youtube_url(source) == 'https://www.youtube.com/watch?v=eR_RNVgewSc'


def test_normalize_youtube_preserves_timestamp_for_single_watch_url():
    source = 'https://www.youtube.com/watch?v=eR_RNVgewSc&list=RDeR_RNVgewSc&t=42s&start_radio=1'
    assert normalize_youtube_url(source) == 'https://www.youtube.com/watch?v=eR_RNVgewSc&t=42s'


def test_normalize_youtube_does_not_strip_playlist_mode():
    source = 'https://www.youtube.com/watch?v=eR_RNVgewSc&list=RDeR_RNVgewSc&start_radio=1'
    assert normalize_youtube_url(source, 'playlist') == source