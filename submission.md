# Mixtape Bug Hunt — Submission

## AI Usage

During this project, I used Claude to help me navigate and debug the Mixtape codebase, and verified its explanations against the actual code and test results rather than accepting them at face value.

For Issue #3, I asked why duplicate search results might appear and where in the codebase to inspect first. Claude predicted that the join in `search_service.py` against the `song_tags` table might fan out one song into multiple duplicate rows. When I verified this myself, the raw SQL join did return 3 repeated rows for the same song, but running `pytest` and testing the API with `curl` showed only one result returned externally. This revealed that SQLAlchemy was automatically deduplicating ORM entity objects by primary key, so Claude was partly right about the join fan-out but incomplete about whether that actually caused a user-visible bug — I concluded Issue #3 does not currently reproduce and did not fix it.

For Issue #4, after I added the `create_notification()` call inside `rate_song()`, I reran my reproduction steps in the same `flask shell` session, but the notification count still didn't increase. Claude suggested the shell session was still using the old imported version of the code. I exited and reopened `flask shell`, reran the same before/after notification check, and confirmed the new `"song_rated"` notification was created correctly.

For Issues #1 and #5, Claude helped by asking guiding questions about exact code behavior instead of giving me the final answer. For the streak bug, it asked me to compare the written streak rule against the condition `days_since_last == 1 and today.weekday() != 6`, which led me to conclude Sunday was incorrectly excluded. For the playlist bug, it asked me to reason through what `songs[:-1]` means after sorting by ascending `position`, which let me identify that the code was always cutting off the most recently added song.

---

## Codebase Map

**Models (`models.py`):** The main models represent the core objects in the Mixtape app. `User` represents an app user, `Song` represents a shared song, `Playlist` represents a playlist created by a user, `Rating` stores a user's score for a song, `Notification` stores messages shown to users when friends interact with their songs, and `ListeningEvent` records when a user listened to a song. The `playlist_entries` association table connects playlists and songs, and — unlike the plain `friendships` and `song_tags` tables — it carries extra columns (`position`, `added_by`, `added_at`); `position` specifically stores the order of songs inside a playlist, separate from insertion order.

**Routes vs. services:** Route files handle the HTTP layer, while service files handle the main application logic. A route (e.g. in `songs.py`) parses the incoming request, extracts values like the current user id, song id, or score, calls the appropriate service function, and returns a JSON response. Routes never touch the database directly. The service layer (e.g. `notification_service.py`, `search_service.py`, `playlist_service.py`) is where the code actually queries or updates the database through `db.session`.

