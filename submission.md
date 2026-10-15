# Mixtape Bug Hunt — Submission

**Branch:** `bugfix/mixtape`

## AI Usage

_(To be completed at the end of the project.)_

---

## Codebase Map

### Files and responsibilities

| File | Responsibility |
|------|----------------|
| `app.py` | Flask **app factory**. `create_app(config)` builds the app, configures SQLAlchemy (SQLite at `instance/mixtape.db` by default, overridable via `config` — tests pass `sqlite:///:memory:`), registers the four blueprints under `/songs`, `/playlists`, `/users`, `/feed`, and runs `db.create_all()`. The module-level `db = SQLAlchemy()` is imported by every other module. Must be run with `FLASK_APP=app:create_app flask run`; running `python app.py` imports the module twice (`__main__` and `app`) and creates two `db` objects. |
| `models.py` | 7 SQLAlchemy models — `User`, `Tag`, `Song`, `ListeningEvent`, `Rating`, `Playlist`, `Notification` — plus 3 association tables: `friendships` (User↔User, stored as two directed rows per friendship), `song_tags` (Song↔Tag), and `playlist_entries` (Playlist↔Song, with extra `position`, `added_by`, and `added_at` columns so playlist order is explicit). All primary keys are UUID strings. `Rating` has a unique constraint on `(user_id, song_id)`, so each user has at most one rating per song. Each model has a `to_dict()` used for JSON responses; `Song.to_dict()` reads tags through the `Song.tags` relationship. `User` also caches streak state directly in `listening_streak` and `last_listened_at`. |
| `routes/songs.py` | `GET /songs/search?q=` → `search_service.search_songs`; `GET /songs/<id>` → `search_service.get_song`; `POST /songs/<id>/rate` → `notification_service.rate_song`; `POST /songs/<id>/listen` → `streak_service.record_listening_event`. |
| `routes/playlists.py` | `POST /playlists/` → `playlist_service.create_playlist`; `GET /playlists/<id>` → `get_playlist`; `GET /playlists/<id>/songs` → `get_playlist_songs`; `POST /playlists/<id>/songs` → `notification_service.add_to_playlist`. |
| `routes/users.py` | `GET /users/<id>` (reads the model directly); `GET /users/<id>/streak` → `streak_service.get_streak`; `GET /users/<id>/notifications` → `notification_service.get_notifications`; `POST /users/notifications/<id>/read` → `mark_as_read`. |
| `routes/feed.py` | `GET /feed/<id>/listening-now` → `feed_service.get_friends_listening_now`; `GET /feed/<id>/activity` → `get_activity_feed`. |
| `services/streak_service.py` | Records `ListeningEvent`s and maintains the cached streak on `User`. `update_listening_streak(user, now)` compares today's calendar date to the date of `last_listened_at` and decides whether to keep, increment, or reset the streak. `get_streak` just reads the cached value. |
| `services/feed_service.py` | Friend feeds. `get_friends_listening_now` gets the user's friend IDs, queries their `ListeningEvent`s newer than a cutoff time, and keeps only the most recent event per friend. `get_activity_feed` returns the latest N friend events with no time filter. |
| `services/search_service.py` | `search_songs` runs a case-insensitive `ILIKE` match on `Song.title` or `Song.artist` with the SQLAlchemy 2.0 `select()` API; `get_song` fetches one song by ID. |
| `services/notification_service.py` | Owns notifications **and** the two "social" actions that can trigger them: `add_to_playlist` and `rate_song`. `create_notification(user_id, type, body)` is the shared helper that inserts and commits a `Notification`. `get_notifications` / `mark_as_read` read and update them. |
| `services/playlist_service.py` | Playlist CRUD: `create_playlist`, `get_playlist` (metadata only), `get_user_playlists`, and `get_playlist_songs`, which joins `Song` to `playlist_entries` and orders by `position`. |
| `seed_data.py` | Drops and recreates the DB, then inserts 5 users (nova, darius, simone, kenji, aaliya), friendships (nova↔darius/simone/kenji, darius↔simone, kenji↔aaliya), 10 tags, 13 songs with 0, 1, or 3 tags, listening events from a few minutes to ~2.5 days old, 3 playlists of 7 songs each ("Late Night Vibes", "Friday Energy", "Study Mode"), and one `song_added_to_playlist` notification. |
| `tests/` | pytest suites for streaks, search, and playlists. Each uses `create_app` with an in-memory SQLite DB, so tests never touch `mixtape.db`. |

