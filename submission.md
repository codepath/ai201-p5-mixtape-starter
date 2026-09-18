# Project 5: Mixtape Bug Hunt — Submission

**Branch:** `bugfix/mixtape`

Bugs fixed (3): **Issue #1** (streak resets on Sunday), **Issue #2** (Friends
Listening Now shows people from hours ago), **Issue #5** (last song in a playlist
never shows up).

### Commit history (`git log --oneline`)

![git log --oneline on bugfix/mixtape](images/git-log-oneline.png)

Three separate `fix:` commits (one per bug) plus the pre-work `docs:` commit that
adds this codebase map, all on the `bugfix/mixtape` branch.

---

## AI Usage

I used an AI coding assistant (Claude) throughout navigation, reproduction, and
debugging. What that actually looked like, honestly:

**Codebase orientation.** I had the assistant read each `services/` file and the
routes that call them and summarize the responsibility of each module and the
route → service call chain. This is where AI is strongest — it built the first
draft of the codebase map below quickly and accurately, because the code was
small enough to hold in full context. I verified every claim by reading the file
myself (e.g., confirming that `to_dict()` pulls tags via the `song.tags`
relationship, not the join in `search_songs`).

**Reproduction over guessing.** Before touching any code I ran the existing test
suite (`pytest tests/`) and a throwaway script that called each suspect service
function directly under an app context and printed what it returned. This is the
step where AI would have led me wrong if I'd trusted it blindly — see below.

