"""
tests/test_feed.py — Mixtape

Tests for the "Friends Listening Now" feed.
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


@pytest.fixture
def friends(app):
    """A user with two friends: one listened today, one only late last night."""
    with app.app_context():
        me = User(username="nova", email="nova@example.com")
        today_friend = User(username="simone", email="simone@example.com")
        yesterday_friend = User(username="darius", email="darius@example.com")
        db.session.add_all([me, today_friend, yesterday_friend])
        db.session.flush()

        for friend in (today_friend, yesterday_friend):
            db.session.execute(friendships.insert().values(user_id=me.id, friend_id=friend.id))

        song = Song(title="Night Song", artist="Various", shared_by=me.id)
        db.session.add(song)
        db.session.flush()

        now = datetime.now(timezone.utc)
        start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        db.session.add_all([
            ListeningEvent(user_id=today_friend.id, song_id=song.id, listened_at=now),
            # 23:59 yesterday — always within the last 24 hours, but not today
            ListeningEvent(user_id=yesterday_friend.id, song_id=song.id,
                           listened_at=start_of_today - timedelta(minutes=1)),
        ])
        db.session.commit()
        yield {"me": me}


def test_listening_now_excludes_friends_who_last_listened_yesterday(app, friends):
    """Only friends who listened today should appear (regression test for Issue #2)."""
    with app.app_context():
        feed = get_friends_listening_now(friends["me"].id)
        usernames = [entry["friend"]["username"] for entry in feed]
        assert usernames == ["simone"]
