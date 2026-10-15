"""
tests/test_notifications.py — Mixtape

Tests for notifications triggered by friends interacting with shared songs.
"""

import pytest
from app import create_app, db
from models import User, Song, Notification
from services.notification_service import rate_song


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def shared_song(app):
    """aaliya shares a song; kenji is a friend who can rate it."""
    with app.app_context():
        sharer = User(username="aaliya", email="aaliya@example.com")
        rater = User(username="kenji", email="kenji@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()

        song = Song(title="Golden Hour", artist="Solange K", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()
        yield {"sharer": sharer, "rater": rater, "song": song}


def test_rating_notifies_song_sharer(app, shared_song):
    """Rating a friend's song should notify them (regression test for Issue #4)."""
    with app.app_context():
        rate_song(shared_song["rater"].id, shared_song["song"].id, 5)

        notifications = Notification.query.filter_by(user_id=shared_song["sharer"].id).all()
        assert len(notifications) == 1
        assert notifications[0].notification_type == "song_rated"
        assert "kenji" in notifications[0].body


def test_rating_own_song_does_not_notify(app, shared_song):
    """Users should not be notified about rating their own song."""
    with app.app_context():
        rate_song(shared_song["sharer"].id, shared_song["song"].id, 4)

        assert Notification.query.count() == 0
