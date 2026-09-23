# Mixtape Bug Hunt — Submission

## Codebase Map

### Top-level files

- **app.py** — Flask application factory (`create_app`). Initializes `SQLAlchemy` (`db`), registers four blueprints (`songs`, `playlists`, `users`, `feed`) under their respective URL prefixes, and calls `db.create_all()` on startup.
- **models.py** — Defines all SQLAlchemy models: `User`, `Tag`, `Song`, `ListeningEvent`, `Rating`, `Playlist`, `Notification`. Also defines three association tables: `friendships` (symmetric many-to-many between users), `song_tags` (many-to-many between songs and tags), and `playlist_entries` (many-to-many between playlists and songs, but with an explicit `position` column — songs in a playlist have an ordering, not just insertion order).
- **seed_data.py** — Populates the database with 5 users and their friendships, 10 tags, 13 songs (deliberately split into songs with 0, 1, and 3+ tags), a mix of recent and older listening events, 3 playlists, and one working notification. The varying tag counts on songs are clearly there to expose the search duplication bug.

### routes/ — thin controllers

Every route file follows the same pattern: parse the request, call a service function, format the response as JSON. No business logic lives in routes.

- **songs.py** → calls `search_service.search_songs` / `get_song`, `notification_service.rate_song`, `streak_service.record_listening_event`
- **playlists.py** → calls `playlist_service.create_playlist` / `get_playlist_songs` / `get_playlist`, `notification_service.add_to_playlist`
- **users.py** → calls `streak_service.get_streak`, `notification_service.get_notifications` / `mark_as_read`
- **feed.py** → calls `feed_service.get_friends_listening_now` / `get_activity_feed`

### services/ — where the business logic (and the bugs) live

- **streak_service.py** — `record_listening_event` creates a `ListeningEvent` row and calls `update_listening_streak`, which compares `user.last_listened_at.date()` to `today` to decide whether to increment, hold, or reset the streak.
- **feed_service.py** — `get_friends_listening_now` filters listening events to a 24-hour `RECENT_THRESHOLD` window, then deduplicates so only the most recent song per friend is shown.
- **search_service.py** — `search_songs` does an `outerjoin` against `song_tags` and filters by title/artist match, then calls `.to_dict()` on each row returned by the query.
- **notification_service.py** — `add_to_playlist` creates a `Notification` after adding a song to a playlist. `rate_song` saves a `Rating` but has no corresponding call to `create_notification` anywhere in the function.
- **playlist_service.py** — `get_playlist_songs` queries songs ordered by `position`, then returns `songs[:-1]` before mapping to dicts.

### Pattern I noticed

Every route delegates immediately to a service function — routes only parse input and shape the response; all logic and DB writes happen in `services/`. The `to_dict()` method on each model is the single serialization point, so any bug visible in JSON output usually traces back to either the query logic in a service function or a relationship defined in `models.py`.

### Data flow trace: user rates a song

