# Project 5: Mixtape Bug Hunt — Submission

**Branch:** `bugfix/mixtape`
**Bugs fixed:** #1 (listening streak), #2 (Friends Listening Now), #5 (playlist last song)

---

## AI Usage

I used an AI coding assistant (Claude) primarily for **codebase orientation and hypothesis-checking**, not for finding bugs blind.

**Where AI helped:**
- **File summaries during the mapping phase.** I had it summarize each `services/` file so I could build the codebase map faster — "what is this module responsible for, and what does each function return." This was reliable because it was describing code I could see, not diagnosing anything.
- **Confirming Python date semantics.** Once I had narrowed Issue #1 to a date comparison, I asked it to confirm that `datetime.weekday()` returns `6` for Sunday (vs. `isoweekday()` returning `7`). That let me name the exact off-by-one in the guard condition instead of hand-waving "the Sunday logic is wrong."
- **Explaining SQLAlchemy result de-duplication.** When investigating Issue #3 (search duplicates), the search tests *passed* on this machine. AI explained that SQLAlchemy 2.0's legacy `Query.all()` de-duplicates single-entity ORM results by identity, which is why the `outerjoin` on `song_tags` doesn't actually produce visible duplicates in this version. **I verified this myself** by checking the installed version (`2.0.51`) and running the search directly — the duplicates never materialized. That's *why I chose not to submit Issue #3*: I couldn't reproduce it in this environment, and the debugging discipline here is "don't fix what you can't reproduce."

**Where I had to override / verify the AI:**
- For Issue #2 the AI's first instinct was that the bug was in the de-duplication loop. Reading the code myself showed the loop is correct — it's `RECENT_THRESHOLD = timedelta(hours=24)` that's wrong. I confirmed by running `get_friends_listening_now` against seeded data and printing "minutes ago" per friend: darius's feed showed nova at **121 minutes ago**. The loop was fine; the window was the bug.
- Every reproduction and every fix was verified by running the actual code (pytest + direct service calls against the seeded DB), not by trusting an explanation.

The workflow that worked: **read the code → form a hypothesis → verify by running it with controlled inputs → fix.** AI was a fast explainer of code I'd already located; it was not a substitute for reading the call chain.

---

## Codebase Map

*(Written during orientation, before fixing anything.)*

### Main files and their responsibilities

- **`app.py`** — Flask application factory (`create_app`). Configures the SQLite DB (`mixtape.db`), initializes `db = SQLAlchemy()`, registers four blueprints under URL prefixes (`/songs`, `/playlists`, `/users`, `/feed`), and calls `db.create_all()`. `create_app` is used both by the server and by tests (which pass an in-memory SQLite config). This is why the app must be started as `FLASK_APP=app:create_app flask run` — running `python app.py` re-imports the module and double-registers `db`.

- **`models.py`** — Defines **6 SQLAlchemy models** and **3 association tables**:
  - Models: `User`, `Tag`, `Song`, `ListeningEvent`, `Rating`, `Playlist`, `Notification`.
  - `friendships` — self-referential many-to-many on `User`, stored **bidirectionally** (each friendship is inserted twice in the seed, once in each direction), exposed via the `User.friends` dynamic relationship.
  - `song_tags` — many-to-many between `Song` and `Tag`.
  - `playlist_entries` — many-to-many between `Playlist` and `Song` **with an explicit `position` column**, plus `added_by` and `added_at`. Songs in a playlist have an explicit ordinal position, not just insertion order.
  - Notable: a rating is its own `Rating` model with a `UniqueConstraint(user_id, song_id)` — a user can rate a song once (re-rating updates the score). `ListeningEvent` is an append-only log of listens; the `User.listening_streak` / `last_listened_at` columns are the derived streak state.

- **`routes/`** — Thin HTTP layer. Every route parses input, delegates to exactly one service function, and formats the JSON response. No business logic lives here.
  - `songs.py` — `/songs/search`, `/songs/<id>`, `POST /songs/<id>/rate`, `POST /songs/<id>/listen`.
  - `playlists.py` — create, get metadata, `GET /<id>/songs`, `POST /<id>/songs`.
  - `users.py` — user profile, `/<id>/streak`, `/<id>/notifications`, mark-read.
  - `feed.py` — `/<id>/listening-now`, `/<id>/activity`.

