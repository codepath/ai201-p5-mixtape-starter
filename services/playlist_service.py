"""
services/playlist_service.py — Mixtape

Handles playlist creation and retrieval logic.
"""

from app import db
from models import Playlist, Song, User, playlist_entries
from sqlalchemy import asc


def create_playlist(name: str, created_by_user_id: str, is_collaborative: bool = True) -> Playlist:
    """
    Create a new playlist.

    Args:
        name: The name of the playlist.
        created_by_user_id: The ID of the user creating the playlist.
        is_collaborative: Whether other users can add songs. Defaults to True.

    Returns:
        The created Playlist instance.
    """
    user = db.session.get(User, created_by_user_id)
    if not user:
        raise ValueError(f"User {created_by_user_id} not found")

    playlist = Playlist(
        name=name,
        created_by=created_by_user_id,
        is_collaborative=is_collaborative,
    )
    db.session.add(playlist)
    db.session.commit()
    return playlist


def get_playlist_songs(playlist_id: str) -> list[dict]:
    """
    Get the ordered list of songs in a playlist.

    Songs are returned in the order they were added (ascending by position).

    Args:
        playlist_id: The ID of the playlist.

    Returns:
        A list of song dicts in playlist order.

    Note:
        This function returns all songs in the playlist.
    """
    playlist = db.session.get(Playlist, playlist_id)
    if not playlist:
        raise ValueError(f"Playlist {playlist_id} not found")

    # Query the songs ordered by their position in the playlist
    songs = (
        db.session.query(Song)
        .join(playlist_entries, Song.id == playlist_entries.c.song_id)
        .filter(playlist_entries.c.playlist_id == playlist_id)
        .order_by(asc(playlist_entries.c.position))
        .all()
    )

    return [song.to_dict() for song in songs]


def get_playlist(playlist_id: str) -> dict:
    """
    Get a playlist's metadata (without songs).

    Args:
        playlist_id: The ID of the playlist.

    Returns:
        A playlist dict.
    """
    playlist = db.session.get(Playlist, playlist_id)
    if not playlist:
        raise ValueError(f"Playlist {playlist_id} not found")
    return playlist.to_dict()


def get_user_playlists(user_id: str) -> list[dict]:
    """
    Get all playlists created by a user.

    Args:
        user_id: The ID of the user.

    Returns:
        A list of playlist dicts.
    """
    playlists = db.session.query(Playlist).filter_by(created_by=user_id).all()
    return [p.to_dict() for p in playlists]
