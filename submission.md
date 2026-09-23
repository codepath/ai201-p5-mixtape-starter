
## AI Usage Section

My AI tool for this project was Claude, which helped with a lot of aspects of this project. The main uses for Claude were tracing the data flow through the whole project - it helped me mentally map how routes and services connected and what functions lived in each file - and explaining how the models/database were used to pull the stored info for each part of the app. Another very useful aspect was verifying my fixes by generating scripts that tested scenarios seed_data.py didn't cover. Overall, Claude did not point me in the wrong direction, but it did sometimes go further than I asked - for example, surfacing an unrelated bug while doing a side-effect check, or writing test scripts in a separate scratch folder instead of the project's tests folder - and I had to redirect it back to the specific task. I also caught an inaccuracy from an earlier session: when Claude first found the RECENT_THRESHOLD = timedelta(hours=24) bug, it mentioned seeing a comment in the file saying the threshold was supposed to be 30 minutes, which I noted down in my own write-up. When I asked about that same comment later, Claude searched feed_service.py directly and found no such comment exists anywhere in the file's history - the earlier claim wasn't accurate, and I had to go back and correct the wording in my submission.

## Codebase map


- main files
# models.py 
Defines the different databases for User, Tag, Song, ListeningEvent, Rating, Playlist, Notification

    Data flow to add a song to a playlist : 
    
    user pulls up playlist "POST /playlists/<playlist_id>/songs"

    then routes/playlists.py calls add_song(playlist_id)

    then services/notification_service.py calls add_to_playlist(playlist_id, song_id, added_by)

    Adding a song to playlist calls two functions which in the end adds a song to a playlist and creates a notification to alert the original sharer

# seed_data.py
Creates a testing environment by creating multiple users with different friendships, some have music currently listening and test playlist and test streaks.

# app.py
Starts up the flask app and registers blueprints for songs, playlists, users and feed

- routes

# songs.py
four functions/gatways
    search() -> GET /search?q -> search_songs(q) 
        - Searches for songs, 400 if missing
    
    get_song_detail(song_id) -> GET /<song_id> -> get_song(id) 
        - gets song details, returns 404 if not found

    rate(song_id) -> POST /<song_id>/rate -> rate_song(user_id, song_id, score)
        - takes in a user_id and a score to run function

    listen(song_id) -> POST /<song_id>/listen -> record_listening_event(user_id, song_id) 
        - Saves listen to a song and updates streak if applicable to song
# feed.py
two functions/gateways
    listening_now(user_id) -> GET /<user_id>/listening-now -> get_friends_listening_now(user_id)
        - friends who listened in the last 24h, 404 if user missing

    activity(user_id) -> GET /<user_id>/activity -> get_activity_feed(user_id)
        - the 20 most recent friend listening events

# playlists.py
four functions
    create() -> POST / -> create_playlist(name, created_by, is_collaborative)
        - makes a playlist, 400 if name or created_by missing

    get_detail(playlist_id) -> GET /<playlist_id> -> get_playlist(id)
        - playlist metadata only

    get_songs(playlist_id) -> GET /<playlist_id>/songs -> get_playlist_songs(id)
        - all songs in the playlist

    add_song(playlist_id) -> POST /<playlist_id>/songs -> add_to_playlist(playlist_id, song_id, added_by)
        - adds a song and notifies the original sharer

# users.py
four functions
    get_user(user_id) -> GET /<user_id>
        - returns user info, 404 if missing (reads the DB directly)

    streak(user_id) -> GET /<user_id>/streak -> get_streak(user_id)
        - current listening streak

    notifications(user_id) -> GET /<user_id>/notifications -> get_notifications(user_id, unread_only)
        - lists notifications, reads ?unread_only=true

    read_notification(notification_id) -> POST /notifications/<notification_id>/read -> mark_as_read(id)
        - marks one notification read

- Services

# feed_service.py
two functions
    get_friends_listening_now(user_id) - shows friends who listen to music in the last 24hr (newest first with no dupes)

    get_activity_feed(user_id, limit = 20) - shows the 20 most recent friend listening events

# notification_service.py
five functions
    create_notification(user_id, notification_type, body) - makes a notitfication

    add_to_playlist(playlist_id, song_id, added_by_user_id) - adds a song to playlist and sends a notification to person who originally shared it

    rate_song(user_id, song_id, score) - make a rating from 1-5 on a song

    get_notifications(user_id, unread_only=False) - get all of a users notifications, newest to olders

    mark_as_read(notification_id) - just to mark a read notification
# playlist_service.py
four functions
    create_playlist(name, created_by_user_id, is_collaborative=True) - makes a new playlist

    get_playlist_songs(playlist_id) - returns all the songs in a playlist in order

    get_playlist(playlist_id) - metadata for playlist, does not return songs

    get_user_playlists(user_id) - returns all playlist created by one user
