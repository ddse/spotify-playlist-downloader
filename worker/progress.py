def clear_download_progress(c, track_id):
    """Mark a finished yt-dlp transfer while clearing transient stats."""
    c.execute(
        """
        UPDATE tracks
        SET progress=?,
            download_speed=?,
            eta=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE spotify_id=?
        """,
        (99, "", "", track_id),
    )
