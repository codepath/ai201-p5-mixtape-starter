"""
tests/test_feed.py — Mixtape

Tests for the "Friends Listening Now" feed logic (RECENT_THRESHOLD).
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


def test_friend_within_threshold_counts_as_listening_now(app):
    """A friend who listened within RECENT_THRESHOLD (3 min) should show up."""
    with app.app_context():
        me = User(username="me", email="me@example.com")
        friend = User(username="friend", email="friend@example.com")
        db.session.add_all([me, friend])
        db.session.flush()

        db.session.execute(friendships.insert().values(user_id=me.id, friend_id=friend.id))

        song = Song(title="Now Playing", artist="Someone", shared_by=friend.id)
        db.session.add(song)
        db.session.flush()

        now = datetime.now(timezone.utc)
        db.session.add(ListeningEvent(user_id=friend.id, song_id=song.id, listened_at=now - timedelta(minutes=1)))
        db.session.commit()

        result = get_friends_listening_now(me.id)
        assert len(result) == 1
        assert result[0]["friend"]["username"] == "friend"


def test_friend_outside_threshold_is_excluded(app):
    """
    Regression test for Issue #2: a friend who listened 23 hours ago
    should NOT count as "currently listening" now that RECENT_THRESHOLD
    is 3 minutes instead of 24 hours.
    """
    with app.app_context():
        me = User(username="me2", email="me2@example.com")
        ghost = User(username="ghost", email="ghost@example.com")
        db.session.add_all([me, ghost])
        db.session.flush()

        db.session.execute(friendships.insert().values(user_id=me.id, friend_id=ghost.id))

        song = Song(title="Golden Hour", artist="Someone", shared_by=ghost.id)
        db.session.add(song)
        db.session.flush()

        now = datetime.now(timezone.utc)
        db.session.add(ListeningEvent(user_id=ghost.id, song_id=song.id, listened_at=now - timedelta(hours=23)))
        db.session.commit()

        result = get_friends_listening_now(me.id)
        assert result == []