### Data flow — a user rates a song

1. Client sends `POST /songs/<song_id>/rate` with JSON `{"user_id": ..., "score": ...}`.
2. `rate()` in `routes/songs.py` checks that both fields are present (400 if not), casts `score` to `int`, and calls `rate_song(user_id, song_id, score)` in `services/notification_service.py`.
3. `rate_song` checks that the score is 1–5, then loads the `Song` and the rating `User` with `db.session.get` and raises `ValueError` if either is missing.
4. It looks up an existing `Rating` for that `(user_id, song_id)` pair. If one exists it updates `score`; otherwise it creates a new `Rating`. This follows the model's unique constraint. Then it commits.
5. The route returns `rating.to_dict()` with status 201. Any `ValueError` from the service becomes a 400 with `{"error": ...}`.

### Data flow — a user views a playlist

`GET /playlists/<id>/songs` → `get_songs()` in `routes/playlists.py` → `get_playlist_songs(playlist_id)` in `services/playlist_service.py`. The service loads the `Playlist` (raises `ValueError` → 404 if missing), queries `Song` joined to `playlist_entries` filtered by `playlist_id` and ordered by `position` ascending, and returns a list of `song.to_dict()`. The route wraps it as `{"songs": [...], "count": n}`.

### Patterns I noticed

- **Thin routes, logic in services.** Every route only parses input, calls one service function, and formats JSON. Errors cross the boundary as `ValueError`, which routes turn into 400/404.
- **Services are grouped by side effect, not by entity.** `add_to_playlist` and `rate_song` live in `notification_service.py` rather than the playlist or songs service, because both are actions that can notify a song's sharer.
- **Notification pattern:** an action finishes its own DB write, then calls `create_notification(...)` for the song's `shared_by` user, skipping self-actions.
- **Denormalized streak.** The streak is stored on `User` and updated when a listen is recorded, not recomputed from `ListeningEvent` history.
- **All timestamps are UTC** (`datetime.now(timezone.utc)`). SQLite stores them without a timezone, so code reading them back re-attaches UTC (see `update_listening_streak`).
- Services call `db.session.commit()` themselves rather than leaving it to the route.

---

## Root Cause Analyses

### **Issue #5 — The last song in a playlist never shows up**

**How I reproduced it.** After running `seed_data.py`, I counted the rows for "Friday Energy" directly in the database (`SELECT count(*) FROM playlist_entries WHERE playlist_id=…` → **7**). Then I called `GET /playlists/<friday_energy_id>/songs`, which returned `"count": 6`. The missing song was "Harlem Renaissance", which has `position = 7`, the highest. The other two seeded playlists showed the same 7 → 6 drop. The existing tests `test_playlist_returns_all_songs` and `test_playlist_returns_songs_in_order` also failed (5-song playlist → 4 returned, `Track 5` missing).

**How I found the root cause.** I started at the route `get_songs()` in `routes/playlists.py`. It only calls `get_playlist_songs()` and returns `len(songs)` as the count, so the count and the list come from the same place. In `services/playlist_service.py`, I checked the query first: the join on `playlist_entries`, the filter on `playlist_id`, and `order_by(position ASC)` are all correct. That told me the database was returning all 7 rows. The last line, `return [song.to_dict() for song in songs[:-1]]`, was the moment I was sure: the data is correct until the very last step, and only then does a song disappear.

**The root cause.** `get_playlist_songs()` documents that it "returns all songs in the playlist," and its query does fetch all of them, sorted by `position` ascending. The return statement then slices the result with `songs[:-1]`, which in Python means "every element except the last one." Because the list is sorted by position, the dropped element is always the song with the highest position, which is the most recently added one. That explains the reporter's exact symptom: adding a new song makes the previously hidden song no longer last, so it reappears, and the new song is hidden instead.

