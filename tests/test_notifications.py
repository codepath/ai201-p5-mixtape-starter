import pytest

from app import create_app, db
from models import User, Song


@pytest.fixture
def app():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })

    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def users(app):
    owner = User(
        username="owner",
        email="owner@example.com",
    )

    rater = User(
        username="rater",
        email="rater@example.com",
    )

    db.session.add_all([owner, rater])
    db.session.commit()

    return owner, rater


@pytest.fixture
def song(app, users):
    owner, rater = users

    song = Song(
        title="Test Song",
        artist="Test Artist",
        shared_by=owner.id,
    )

    db.session.add(song)
    db.session.commit()

    return song


def test_rating_creates_notification_for_song_owner(app, users, song):
    with app.app_context():
        from services.notification_service import rate_song, get_notifications

        owner, rater = users

        before = get_notifications(owner.id)

        rate_song(rater.id, song.id, 4)

        after = get_notifications(owner.id)

        assert len(after) == len(before) + 1
        assert after[0]["type"] == "song_rated"
        assert "rater" in after[0]["body"]
        assert "Test Song" in after[0]["body"]
        assert "4" in after[0]["body"]