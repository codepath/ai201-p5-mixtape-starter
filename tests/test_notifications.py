"""
tests/test_notifications.py — Mixtape

Tests for rating-triggered notification behavior.
"""

import pytest
from app import create_app, db
from models import User, Song, Rating
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


def test_rate_song_creates_notification_for_sharer(app):
    """Rating someone else's song should notify the song sharer."""
    with app.app_context():
        sharer = User(username="owner", email="owner@example.com")
        rater = User(username="friend", email="friend@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()

        song = Song(title="Signal Fire", artist="Arc Lines", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()

        rating = rate_song(rater.id, song.id, 5)

        notifications = get_notifications(sharer.id)
        assert len(notifications) == 1
        assert notifications[0]["type"] == "song_rated"
        assert "friend" in notifications[0]["body"]
        assert "Signal Fire" in notifications[0]["body"]
        assert "5/5" in notifications[0]["body"]

        saved_rating = db.session.get(Rating, rating.id)
        assert saved_rating is not None
        assert saved_rating.score == 5


def test_rate_song_self_rating_does_not_notify(app):
    """Rating your own song should not generate a notification to yourself."""
    with app.app_context():
        sharer = User(username="owner", email="owner@example.com")
        db.session.add(sharer)
        db.session.flush()

        song = Song(title="Solo Track", artist="Owner", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()

        rate_song(sharer.id, song.id, 4)

        notifications = get_notifications(sharer.id)
        assert notifications == []