# search_service.py
two functions
    search_songs(query) - used to find title/artisit

    get_song(song_id) - returns song from matching id

# streak_service.py
three functions
    record_listening_event(user_id, song_id) - to log a song listen and to add to the streak

    update_listening_streak(user, now) - streak logic which always begins at 1 and adds 1 everyday, while missing one day resets it

    get_streak(user_id) - returns the streak count of a specific user



## pattern

A pattern i noticed is how every step validates if a song or user is present in the database session when doing any action



## Bug Reproduction

- Issue #2 - Friends Listening Now Shows People from yesterday

    # reproduce bug
    python -m flask --app "app:create_app" run --port 5055 --no-debugger --no-reload

    discover a user id (darius shares "Block Party")
    curl -s "http://localhost:5055/songs/search?q=Block%20Party"

    read his "listening now" feed
    curl -s "http://localhost:5055/feed/<darius_id>/listening-now"

    Result = 
    count = 2
    simone   Still Waters      listened   0.26 hours ago   <- genuinely "now"
    nova     Midnight Drive    listened   2.01 hours ago   <- stale

    Add a "ghost" who listened to music 23 hour ago. The seed data only had 2 hours as maximum amount in the past, A script was used to create the new user

    GET /feed/<nova>/listening-now
    count = 4
    darius   Midnight Drive     0.19h ago
    simone   Still Waters       0.27h ago
    kenji    First Light        0.35h ago
    ghost    Golden Hour       23.00h ago   <-- YESTERDAY


    # How I found the route cause
    
    Using the seed_data.py only made users have a maximum of 2 hours in the "listening now" feed. Already thats way more than "now" so claude made a test to see the Edge case for what no longer counts as "listening now". 23 Hours still counted, 
    Looking at feed.py and then feed_service.py showed an issue with the recent threshold of the feed_service.py file, the recent threshold was set too 24 hours for the whole file.
    
    
    # The Root Cause
    The user_id's who have music in "Listening now" shows the bug, that music listened to in the past 24 hours count as "Currently Listening". RECENT_THRESHOLD was set to timedelta(hours=24), which is far too long a window to represent someone "currently" listening
    # Fix and side effect check

        RECENT_THRESHOLD = timedelta(hours=24)
        
        ->

        RECENT_THRESHOLD = timedelta(hours=0.05)

    Narrowing the window to 3 minutes means the cutoff filter (listened_at >= cutoff)
    only matches events from the last few minutes, so a 23-hour-old event like the
    ghost's no longer passes the filter and is correctly excluded.

    Side effect check - get_activity_feed after changing RECENT_THRESHOLD

    RECENT_THRESHOLD is used by the whole file, but get_activity_feed is documented as
    not filtered by recency, so it shouldn't be affected at all. Verified by seeding one event 3 days old and one
    1 minute old, then confirming get_activity_feed still returns both,
    newest-first, and that `limit` is respected:

    RECENT_THRESHOLD is currently: 0:03:00
    activity feed returned: ['New Song', 'Old Song']
    PASS: get_activity_feed still returns old + new events, newest-first, respects limit
        -> unaffected by the RECENT_THRESHOLD change (as expected, it never reads that constant)


    get_activity_feed behavior is not affected by the change and currently_listening is alot more accurate on time

    tests/test_feed.py::test_friend_within_threshold_counts_as_listening_now PASSED

    tests/test_feed.py::test_friend_outside_threshold_is_excluded PASSED

    These tests were to check that a friend listening to a song 1 minute ago does show up in "currently listening" opposed to the second test of someone with 23 hours



