"""
services/notification_service.py — Mixtape

Handles creating and retrieving notifications.
Notifications are generated when friends interact with a user's shared songs.
"""

from app import db
from models import Notification, Song, User, Rating
from sqlalchemy import desc


def create_notification(user_id: str, notification_type: str, body: str) -> Notification:
    """
    Create a notification for a user.

    Args:
        user_id: The ID of the user who should receive the notification.
        notification_type: A short type string, e.g. 'song_added_to_playlist' or 'song_rated'.
        body: The human-readable notification message.

    Returns:
        The created Notification instance.
    """
    notification = Notification(
        user_id=user_id,
        notification_type=notification_type,
        body=body,
    )
    db.session.add(notification)
    db.session.commit()
    return notification


def add_to_playlist(playlist_id: str, song_id: str, added_by_user_id: str) -> None:
    """
    Record that a user added a song to a playlist, and notify the song's sharer.

    Args:
        playlist_id: The ID of the playlist.
        song_id: The ID of the song being added.
        added_by_user_id: The ID of the user who added the song.
    """
    from models import Playlist
    from services.playlist_service import get_playlist_songs

    song = db.session.get(Song, song_id)
    if not song:
        raise ValueError(f"Song {song_id} not found")

    adder = db.session.get(User, added_by_user_id)
    if not adder:
        raise ValueError(f"User {added_by_user_id} not found")

    playlist = db.session.get(Playlist, playlist_id)
    if not playlist:
        raise ValueError(f"Playlist {playlist_id} not found")

    # Add the song to the playlist
    if song not in playlist.songs:
        playlist.songs.append(song)
        db.session.commit()

    # Notify the person who originally shared the song (if it wasn't them who added it)
    if song.shared_by != added_by_user_id:
        create_notification(
            user_id=song.shared_by,
            notification_type="song_added_to_playlist",
            body=f"{adder.username} added your song '{song.title}' to the playlist '{playlist.name}'.",
        )


def rate_song(user_id: str, song_id: str, score: int) -> Rating:
    """
    Save a user's rating for a song.

    Args:
        user_id: The ID of the user submitting the rating.
        song_id: The ID of the song being rated.
        score: An integer from 1 to 5.

    Returns:
        The created or updated Rating instance.
    """
    if score < 1 or score > 5:
        raise ValueError("Score must be between 1 and 5")

    song = db.session.get(Song, song_id)
    if not song:
        raise ValueError(f"Song {song_id} not found")

    rater = db.session.get(User, user_id)
    if not rater:
        raise ValueError(f"User {user_id} not found")

    # Check if the user has already rated this song
    existing = db.session.query(Rating).filter_by(
        user_id=user_id, song_id=song_id
    ).first()

    if existing:
        existing.score = score
        rating = existing
    else:
        rating = Rating(user_id=user_id, song_id=song_id, score=score)
        db.session.add(rating)

    db.session.commit()

    if song.shared_by != user_id:
        create_notification(
            user_id=song.shared_by,
            notification_type="song_rated",
            body=f"{rater.username} rated your song '{song.title}' {score}/5.",
        )

    return rating


def get_notifications(user_id: str, unread_only: bool = False) -> list[dict]:
    """
    Retrieve notifications for a user.

    Args:
        user_id: The ID of the user.
        unread_only: If True, return only unread notifications.

    Returns:
        A list of notification dicts, ordered by most recent first.
    """
    query = db.session.query(Notification).filter_by(user_id=user_id)
    if unread_only:
        query = query.filter_by(read=False)
    notifications = query.order_by(desc(Notification.created_at)).all()
    return [n.to_dict() for n in notifications]


def mark_as_read(notification_id: str) -> None:
    """
    Mark a notification as read.

    Args:
        notification_id: The ID of the notification to mark read.
    """
    notification = db.session.get(Notification, notification_id)
    if not notification:
        raise ValueError(f"Notification {notification_id} not found")
    notification.read = True
    db.session.commit()