**My fix.** I changed `songs[:-1]` to `songs` so the function returns the full query result. This matches its documented contract. The query and ordering were already correct, so nothing else needed to change.

**Side-effect check.**
- `pytest tests/test_playlists.py` passes 3/3, including the two tests that were failing and `test_empty_playlist_returns_empty_list`.
- Through the API, all three seeded playlists now return 7 of 7 songs in `position` order.
- A newly created empty playlist returns `{"count": 0, "songs": []}`, and an unknown playlist ID still returns 404.
- `get_playlist()` (metadata) and `add_to_playlist()` don't use this function's return value for anything except membership, so they are unaffected.

### **Issue #3 — The same song keeps showing up twice in search**

**How I reproduced it.** With the seeded database, `GET /songs/search?q=Anthem` returned `"count": 3` and listed "Crown Heights Anthem" three times. To find the condition that triggers it, I searched for songs with different tag counts: "Midnight Drive" (0 tags) and "Block Party" (1 tag) each came back once, while every 3-tag song (Crown Heights Anthem, Harlem Renaissance, After Hours) came back three times. So the number of copies equals the number of tags, with a minimum of 1. The existing test `test_search_no_duplicates_multi_tag_song` also failed (3 instead of 1), while the 0-tag and 1-tag tests passed.

**How I found the root cause.** Route `search()` in `routes/songs.py` → `search_songs()` in `services/search_service.py`. The query is `select(Song).outerjoin(song_tags, …).filter(title ILIKE … OR artist ILIKE …)`. The "copies = tag count" pattern pointed straight at the join to `song_tags`. I checked whether the join was used anywhere: the filter only references `Song.title` and `Song.artist`, and the `tags` in each result come from `Song.to_dict()` reading the `Song.tags` relationship, not from the joined rows. `git log` showed the previous commit (`dc87e8c`) converted this query from the legacy `db.session.query(Song)` API to 2.0-style `select()`. That explained why the duplicates appeared: the legacy `Query` automatically de-duplicates full ORM entities, but `session.execute(select(Song)).scalars().all()` returns one object per SQL row.

**The root cause.** `search_songs()` outer-joined `Song` to the `song_tags` association table even though nothing in the query needed tag data. A join returns one row for every (song, tag) pair, so a song with three tags produced three rows. Songs with zero tags (kept by the *outer* join) or one tag produced a single row, which is why only some songs were duplicated. `select(...).scalars().all()` does not collapse identical entities, so each extra row became a repeated song in the response.

**My fix.** I removed the unneeded `.outerjoin(song_tags, …)`, so the query selects from `song` alone and returns one row per matching song. I also removed the now-unused `Tag` and `song_tags` imports. Removing the join fixes the actual cause. Adding `.distinct()` would only hide the extra rows that the join creates.

**Side-effect check.**
- `pytest tests/test_search.py` passes 5/5 (no-duplicate tests for 0, 1, and 3 tags, plus "returns matching songs" and "empty for no match").
- Through the API, "Anthem", "Harlem", and "After" each return 1 result, and each result still includes its full `tags` list (e.g. `['rap', 'hip-hop', 'boom bap']`), so tag display is unaffected.
- The broad query `q=a` returns all 13 songs with no repeats, `q=ZZZ` returns `[]`, and a missing `q` still returns 400.
- `get_song()` in the same file doesn't use the join and is unchanged.

### **Issue #1 — My listening streak keeps resetting**

**How I reproduced it.** The bug only happens on Sundays, so I couldn't wait for the clock. I isolated `update_listening_streak(user, now)` in a small Python script that builds an in-memory `User` with `listening_streak=12` and passes in controlled datetimes (October 2026: Sat the 10th, Sun the 11th):

| Transition | `today.weekday()` | Streak 12 → |
|---|---|---|
| Fri 20:00 → Sat 09:00 | 5 | 13 ✅ |
| **Sat 22:00 → Sun 09:00** | **6** | **1** ❌ |
| Sun 22:00 → Mon 09:00 | 0 | 13 ✅ |
| Thu → Sat (skipped a day) | 5 | 1 ✅ |