**Data flow — rating a song:** The user sends `POST /songs/<id>/rate`. The route in `songs.py` (`rate()`) parses `user_id` and `score` from the request body and calls `rate_song(user_id, song_id, score)` in `notification_service.py`. Inside `rate_song()`, the service validates the score is between 1 and 5, loads the `Song` and `User`, and either creates a new `Rating` or updates an existing one (there's a unique constraint on `user_id` + `song_id`), then commits it to the database.

**Pattern noticed:** Every route wraps its service call in a `try/except ValueError`, translating it into a 404 or 400 JSON response. Services raise `ValueError` for "not found" or invalid-input cases rather than returning `None`.

---

## Root Cause Analysis

### Issue #1 — My listening streak keeps resetting

**How I reproduced it:** I reproduced this bug by running the streak tests with `pytest`. The failing test was `test_streak_increments_on_sunday`, which showed that a user who listened on Saturday and then again on Sunday did not get their streak incremented correctly.

**How I found the root cause:** I traced the issue to `services/streak_service.py`, inside the `update_listening_streak()` function. The suspicious line was `elif days_since_last == 1 and today.weekday() != 6:`, which controlled whether the streak should increment when the user listened on the next calendar day.

**The root cause:** The written streak rule says that if the user listened yesterday, the streak should increment by 1. That rule only depends on `days_since_last == 1`, but the code also checked `today.weekday() != 6`. Since Python's `datetime.weekday()` returns `6` for Sunday, this condition becomes false on Sundays, so even when `days_since_last == 1` is true, the code skips the increment branch and resets the streak to 1 instead.

**Fix and side-effect check:** I fixed the bug by changing the condition to `elif days_since_last == 1:`, removing the incorrect Sunday exclusion. To verify the fix and check side effects, I ran the full streak test file: `test_streak_starts_at_1_for_new_user`, `test_streak_increments_on_consecutive_day`, `test_streak_does_not_double_count_same_day`, `test_streak_resets_after_skipped_day`, and `test_streak_increments_on_sunday`. All 5 passed, confirming the Sunday case was fixed while the existing first-listen, same-day, consecutive-day, and skipped-day behaviors were unaffected.

### Issue #2 — Friends Listening Now shows people from yesterday

**How I reproduced it:** I reproduced this bug manually in `flask shell` by creating a fake listening event for Darius from about 20 hours earlier, then deleting Darius's more recent events so that this older event was the only one left. When I called `get_friends_listening_now(nova.id)`, Darius still appeared in the result even though the event timestamp was from `2026-07-07` and the current day was `2026-07-08`. This confirmed nova's report that yesterday's listening activity could still show up in the "Listening Now" feed.

**How I found the root cause:** I traced the issue to `services/feed_service.py`, inside `get_friends_listening_now()`. At the top of the file, the code defined `RECENT_THRESHOLD = timedelta(hours=24)`, and inside the function it calculated `cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD`. The query then returned events where `ListeningEvent.listened_at >= cutoff`.

**The root cause:** The root cause was that the code used a rolling 24-hour window instead of a calendar-day window. If the current time is the morning of July 8, an event from the evening of July 7 can still be within the last 24 hours, so it passes the filter even though it happened on a different calendar day. nova expected "Listening Now" to mean activity from today only, starting at `00:00` of the current UTC day, not any activity from the last 24 hours.

**Fix and side-effect check:** I fixed the bug by changing the cutoff to the start of the current UTC day: `datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)`, and importing `time` from `datetime`. After restarting `flask shell`, I reran `get_friends_listening_now(nova.id)` and confirmed that Darius no longer appeared, because his only remaining event was before the new cutoff. As a side-effect check, I also ran `get_activity_feed()` and confirmed it still returned recent friend activity as expected, because that function has a separate code path and intentionally does not filter by today-only recency.

### Issue #3 — The same song keeps showing up twice in search (investigated, not reproducible — no fix applied)

**Reproduction attempt:** I investigated the reported duplicate search results by running the existing tests, testing the search endpoint manually with `curl`, and checking the underlying SQL behavior directly in `flask shell`. The raw SQL join between songs and `song_tags` did fan out one matching song into multiple rows when the song had multiple matching tags.

**Result:** Even though the raw SQL join produced repeated rows, the user-visible API result did not contain duplicates. `search_service.py` queries ORM `Song` entities via `db.session.query(Song)`, and SQLAlchemy's legacy Query API deduplicates those entity objects by primary key before returning them. Because `pytest` and `curl` both showed only one result externally, I concluded that this bug does not actually reproduce in the current app behavior.

**Decision:** I decided not to make a code change for this issue because there was no confirmed user-visible bug to fix. Changing the search query without a failing reproduction would risk introducing unnecessary side effects, so I documented the investigation as a finding instead of counting it as one of my fixed bugs.

### Issue #4 — I got notified when a friend added my song to a playlist but not when they rated it

**How I reproduced it:** I reproduced this bug in `flask shell` by choosing one user to rate a song shared by another user. I checked `get_notifications(song.shared_by)` before calling `rate_song(rater.id, song.id, score)`, then checked it again afterward. The rating was saved successfully in the database, but the notification count did not increase, so no new notification was created for the song owner.

**How I found the root cause:** I traced the issue in `services/notification_service.py` by comparing `add_to_playlist()` with `rate_song()`. `add_to_playlist()` saves the playlist action and then calls `create_notification()` for the song owner if the acting user is not the owner. In contrast, `rate_song()` created or updated the `Rating` and committed it, but had no corresponding `create_notification()` call.

**The root cause:** The root cause was that `rate_song()` was missing an entire notification step. The function successfully saved the rating, but it never created a notification for the user who originally shared the song. This was different from a wrong-condition bug like Issue #1; the notification logic simply had not been implemented in this code path.

**Fix and side-effect check:** I fixed the bug by adding a check after the rating commit: if `song.shared_by != user_id`, call `create_notification()` with `user_id=song.shared_by`, `notification_type="song_rated"`, and a body that includes the rater's username, song title, and score. I verified the main case by rating another user's song and confirming that `get_notifications(song.shared_by)` increased by one and the newest notification had type `"song_rated"`. I also verified the side-effect case by having the song owner rate their own song and confirming the notification count did not increase, so the fix does not create self-notifications — matching the same pattern `add_to_playlist()` already used.

### Issue #5 — The last song in a playlist never shows up

**How I reproduced it:** I reproduced this bug by running `pytest tests/ -v`, where the playlist-related tests failed. In particular, `test_playlist_returns_all_songs` and `test_playlist_returns_songs_in_order` showed that the playlist was not returning the full ordered list of songs.

**How I found the root cause:** I traced the failing behavior to `services/playlist_service.py`, specifically the `get_playlist_songs()` function. This function queries songs by joining `Song` with `playlist_entries`, filters by `playlist_id`, and orders the results by `playlist_entries.c.position` in ascending order.

**The root cause:** The bug was caused by the final return line: `return [song.to_dict() for song in songs[:-1]]`. In Python, `songs[:-1]` returns every item except the last one. Since the songs were sorted by ascending `position`, the last item was the song with the largest position, meaning the most recently added song was always removed from the response.

**Fix and side-effect check:** I fixed the bug by changing the return line to `return [song.to_dict() for song in songs]`, so the function returns the full list without cutting off the final song. To verify the fix and check for side effects, I ran the relevant playlist tests: `test_playlist_returns_all_songs`, `test_playlist_returns_songs_in_order`, and `test_empty_playlist_returns_empty_list`. The first two confirmed the missing-song bug was fixed, and the empty-playlist test confirmed that the existing empty-playlist behavior still worked.

---

## Regression Test

I added `tests/test_notifications.py` to cover Issue #4. The test creates two users, creates a song shared by one user, has the other user rate the song through `rate_song()`, and then checks that the song owner receives exactly one new notification with type `"song_rated"`.

To confirm the test actually catches the bug, I temporarily commented out the new `create_notification()` block in `rate_song()` and ran the test, which failed (`assert 0 == (0 + 1)`) because the notification count did not increase. I then restored the fix and reran the test, and it passed. This confirmed the regression test fails against the old buggy behavior and passes after the fix, so it would have caught this bug if it had been introduced (or reintroduced) before merging.