- **`services/`** — All business logic. One file per domain: `streak_service`, `feed_service`, `search_service`, `notification_service`, `playlist_service`. This is where all five bugs live.

- **`seed_data.py`** — Drops and recreates the DB, then loads 5 users with friendships, 13 songs (grouped as 0-tag / 1-tag / 3+-tag to expose the search issue), 3 playlists, listening events (a recent cluster ~10–20 min ago and an older cluster 2–58 h ago), streaks, and one sample playlist-add notification.

- **`tests/`** — `test_streaks.py`, `test_search.py`, `test_playlists.py`. Each uses an in-memory DB fixture. On a clean checkout, 3 tests fail (streak-on-Sunday and the two playlist tests); the search tests pass on SQLAlchemy 2.0 (see Issue #3 note in AI Usage).

### Architectural patterns I noticed

1. **Route → single service call → response.** Routes never touch the ORM directly (except a trivial `db.session.get` in `users.py`). Business logic is fully in `services/`.
2. **Services commit their own transactions.** Each mutating service function calls `db.session.commit()` itself rather than deferring to the caller.
3. **Notifications are a cross-service side effect.** `notification_service` is imported *by* other flows (e.g. `add_to_playlist` lives there and calls `create_notification`), and only notifies the song's original sharer, and only when the actor ≠ the sharer.
4. **Timestamps are UTC and sometimes tz-naive after a round-trip.** SQLite stores naive datetimes, so services re-attach `timezone.utc` when comparing (see `streak_service`).

### Data flow — a user rates a song (and why no notification fires today)

`POST /songs/<song_id>/rate` (with `{user_id, score}`) → `routes/songs.py:rate()` parses the body → calls `notification_service.rate_song(user_id, song_id, score)`. `rate_song` validates the 1–5 range, finds the song and rater, then either updates the existing `Rating` or inserts a new one, and commits. **It returns the `Rating` and stops there** — unlike its sibling `add_to_playlist` in the same file, it never calls `create_notification`. That asymmetry is exactly Issue #4: adding a song to a playlist notifies the sharer, but rating it does not.

### Data flow — recording a listen updates the streak

`POST /songs/<song_id>/listen` → `routes/songs.py:listen()` → `streak_service.record_listening_event()`. That inserts a `ListeningEvent`, then calls `update_listening_streak(user, now)`, which compares `now.date()` to `last_listened_at.date()`: same day → no change; exactly 1 day → increment; otherwise → reset to 1. It then persists `last_listened_at = now`.

---

## Root Cause Analysis

### Issue #1 — "My listening streak keeps resetting"

**How I reproduced it.** The repo ships a failing test, `test_streak_increments_on_sunday`, that listens on Saturday 2024-06-15 then Sunday 2024-06-16 and expects the streak to reach 2. Running `pytest tests/test_streaks.py` showed it asserting `1 == 2`. I also confirmed the seed sets `darius.last_listened_at` to "yesterday," so a listen today would exercise the consecutive-day branch.

**How I found the root cause.** Route → service trace: `POST /songs/<id>/listen` → `record_listening_event` → `update_listening_streak`. Reading `update_listening_streak`, the only place a consecutive day is handled is line 73. The condition was `days_since_last == 1 and today.weekday() != 6`. The moment I was confident: I confirmed (and double-checked with AI) that `datetime.weekday()` returns `6` for **Sunday** — so the guard specifically excludes Sundays from the increment path.

**The root cause.** `datetime.weekday()` returns `6` for Sunday. The increment branch required `days_since_last == 1 AND today.weekday() != 6`. So whenever "today" was a Sunday, a legitimate consecutive-day listen failed that condition and fell through to the `else` branch, which resets the streak to 1. The streak silently reset every Sunday regardless of whether the user had actually skipped a day. There was no product reason for a weekday to matter at all — streaks are about consecutive calendar days.

**My fix and side-effect check.** I removed the spurious `and today.weekday() != 6`, leaving `elif days_since_last == 1:`. Side effects checked: the other three streak branches (first-ever listen → 1, same-day → no change, gap > 1 day → reset) are untouched and their tests still pass. Full suite: 13/13 pass. I confirmed the fix doesn't over-increment (same-day test still yields 1) and correctly resets across a skipped day (Wednesday-after-Monday test still yields 1).

---

### Issue #2 — "Friends Listening Now shows people from yesterday"

**How I reproduced it.** After `python seed_data.py`, I called `get_friends_listening_now` for `darius` and printed each friend's "minutes ago." darius's feed listed **nova at 121 minutes ago** alongside simone at 16 minutes ago. Someone who listened two hours ago should not count as listening *now*.

**How I found the root cause.** Route → service: `GET /feed/<id>/listening-now` → `feed_service.get_friends_listening_now`. My first hypothesis (and the AI's) was the de-dup loop, but reading it showed it correctly keeps only each friend's most recent event. The determining line is the cutoff: `cutoff = now - RECENT_THRESHOLD`, with `RECENT_THRESHOLD = timedelta(hours=24)` at module top. A 24-hour window is not "now."

**The root cause.** `RECENT_THRESHOLD` was `timedelta(hours=24)`, so the "listening now" query admitted any event within the last full day. A friend whose most recent listen was hours ago (nova, 2 h) still passed the `listened_at >= cutoff` filter and appeared as currently listening — which reads as "people from yesterday." The seed data is explicitly built around a ~30-minute real-time window (a recent cluster at 10–20 min and an older cluster at 2–58 h).

**My fix and side-effect check.** Changed `RECENT_THRESHOLD` to `timedelta(minutes=30)`. Re-running: darius now sees only simone (16 min), and nova (121 min) correctly drops off; nova — whose three friends all listened within 20 min — still sees all three. Side-effect check: `get_activity_feed` in the same file is intentionally *not* time-filtered (its docstring says so) and shares none of this constant, so it's unaffected. No test covers this service, so I verified behaviorally against seeded data on both sides of the 30-minute boundary.

---

### Issue #5 — "The last song in a playlist never shows up"

**How I reproduced it.** `test_playlist_returns_all_songs` seeds a 5-song playlist and asserts `len == 5`; it failed with `4`. `test_playlist_returns_songs_in_order` expected `Track 1..5` and got `Track 1..4`. Consistently, exactly the highest-position song was missing.

**How I found the root cause.** Route → service: `GET /playlists/<id>/songs` → `playlist_service.get_playlist_songs`. The query itself is correct — it joins `playlist_entries`, filters by playlist, and orders ascending by `position`. The bug is the very last expression: `return [song.to_dict() for song in songs[:-1]]`. The `[:-1]` slice drops the final element of an already-correct, position-ordered list.

**The root cause.** `songs[:-1]` slices off the last element of the ordered result. Because the list is ordered ascending by `position`, the dropped element is always the highest-position (last) song. The query returned all N songs; the slice discarded one before serialization — so callers always saw N-1 songs and the final one "never showed up."

**My fix and side-effect check.** Changed the return to `[song.to_dict() for song in songs]`. Both playlist tests now pass (5 songs, correct order), and the empty-playlist test still returns `[]` (previously `[][:-1]` also happened to be `[]`, so that case was never broken — but the fix keeps it correct). Full suite: 13/13. I checked the only caller path (the route just wraps the list and counts it) — nothing else depended on the truncated length.

---

## Bugs I chose *not* to fix (and why)

- **Issue #3 (search duplicates)** — The `outerjoin` on `song_tags` in `search_service` can produce duplicate rows for multi-tag songs, but on the installed **SQLAlchemy 2.0.51**, legacy `Query.all()` de-duplicates single-entity ORM results by identity, so the duplicates never surface. All search tests pass and I could not reproduce the reported symptom in this environment. Per the "reproduce before fixing" discipline, I left it. (A defensive fix would be `.distinct()` or dropping the unused join.)
- **Issue #4 (no notification on rating)** — Real and clear: `rate_song` never calls `create_notification`, unlike `add_to_playlist`. I documented it in the codebase map but scoped my submission to the three bugs above.

---

## Commit history

```
$ git log --oneline
cb57ca8 fix: include the last song when listing playlist songs
8ff714b fix: narrow Friends Listening Now window to 30 minutes
8d8e69a fix: increment listening streak on Sundays
2dfdeaa Add .gitignore file and update README with setup instructions
7b64551 initial commit
```

*(Screenshot of `git log --oneline` to be attached in the Course Portal submission.)*