This matches kenji's report exactly: the streak only breaks when the second day is a Sunday, and it counts normally again on Monday. The existing test `test_streak_increments_on_sunday` also failed (`assert 1 == 2`).

**How I found the root cause.** `GET /users/<id>/streak` → `streak()` in `routes/users.py` → `get_streak()`, which only reads the stored `User.listening_streak`. So the bad value had to be *written* somewhere else. I searched for writers of `listening_streak` and found `update_listening_streak()`, called from `record_listening_event()` (the `POST /songs/<id>/listen` path). I compared its branches to the rules in its own docstring ("If the user listened yesterday: streak increments by 1"). The increment branch is `elif days_since_last == 1 and today.weekday() != 6:`. The docstring says nothing about weekdays, and in Python `date.weekday()` numbers Monday=0 … **Sunday=6**. That extra clause is false only on Sundays, which matched the "always a Sunday" pattern. The table above confirmed it.

**The root cause.** In `update_listening_streak()` in `services/streak_service.py`, the "listened yesterday → increment" branch had an extra condition, `and today.weekday() != 6`. When today is Sunday, that condition is false, so even with `days_since_last == 1` the code skipped the increment and fell through to the `else` branch, which resets the streak to 1 (the rule meant for "more than one day has passed"). Every user who listened Saturday and then Sunday lost their streak.

**My fix.** I removed the `and today.weekday() != 6` clause, so the branch is just `elif days_since_last == 1:`. Now the code matches the documented rules: same day → no change, exactly one calendar day later → +1, more than one day → reset to 1. The decision now depends only on the number of days between listens, not on which day of the week it is.

**Side-effect check.**
- `pytest tests/test_streaks.py` passes 5/5: first listen → 1, consecutive days increment, a skipped day resets, the same day doesn't double-count, and Sat → Sun increments (previously failing).
- I reran my repro script on both sides of the Sunday boundary: Sat→Sun and Sun→Mon both go 12→13, and a skipped day still resets to 1.
- Through the live API: kenji (already listened today) stays at 12 after another `POST /songs/<id>/listen`, and darius (listened yesterday) goes 3 → 4.

### **Issue #2 — Friends Listening Now shows people from yesterday**

**How I reproduced it.** In the seeded data, every user has a listen from the last 30 minutes, so I needed a friend whose *only* listen was the previous evening. kenji ↔ aaliya are friends, so I deleted aaliya's listening events and inserted one at **2026-10-12 23:00 UTC** (yesterday evening). At 21:55 UTC on 2026-10-13, `GET /feed/<kenji_id>/listening-now` returned aaliya "listening now" with `listened_at: 2026-10-12T23:00:00`. This is the same situation as darius in nova's report: a listen from last night still showing the next day.

**How I found the root cause.** Route `listening_now()` in `routes/feed.py` → `get_friends_listening_now()` in `services/feed_service.py`. The function gets the friend IDs, queries `ListeningEvent`s with `listened_at >= cutoff`, and de-duplicates to one event per friend. The friend lookup and de-duplication matched the docstring, so I focused on `cutoff`. It is `datetime.now(timezone.utc) - RECENT_THRESHOLD`, with `RECENT_THRESHOLD = timedelta(hours=24)` at the top of the file. Doing the math confirmed it: 21:55 today minus 24h = 21:55 yesterday, and 23:00 yesterday is after that, so it passes the filter. The reporter's phrase "keeps hanging around … until the same time the next day" describes a rolling 24-hour window exactly.

**The root cause.** `get_friends_listening_now()` decided "recent" with a **rolling 24-hour window** (`now - 24h`), but the feature's intended behavior is "friends who have listened **today**", a calendar-day boundary. Any listen from yesterday that happened later in the day than the current time-of-day is still less than 24 hours old, so it passed the `listened_at >= cutoff` filter. An 11pm listen therefore stayed in the feed all of the next day until 11pm.

**My fix.** I changed the cutoff to the start of the current day: `datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)`, and removed the now-unused `RECENT_THRESHOLD` constant. "Today" is defined in UTC because the app stores all timestamps in UTC and the streak logic in `streak_service.py` uses the same UTC calendar-day boundary, so "listened today" now means the same thing for both features.