1. Client sends `POST /songs/<song_id>/rate` with `user_id` and `score` in the JSON body.
2. `routes/songs.py::rate()` parses the body, validates that `user_id` and `score` are present, then calls `notification_service.rate_song(user_id, song_id, score)`.
3. `rate_song()` validates the score is between 1 and 5, confirms the song and user exist, checks whether a `Rating` already exists for that user/song pair (there's a unique constraint on `user_id` + `song_id`), then either updates the existing `Rating` or creates a new one, and commits.
4. The route serializes the returned `Rating` object back to the client as JSON.

Notably, `rate_song()` never calls `create_notification()`, unlike the parallel `add_to_playlist()` function in the same file, which explicitly notifies the song's original sharer after modifying the playlist. This asymmetry is the shape of Issue #4 — the rating is saved correctly, but no notification is ever generated for it.

---

## AI Usage

I used Claude throughout this project primarily for codebase navigation, tracing execution flow, and verifying hypotheses before committing to a fix — not for generating fixes blind.

**Codebase orientation (Milestone 1):** I had Claude read through `app.py`, `models.py`, the `routes/` layer, and all five `services/` files, then summarize each file's responsibility and trace one full data flow (a user rating a song, end to end from the route through the service layer). This gave me a working mental model of the app's structure — routes as thin controllers, all logic in services — before I opened a single issue.

**Reproduction (Milestone 2):** For each bug, Claude helped me design a reproduction strategy suited to that bug's specific trigger condition — a controlled `flask shell` session with fixed datetimes for the date-dependent bugs (#1, #2), and live HTTP requests for the bugs that reproduced naturally against seed data (#5, #4). This mattered most for Issue #3: my first few reproduction attempts showed the bug _not_ occurring, and rather than assuming I'd made a mistake, we worked through several layers of verification together (checking a fresh shell session to rule out identity-map caching, inspecting the raw SQL join at the column level versus the full-entity level, and finally cross-checking against the repo's own pre-written test for this exact scenario). That process showed the underlying code smell (missing `.distinct()`) is real, but the bug doesn't manifest as user-visible duplication with the installed library versions — an honest finding I wouldn't have trusted without that layered verification, and I swapped Issue #3 out for Issue #2 as a result.

**Investigation and fixing (Milestone 3):** For each bug, I had Claude compare the buggy function against the codebase's own working precedent where one existed (e.g., comparing `rate_song` against the working `add_to_playlist` line-by-line for Issue #4, which was flagged as an architectural gap rather than a typo). I verified each proposed root cause against the actual code myself before applying any fix, and verified every fix afterward by re-running the reproduction and the relevant test suite (or live HTTP checks, where no test file existed, as with the feed and notification services).

**Where I had to verify or correct something:** During Issue #2's reproduction, an early result showed a listening event's timestamp shifted by exactly one day between insertion and read-back. Rather than accepting that at face value, we traced it down to leftover test data from an earlier `flask shell` session (a stray event with a mismatched timestamp, from mixing timezone-aware and naive datetimes) rather than a real storage bug — cleaning that up and re-running with consistent naive datetimes gave a trustworthy result. This was a good reminder that shell-based reproduction can introduce its own noise, and that verifying what's actually stored in the database (rather than trusting a single derived output) is worth the extra step.

---

## Root Cause Analysis Entries

_(Full 5-field entries to be completed per bug in Milestone 3. Reproduction notes captured below while fresh, from Milestone 2.)_

### Issue #1 — My listening streak keeps resetting

**How I reproduced it:** In `flask shell`, called `update_listening_streak(user, saturday)` followed by `update_listening_streak(user, sunday)` using fixed `datetime` objects for a real consecutive Saturday/Sunday pair, bypassing the system clock so the exact dates were controlled. After the Saturday call the streak was 1; after the Sunday call (a consecutive day) the streak dropped back to 1 instead of incrementing to 2.

**How I found the root cause:** Navigated from the failing behavior straight to `streak_service.py`, since it's the only file that touches `listening_streak`. Read `update_listening_streak` top to bottom and found the three-branch conditional that decides whether to increment, hold, or reset the streak. The `elif` branch included an extra condition, `today.weekday() != 6`, that had no explanation in the docstring or comments. Checked what `datetime.weekday()` returns for a known Sunday date (`6`) and confirmed that was the exact day the bug reports named.

**The root cause:** The streak-increment branch was `elif days_since_last == 1 and today.weekday() != 6`. Python's `datetime.weekday()` returns `6` for Sunday (Monday=0 through Sunday=6). Because the condition explicitly excluded weekday `6`, any listen that fell on a Sunday and was otherwise a valid consecutive-day listen (`days_since_last == 1`) failed the `and` check and fell through to the `else` branch, which unconditionally reset the streak to 1. There is no legitimate reason a calendar weekday should affect whether a streak increments — the only two things that should matter are whether it's a new day and whether exactly one day has passed since the last listen.

**My fix and side-effect check:** Removed `and today.weekday() != 6` from the `elif` condition, leaving `elif days_since_last == 1:`. This is the smallest possible change that removes the erroneous weekday exclusion while leaving the same-day and skipped-day branches untouched. Verified by re-running the reproduction (Saturday → 1, Sunday → 2, as expected) and by running the full `test_streaks.py` suite: all 5 tests pass, including `test_streak_increments_on_sunday` (written to catch this exact bug) and the four pre-existing tests for new users, consecutive days, same-day no-double-count, and skipped-day resets, confirming no adjacent streak behavior was broken.

### Issue #2 — Friends Listening Now shows people from yesterday

**How I reproduced it:** In `flask shell`, inserted a `ListeningEvent` for a friend (darius) timestamped at 11pm the previous calendar day, using naive UTC datetimes to avoid timezone/storage round-trip issues. Called `get_friends_listening_now(nova_id)` using the real current time (afternoon of the next day). Darius appeared in the feed with yesterday's timestamp, even though he had not listened at all on the current calendar day.

**How I found the root cause:** Went straight to `feed_service.py` since it's the only file behind the `/feed/<user_id>/listening-now` route. Found the module-level constant `RECENT_THRESHOLD = timedelta(hours=24)` and the line computing `cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD`. Recognized this as a rolling time window rather than a calendar-day boundary, and confirmed by reproduction that an event from just under 24 hours ago (but from the previous calendar date) still passed the `listened_at >= cutoff` filter.

**The root cause:** The "listening now" cutoff was computed as "current time minus 24 hours," a rolling window, instead of "the start of today," a calendar-day boundary. The issue report explicitly expects only friends who listened _today_ to appear. Because the cutoff was rolling, any listen within the past 24 hours qualified even if it happened on the previous calendar day (e.g., 11pm yesterday is well within 24 hours of 9am today), so stale entries from the night before persisted into the next morning until they aged past the full 24-hour mark, not past midnight.

**My fix and side-effect check:** Replaced the rolling `RECENT_THRESHOLD`-based cutoff with a cutoff computed as midnight UTC of the current day: `cutoff = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)`. Removed the now-unused `RECENT_THRESHOLD` constant so it doesn't linger as misleading dead code. Verified by re-running the reproduction: the same "yesterday 11pm" event no longer appears in the feed after the fix, confirming the calendar-day boundary is now respected. Checked `get_activity_feed()` in the same file for side effects — it does not reference `RECENT_THRESHOLD` or `cutoff` at all, so it is unaffected. There is no existing automated test file for feed logic (no `test_feed.py` in the repo), so manual shell-based verification was the only regression check available for this fix. Cleaned up the manually inserted test event afterward so it doesn't linger in the seeded database.

### Issue #5 — The last song in a playlist never shows up

**How I reproduced it:** Confirmed via `flask shell` that a seeded playlist ("Late Night Vibes") had exactly 7 rows in the `playlist_entries` join table. Then called `GET /playlists/<playlist_id>/songs` via HTTP and got back `count: 6` — one fewer song than actually exists in the playlist.

**How I found the root cause:** Went to `playlist_service.py` since it's the only file behind the playlist-songs route. Read `get_playlist_songs` top to bottom: the query itself builds a join against `playlist_entries`, filters by `playlist_id`, and orders by `position` — correct and complete. The bug was in the return statement, `[song.to_dict() for song in songs[:-1]]`, which slices off the last element of the already-correct, ordered list before returning it. The function's own docstring ("This function returns all songs in the playlist") directly contradicts what the code does, which was a strong signal the slice was unintentional rather than a deliberate pagination or preview limit.

**The root cause:** The query correctly fetched every song in the playlist in position order, but the final return line applied `songs[:-1]`, an unconditional slice that drops the last element of the list regardless of playlist size or content. Since songs are ordered by position ascending, the last element in the list is always the most recently added song — matching the reported symptom exactly, including the detail that adding a new song "frees" the previously-hidden one (each new addition becomes the new last position, so the old last-position song is no longer sliced off, and the new one takes its place as the missing one).

**My fix and side-effect check:** Removed the `[:-1]` slice, changing the return line to `[song.to_dict() for song in songs]`. This is the smallest possible change since the query logic itself required no modification. Verified via a live HTTP request against the same playlist: `count` changed from 6 to 7, matching the true row count in `playlist_entries`. Ran the full `test_playlists.py` suite: all 3 tests pass, including `test_playlist_returns_all_songs` (previously failing, asserting `len(songs) == 5`) and `test_empty_playlist_returns_empty_list`, confirming the fix doesn't introduce an off-by-one or index error on empty playlists.

### Issue #3 — Duplicate search results (investigated, not fixed)

Investigated thoroughly rather than fixed. Raw SQL join (selecting `Song.id` only) confirmed 3 duplicate rows for a 3-tag song, proving the missing `.distinct()` on the `outerjoin` in `search_service.py` is a genuine code smell. However, `search_songs()` queries full `Song` ORM entities rather than raw columns, and in a fresh `flask shell` session (no identity-map caching), that same join consistently returned only 1 result for the 3-tag song — full-entity queries appear to deduplicate by primary key in the installed Flask-SQLAlchemy 3.1.1 / SQLAlchemy version. Cross-checked against the existing test suite: all 5 tests in `test_search.py`, including `test_search_no_duplicates_multi_tag_song` (written specifically to catch this bug), pass without any code changes. Conclusion: the bug's underlying mechanism exists in the query construction, but it does not currently produce user-visible duplicates in this environment. Swapped out in favor of Issue #2 as one of the three required fixes; not fixed as part of this submission.

### Issue #4 — Missing rating notification (stretch)

**How I reproduced it:** Via live HTTP: `POST /songs/<id>/rate` with one user (nova) rating a song ("Block Party") shared by a different user (darius) succeeded and saved the rating correctly (score, `song_id`, `user_id` all returned as expected). Checking `GET /users/<darius_id>/notifications` immediately after returned `count: 0` — no notification was created for the sharer, unlike the working playlist-add notification.

**How I found the root cause:** Compared `rate_song` line-by-line against the working `add_to_playlist` function, both in `notification_service.py`, per the hint that the root cause is architectural rather than a typo. Both functions load the relevant `Song` and the acting `User`, and both know the song's `shared_by` field. `add_to_playlist` ends with a guarded call to `create_notification()` (skipping the sharer if they're the one performing the action). `rate_song` has no equivalent call anywhere in its body — it saves or updates the `Rating`, commits, and returns, with no notification step at all.

**The root cause:** The notification step for ratings was never implemented, not miswritten. `create_notification()` already exists as a shared, reusable helper, and `rate_song` already has every piece of data it needs (the song's `shared_by`, the rater's username, the song's title, the score) to build a rating notification the same way `add_to_playlist` builds a playlist notification — the function simply never calls it. This is why the hint calls it architectural: the missing piece isn't a broken condition, it's an entire absent code path that has a clear working precedent elsewhere in the same file.

**My fix and side-effect check:** Added a guarded `create_notification()` call at the end of `rate_song`, immediately after `db.session.commit()` and before the `return rating` statement, mirroring the same `if <sharer> != <acting user>` self-notification guard used in `add_to_playlist`:

```python
if song.shared_by != user_id:
    create_notification(
        user_id=song.shared_by,
        notification_type="song_rated",
        body=f"{rater.username} rated your song '{song.title}' {score} stars.",
    )
```

Verified via live HTTP: after the fix, rating "Block Party" as nova produced a new notification for darius ("nova rated your song 'Block Party' 4 stars."), with `count: 1`. Checked for side effects by confirming nova's own notifications were untouched — her original seeded playlist-add notification ("darius added your song 'Midnight Drive' to the playlist 'Late Night Vibes'") was still present and unchanged, confirming the two notification paths (`add_to_playlist` and `rate_song`) operate independently with no cross-interference. There is no existing `test_notifications.py` in the repo, so live HTTP verification was the only regression check available for this fix.
