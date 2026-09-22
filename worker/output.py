from pathlib import Path
import re


MEDIA_EXTENSIONS = {
    '.mp3', '.m4a', '.opus', '.ogg', '.oga', '.wav', '.flac', '.aac',
    '.webm', '.mp4', '.mkv', '.avi', '.mov',
}
EXCLUDED_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.webp', '.gif', '.vtt', '.srt', '.ass',
    '.lrc', '.part', '.ytdl', '.json', '.description', '.txt', '.url', '.meta',
}


def _normalize(value):
    return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())


def resolve_downloaded_file(row, music_dir, since):
    """Return a media file produced by this download, or an empty string.

    The timestamp requirement prevents an old file from being reported as the
    output of a failed/new download. Existing files overwritten by yt-dlp are
    still accepted because their mtime changes during the job.
    """
    music = Path(music_dir).resolve()
    custom = (row['download_folder'] or '').strip()
    if custom:
        folder = (music / custom).resolve()
    else:
        artist = (row['artists'] or 'Unknown Artist').replace('/', '_')
        album = (row['album'] or 'YouTube').replace('/', '_')
        folder = (music / artist / album).resolve()

    if music != folder and music not in folder.parents:
        raise RuntimeError('invalid download folder')
    if not folder.exists():
        return ''

    def valid(path):
        try:
            stat = path.stat()
        except OSError:
            return False
        return (
            path.is_file()
            and path.suffix.lower() in MEDIA_EXTENSIONS
            and path.suffix.lower() not in EXCLUDED_EXTENSIONS
            and stat.st_size >= 1024
            and stat.st_mtime >= since - 2
        )

    files = [p for p in folder.rglob('*') if valid(p)]
    if not files:
        # Custom yt-dlp templates/post-processing can place the final file in
        # another subdirectory. Search the complete music tree, but still
        # require the file to have been created/updated by this job.
        files = [p for p in music.rglob('*') if valid(p)]
    if not files:
        return ''

    title = (row['title'] or 'Unknown Title').replace('/', '_')
    wanted = _normalize(title)
    exactish = [p for p in files if _normalize(p.stem) == wanted]
    candidates = exactish or files
    return str(max(candidates, key=lambda p: p.stat().st_mtime).resolve())


def describe_recent_media(music_dir, since):
    music = Path(music_dir).resolve()
    result = []
    if not music.exists():
        return result
    for path in music.rglob('*'):
        try:
            stat = path.stat()
        except OSError:
            continue
        if (
            path.is_file()
            and path.suffix.lower() in MEDIA_EXTENSIONS
            and stat.st_mtime >= since - 2
        ):
            result.append({
                'path': str(path.resolve()),
                'size': stat.st_size,
                'mtime': stat.st_mtime,
            })
    return sorted(result, key=lambda item: item['mtime'], reverse=True)