- Issue #4 - I get notified when a friend added my song to a playlist but not when they rated it

    # reproduce bug
    darius rates nova's song "Midnight Drive" 5 stars

    BEFORE: nova's notifications
    curl -s "http://localhost:5055/users/<nova>/notifications"
    count = 1
        - song_added_to_playlist | darius added your song 'Midnight Drive' to the playlist 'Late Night Vibes'

        POST the rating
    curl -s -X POST "http://localhost:5055/songs/<song>/rate" \
    -H "Content-Type: application/json" -d '{"user_id":"<darius>","score":5}'
    -> HTTP 201    (rating saved successfully)

        AFTER: nova's notifications — unchanged
    curl -s "http://localhost:5055/users/<nova>/notifications"
    count = 1
        - song_added_to_playlist | darius added your song 'Midnight Drive' to the playlist 'Late Night Vibes'


    # How I found the route clause

    from songs.py the rate route goes too notification services where two similar functions catches your attention rate_song and add_to_playlist. These are similar because after they complete their goal of either rating a song or adding a song to a playlist, they are both supposed to notify the sharer of the song. Only add_to_playlist completes this action with lines 65-69 which rate_song does not have any similar code in the function


    # The Root Cause
    When a user adds a song that was shared by someone else, the function to send the notification gets called in the end of add_to_playlist. But when a user rates a song, the method create_notification isnt called in the rate_song function to send the notification to the original sharer. Futhermore the rate_song function has no mention of the added_by_user_id to recognize the orignal sharer thats present in the add_to_playlist function

    # Fix and side effect check

     N/A

     ->    

    if song.shared_by != user_id:
        create_notification(
            user_id=song.shared_by,
            notification_type="song_rated",
            body=f"{rater.username} rated your song '{song.title}' {score} stars.",
    )
    
    Side effect check - add_to_playlist in notification_service.py

    I checked add_to_playlist since its in the same file as rate_song and in real world
    use they would often go in sequence of each other (add a song, then rate it). Since
    rate_song now calls create_notification (the same helper add_to_playlist uses), I
    checked that add_to_playlist's own notification behavior wasn't affected by the change.

    Note: while writing this check I found a separate, pre-existing bug in
    add_to_playlist unrelated to Issue #4 - playlist.songs.append(song) never
    sets the NOT NULL position/added_by columns on playlist_entries, so the real
    POST /playlists/<id>/songs route would 500 for anyone. seed_data.py never
    actually calls add_to_playlist (it inserts playlist_entries directly), which
    is why this was never caught. Left this for a separate fix/issue - to check
    JUST the notification logic here, I pre-inserted the playlist entries
    directly (same technique seed_data.py uses) so add_to_playlist's
    `if song not in playlist.songs` check is True and it skips the buggy append.

    Verified:
    1. add_to_playlist still notifies the original sharer when a friend adds
    their song.
    2. Adding your own song still does not self-notify.
    3. add_to_playlist and rate_song notifications coexist independently for
    the same song/sharer - one doesn't overwrite or interfere with the other.

    PASS: add_to_playlist still notifies the sharer -> darius added your song 'Midnight Drive' to the playlist 'Late Night Vibes'.
    PASS: adding your own song does not self-notify
    PASS: add_to_playlist and rate_song notifications coexist independently -> ['song_rated', 'song_added_to_playlist']

    Both functions are working as intended and send notifications to the user when the action is fufilled. Fixing rate_song did not affect add_to_playlist



- Issue #5 - The last song in a playlist never shows up

    # reproduce bug
    Bug: get_playlist_songs drops the last song (returns 4 of 5)
    pytest tests/test_playlists.py::test_playlist_returns_all_songs -v

    FAILED tests/test_playlists.py::test_playlist_returns_all_songs - AssertionError: assert 4 == 5

    Playlist has 5 songs saved to it. Running pytest showed the playlist contained 5 songs
    in the database but get_playlist_songs only returned 4 - the songs were saving
    correctly, so the bug had to be in how they were being returned.


    # How I found the root cause

    Opened playlist_service.py and checked the query in get_playlist_songs - the join and
    order_by(asc(position)) looked correct, so I ruled out the DB layer. The confidence
    moment was noticing the return line explicitly slices the list with songs[:-1] before
    converting to dicts, which explains why the result was always exactly one song short.
    Any playlist with more than 1 song shows this bug.


    # The Root Cause
    The return part of the function slices the list with [:-1]. This causes the last song not to be returned, changing to song in songs should accurately iterate through the whole list

    # Fix and side effect check

    return [song.to_dict() for song in songs[:-1]]

    ->

    return [song.to_dict() for song in songs]



    Side effect check - I checked to see if returning a playlist with one song will accurately return the song or an empty list, the function still returned the appropiate amount of songs

    ```python
    """Manual side-effect check: playlist with exactly 1 song."""
    from app import create_app, db
    from models import User, Song, Playlist, playlist_entries
    from services.playlist_service import get_playlist_songs

    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()

        user = User(username="soloDj", email="solo@example.com")
        db.session.add(user)
        db.session.flush()

        song = Song(title="Only Track", artist="Solo Artist", shared_by=user.id)
        db.session.add(song)
        db.session.flush()

        playlist = Playlist(name="One Song Playlist", created_by=user.id)
        db.session.add(playlist)
        db.session.flush()

        db.session.execute(
            playlist_entries.insert().values(
                playlist_id=playlist.id,
                song_id=song.id,
                position=1,
                added_by=user.id,
            )
        )
        db.session.commit()

        songs = get_playlist_songs(playlist.id)
        print(f"songs returned: {len(songs)}")
        assert len(songs) == 1, "1-song playlist should return 1 song, not 0"
        print("PASS: single-song playlist returns the song")
    ```