**Side-effect check.**
- **Regression test:** I added `tests/test_feed.py::test_listening_now_excludes_friends_who_last_listened_yesterday`. A friend who listened at 23:59 yesterday (always within 24h) must be excluded, and a friend who listened just now must be included. It **fails on the original code** (`['simone', 'darius'] != ['simone']`) and passes with the fix.
- Live API: aaliya (last listen yesterday 23:00) no longer appears in kenji's feed. nova's feed still shows darius, kenji, and simone, who all listened today, newest first with one entry per friend.
- `get_activity_feed()` (`/feed/<id>/activity`) never used the cutoff and still returns all recent friend events (10 for nova).
- Known tradeoff: with a UTC day boundary, a user in a US timezone sees the feed "reset" at 7–8pm local time. Making it per-user would need a timezone field on `User`, which is out of scope for a targeted fix.

### **Issue #4 — I got notified when a friend added my song to a playlist but not when they rated it**

**How I reproduced it.** With the seeded database, nova's `GET /users/<nova_id>/notifications` showed 1 notification (the seeded `song_added_to_playlist` one). I had kenji rate one of nova's shared songs with `POST /songs/<song_id>/rate` `{"user_id": <kenji_id>, "score": 5}`. The response was 201 with the saved `Rating`, so the rating worked. Fetching nova's notifications again still showed **1**. No rating notification was created, which matches aaliya's report.

**How I found the root cause.** Following the hint to compare the working notification with the missing one, I traced both from their routes. `POST /playlists/<id>/songs` → `add_to_playlist()` and `POST /songs/<id>/rate` → `rate_song()` both live in `services/notification_service.py`. Comparing them step by step:

| Step | `add_to_playlist` | `rate_song` |
|---|---|---|
| Validate & load song / user | ✅ | ✅ |
| Write the change and commit | ✅ append to `playlist.songs` | ✅ create/update `Rating` |
| `if song.shared_by != actor: create_notification(...)` | ✅ | **❌ missing** |

`rate_song` ends with `db.session.commit()` and `return rating`. Nothing in the function calls `create_notification`. To make sure there wasn't a notification created elsewhere (in the route, or some hook), I searched the project for `create_notification(` and `song_rated`. The only call is in `add_to_playlist`, and `'song_rated'` appears only as an example in `create_notification`'s docstring. A notification type that is documented but never created confirmed this was the cause.

**The root cause.** Notifications in Mixtape aren't triggered automatically (no database triggers or event listeners). Each action function has to explicitly call `create_notification()` for the song's sharer after it saves its own change. `add_to_playlist()` does this. `rate_song()` saves the `Rating` and returns without ever calling `create_notification()`. So ratings were stored correctly, but no `Notification` row was ever written, and `get_notifications()` had nothing to return. This affected every user and every rating, which matches "ratings notifications seem to just not happen, for anyone."

**My fix.** After the rating is committed, `rate_song()` now follows the same pattern as `add_to_playlist()`: if `song.shared_by != user_id`, it calls `create_notification(user_id=song.shared_by, notification_type="song_rated", body=f"{rater.username} rated your song '{song.title}' {score} out of 5.")`. The type string matches the one already named in `create_notification`'s docstring. The self-action check means users aren't notified about rating their own songs, the same rule the playlist notification uses.

**Side-effect check.**
- **Regression tests:** I added `tests/test_notifications.py`. `test_rating_notifies_song_sharer` **fails on the original code** and passes with the fix. `test_rating_own_song_does_not_notify` checks the self-rating case.
- Live API: kenji rating nova's song → nova's count goes 1 → 2, with the newest entry `song_rated: "kenji rated your song 'Midnight Drive' 4 out of 5."` nova rating her own song → no new notification. A score of 9 → 400 "Score must be between 1 and 5", and no notification is created, because validation runs before anything is written.
- The existing `song_added_to_playlist` notification is still listed, and `?unread_only=true` still filters correctly.
- Rating storage is unchanged: re-rating still updates the single existing `Rating` (unique `(user_id, song_id)`), so the 201 response is the same as before.