**Where AI was wrong / incomplete — Issue #3.** The single most useful thing I
did was *not* believe the AI (or the seed-data comments, or the test docstrings)
about Issue #3. Every surface signal said the `outerjoin(song_tags)` in
`search_songs` would return a song with 3 tags three times. The AI agreed with
that reading. But when I actually ran `search_songs("Crown Heights")` and ran
`pytest tests/test_search.py`, the multi-tag song came back **exactly once** and
all search tests **passed**. The reason: SQLAlchemy's ORM deduplicates
full-entity query results by primary-key identity, so `query(Song).outerjoin(...)`
never yields duplicate `Song` objects even though the underlying SQL rows fan
out. The join is dead weight, but it is not an observable bug. Because I could
not reproduce Issue #3, I dropped it and picked a bug I *could* trigger, exactly
as the brief instructs. (This is documented more fully under "Issues investigated
but not fixed.")

**Where I used AI to confirm a diagnosis I'd already formed.** For Issue #1 I had
already narrowed the problem to the `today.weekday() != 6` guard; I asked the AI
to confirm the Python convention (`datetime.weekday()`: Monday=0 … Sunday=6, vs
`isoweekday()`: Monday=1 … Sunday=7) so my root-cause wording was precise. I
verified it in a REPL rather than taking its word.

The pattern that worked: **I** found the suspicious line by tracing route →
service and running the code; the **AI** explained mechanics and drafted prose;
**I** verified every diagnosis by running it with controlled inputs before
writing a fix. Asking the AI to "find the bug" cold (as with Issue #3) produced a
confident, plausible, wrong answer.

---

## Codebase Map

Mixtape is a small Flask + SQLAlchemy JSON API. Every HTTP route is a thin
wrapper that parses input, calls exactly one service function, and formats the
response; **all business logic lives in `services/`**. That is the dominant
architectural pattern — the routes never touch the ORM directly except
`routes/users.py`'s trivial `get_user` lookup.

### Main files and their responsibilities

- **`app.py`** — Flask application factory (`create_app`). Creates the shared
  `SQLAlchemy` instance `db`, configures the SQLite URI, registers the four
  blueprints under `/songs`, `/playlists`, `/users`, `/feed`, and calls
  `db.create_all()`. Tests inject an in-memory SQLite URI via the `config` arg.

- **`models.py`** — 6 SQLAlchemy models plus 3 association tables:
  - `User` (has `listening_streak`, `last_listened_at`, and a self-referential
    many-to-many `friends` relationship via the `friendships` table),
  - `Song` (title/artist/album/genre, `shared_by` FK to the sharer),
  - `Tag`, `ListeningEvent` (user listened to a song at a time),
  - `Rating` (1–5 score, unique per user+song),
  - `Playlist`, `Notification` (typed message for a recipient user).
  - Association tables: `friendships` (symmetric, stored as two directed rows),
    `song_tags`, and **`playlist_entries`** — a join table that carries an
    explicit **`position`** column, so songs in a playlist have an ordered
    position, not just insertion order (this is the table Issue #5 lives in).

- **`routes/songs.py`** — `GET /songs/search?q=`, `GET /songs/<id>`,
  `POST /songs/<id>/rate`, `POST /songs/<id>/listen`. Delegates to
  `search_service`, `notification_service.rate_song`, and
  `streak_service.record_listening_event`.

- **`routes/playlists.py`** — create playlist, `GET /playlists/<id>`,
  `GET /playlists/<id>/songs`, `POST /playlists/<id>/songs`. Delegates to
  `playlist_service` and `notification_service.add_to_playlist`.

- **`routes/users.py`** — user profile, `GET /users/<id>/streak`,
  notifications list, mark-notification-read.

- **`routes/feed.py`** — `GET /feed/<id>/listening-now` and
  `GET /feed/<id>/activity`, both delegating to `feed_service`.

- **`services/streak_service.py`** — `record_listening_event` (creates a
  `ListeningEvent` and calls `update_listening_streak`), and the pure function
  `update_listening_streak(user, now)` that mutates the streak counter.
  **(Issue #1)**

- **`services/feed_service.py`** — `get_friends_listening_now` (friends' events
  newer than `RECENT_THRESHOLD`, deduped to one most-recent event per friend) and
  `get_activity_feed` (most recent N events, no recency filter). **(Issue #2)**

- **`services/search_service.py`** — `search_songs` (title/artist ILIKE match)
  and `get_song`. **(Issue #3 — investigated, does not reproduce)**

- **`services/notification_service.py`** — `create_notification`,
  `add_to_playlist` (adds a song and notifies the sharer), `rate_song` (saves a
  rating — **but does not notify anyone**, Issue #4), `get_notifications`,
  `mark_as_read`.

- **`services/playlist_service.py`** — `create_playlist`, `get_playlist_songs`
  (ordered by `position`), `get_playlist`, `get_user_playlists`. **(Issue #5)**

- **`seed_data.py`** — drops/recreates the DB and inserts 5 users with
  friendships, 13 songs (0/1/3-tag variants), 3 playlists, recent + older
  listening events, and one playlist-add notification. The seed data is
  deliberately shaped to expose the bugs (e.g. recent events <30 min old vs.
  older events hours/days old for the listening-now window).

### Data flow trace #1 — how rating a song *should* trigger a notification

`POST /songs/<song_id>/rate` (`routes/songs.py:29`) parses `user_id` + `score`
→ calls `notification_service.rate_song(user_id, song_id, score)` → that function
validates the score, upserts a `Rating` row (respecting the unique
`user_id+song_id` constraint), and commits. **The notification for the song's
original sharer is never created** — compare `add_to_playlist` in the same file,
which *does* call `create_notification(user_id=song.shared_by, ...)`. There is no
separate rating-notification model; a notification is just a `Notification` row
with `notification_type="song_rated"`. This asymmetry is Issue #4.

### Data flow trace #2 — how a listening event updates a streak

`POST /songs/<song_id>/listen` (`routes/songs.py:43`) →
`streak_service.record_listening_event(user_id, song_id)` creates a
`ListeningEvent(listened_at=now)` and calls `update_listening_streak(user, now)`.
That pure function compares `now.date()` to `user.last_listened_at.date()`:
0 days → no change, 1 day → increment, >1 day → reset to 1. The streak counter and
`last_listened_at` live directly on the `User` row; there is no separate streak
table. The day-boundary comparison in this function is Issue #1.

### Patterns I noticed

1. **Thin routes, fat services.** Every route delegates immediately; business
   logic and all DB access are in `services/`. To debug any endpoint you trace
   route → the one service function it calls.
2. **Service functions own their own commits.** Each mutating service function
   calls `db.session.commit()` itself rather than the route doing it.
3. **State lives on the parent row, not in a side table** where it's a scalar:
   streak on `User`, no rating-count on `Song`. Ordered/relational state uses
   association tables (`playlist_entries.position`).
4. **Seed data is adversarial by design** — it plants exactly the edge-case rows
   (multi-tag songs, hours-old vs. minutes-old events, a 7-song playlist) needed
   to trigger each reported bug.

---

## Root Cause Analysis

### Issue #1 — My listening streak keeps resetting

**How I reproduced it.** Ran `pytest tests/test_streaks.py`. The test
`test_streak_increments_on_sunday` failed with `assert 1 == 2`: it records a
listen on Saturday 2024-06-15 (streak → 1), then Sunday 2024-06-16, and expects
the streak to increment to 2, but it reset to 1. I confirmed by hand that the
trigger is specifically **the second listen landing on a Sunday**, one calendar
day after the previous listen — no other weekday reproduces it.

**How I found the root cause.** Trace: `POST /songs/<id>/listen`
(`routes/songs.py:43`) → `record_listening_event` → `update_listening_streak`
(`streak_service.py:42`). Reading that function, every branch looked right
*except* the increment guard on line 73:
`elif days_since_last == 1 and today.weekday() != 6:`. The moment I was sure was
seeing `!= 6` — I confirmed in a REPL that Python's `datetime.weekday()` returns
**6 for Sunday** (Mon=0 … Sun=6). So the extra condition specifically excludes
Sundays from the "consecutive day" increment path.

**The root cause.** `datetime.weekday()` returns 6 for Sunday. The increment
branch required `days_since_last == 1 AND today.weekday() != 6`, so when the
current listen fell on a Sunday, the "listened yesterday" case failed its guard
and control fell through to the `else` branch, which resets the streak to 1. The
weekday check has no legitimate purpose — a streak is about *consecutive calendar
days* and does not care which day of the week it is — so any user who listened
Saturday then Sunday (a normal continuation) had their streak silently wiped
every Sunday.

**My fix and side-effect check.** I removed the `and today.weekday() != 6`
clause, leaving `elif days_since_last == 1:` to increment on any consecutive day
(`streak_service.py:73`). I checked both sides of the day boundary: all 5 streak
tests pass — Sunday now increments (`test_streak_increments_on_sunday`), a
genuinely skipped day still resets (`test_streak_resets_after_skipped_day`,
Mon→Wed → 1), same-day double listens still don't double count, and new users
still start at 1. No other code reads `weekday()`, so nothing else is affected.

---

### Issue #2 — Friends Listening Now shows people from yesterday

**How I reproduced it.** There is no unit test for this feed, so I reproduced it
against seed data. I called `get_friends_listening_now` directly under an app
context for `kenji` and printed how long ago each returned friend last listened.
Result: kenji's "listening now" feed contained **nova, whose only recent listen
was 2 hours ago** — clearly not "now." (nova's own feed correctly showed friends
who listened 10–20 minutes ago.) So the feed was surfacing stale events.

**How I found the root cause.** Trace: `GET /feed/<id>/listening-now`
(`routes/feed.py:9`) → `get_friends_listening_now` (`feed_service.py:16`). The
function builds `cutoff = now - RECENT_THRESHOLD` and returns every friend event
newer than the cutoff (deduped to one per friend). The logic is correct; the
constant is not. At the top of the file: `RECENT_THRESHOLD = timedelta(hours=24)`.
That was the moment it clicked — a 24-hour "recently" window is the entire day,
so anyone who listened at any point in the last day counts as "listening now."

**The root cause.** `RECENT_THRESHOLD` was set to 24 hours. "Friends Listening
Now" is meant to show who is *actively* listening right now, but a 24-hour cutoff
admits events from many hours (up to a full day) earlier. Any friend with a
listen anywhere in the previous 24 hours appeared in the live feed, which is why
kenji saw nova's 2-hour-old event. The seed data encodes exactly this: "recent"
events are <30 min old and "older" events are 2+ hours old and explicitly marked
as ones that should *not* appear.

**My fix and side-effect check.** I changed the constant to
`timedelta(minutes=30)` (`feed_service.py`), matching the seed data's definition
of a genuinely-current listen, and added a comment explaining the intent. I
verified both sides of the boundary after re-seeding: nova's feed still returns
its 3 friends who listened 10/15/20 minutes ago (inside the window), and kenji's
feed now returns 0 — nova's 2-hour-old event is correctly excluded. I checked
that `get_activity_feed` in the same file is unaffected: it deliberately does not
use `RECENT_THRESHOLD` (it returns the most recent N events regardless of age),
so the tightened window does not touch the activity feed. Full test suite shows
no new failures.

---

### Issue #5 — The last song in a playlist never shows up

**How I reproduced it.** Ran `pytest tests/test_playlists.py`:
`test_playlist_returns_all_songs` failed — a 5-song playlist returned 4 songs —
and `test_playlist_returns_songs_in_order` failed showing the list stopped at
`Track 4`. I also confirmed against seed data: the "Late Night Vibes" playlist
has 7 rows in `playlist_entries`, but `get_playlist_songs` returned only 6, and
the missing one was always the **highest-position (last)** song.

**How I found the root cause.** Trace: `GET /playlists/<id>/songs`
(`routes/playlists.py:34`) → `get_playlist_songs` (`playlist_service.py:38`). The
query is correct: it joins `playlist_entries`, filters by playlist, and orders by
`position` ascending. The bug is in the very last line, the return statement:
`return [song.to_dict() for song in songs[:-1]]`. The `[:-1]` slice was the
smoking gun — it drops the final element of an already-correct, correctly-ordered
list. The function's own docstring says "returns **all** songs in the playlist,"
which the slice directly contradicts.

**The root cause.** `songs[:-1]` is a Python slice that returns every element
*except the last one*. Because the songs are ordered by ascending `position`, the
last element is always the highest-position (most recently added) song, so that
one song was silently omitted from every non-empty playlist. It was an
off-by-one truncation in the return value, not a query or ordering problem.

**My fix and side-effect check.** I changed the slice to iterate the full list:
`return [song.to_dict() for song in songs]` (`playlist_service.py:66`). I checked
the boundary on the small-count side too, which is where `[:-1]` is most
dangerous: a 1-song playlist previously returned `[]` (the slice ate the only
song), and now returns that 1 song. The empty-playlist path is unaffected —
`[][:-1]` and `[]` are both empty — and `test_empty_playlist_returns_empty_list`
still passes. All 13 tests pass, including the two playlist tests that previously
failed and the ordering test.

---

## Issues investigated but not fixed

**Issue #3 — "the same song keeps showing up twice in search" — could not
reproduce; not a real bug.** `search_songs` (`search_service.py`) does
`db.session.query(Song).outerjoin(song_tags, ...)`. On paper this fans out: a
song with 3 tags matches 3 rows of `song_tags`, so the SQL returns the song 3
times. The seed-data comments and the test docstrings both assert this causes
duplicates. But when I actually ran `search_songs("Crown Heights")` and
`pytest tests/test_search.py`, the multi-tag song came back **exactly once** and
every search test **passed**. The reason: SQLAlchemy's ORM deduplicates
full-entity results by primary-key identity — `query(Song)...all()` returns
distinct `Song` objects regardless of row fan-out. So the join is unnecessary
(dead code) but produces no observable duplication. Per the brief's guidance
("if you can't reproduce a bug, try a different one"), I did not "fix" a bug that
does not manifest, and chose a reproducible issue instead. If desired, the join
could be removed for clarity, but that is a cleanup, not a bug fix.

**Issue #4 — rating a song sends no notification — real bug, not selected.** I
confirmed this reproduces (`rate_song` commits a `Rating` but never calls
`create_notification`, unlike its sibling `add_to_playlist`). I left it out
because I selected the three boundary-condition issues (#1, #2, #5); it is a
genuine, easily-fixable omission (add a `create_notification(user_id=song.shared_by,
notification_type="song_rated", ...)` call guarded by `song.shared_by != user_id`).
