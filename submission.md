# Mixtape Codebase Map

## AI usage

I used AI as a code-reading partner, not as an autonomous bug finder.

- What I asked AI to do:
  - Summarize each service module's responsibilities so I could build a codebase map quickly.
  - Trace call chains from route to service for concrete actions (rating a song, listening to a song, viewing playlist songs).
  - Explain suspicious functions I had already located, especially edge-case logic in `update_listening_streak`, structural differences between `add_to_playlist` and `rate_song`, and list slicing behavior in `get_playlist_songs`.
  - Help format and organize findings into reproducible RCA notes.

- What AI helped me understand:
  - Where business logic consistently lives (service layer) and how routes delegate to it.
  - Why boundary conditions were central in two bugs (Sunday streak transition and final list element truncation).
  - How to compare a working interaction-notification path (`add_to_playlist`) with a failing one (`rate_song`) to isolate a missing side effect.

- What I still had to verify myself:
  - I validated every diagnosis by running code: targeted pytest cases and an isolated in-memory function call for notifications.
  - I checked adjacent behavior after each fix (same-day streak, skipped-day reset, empty playlist behavior, and self-rating no-notification).
  - I confirmed commit granularity and test pass status from actual git and pytest output.

- Where AI was incomplete or potentially misleading:
  - Issue #3 looked suspicious from code shape, but my actual run did not reproduce it in the chosen test path; this required pivoting to a different issue instead of forcing a speculative fix.
  - High-level AI explanations were useful, but insufficient alone for root-cause claims until I confirmed exact branches/returns in the source and executed reproductions.

## Screenshot evidence

Required screenshot: git log --oneline on bugfix/mixtape showing separate commits for each bug fix.

Paste your screenshot below this line before submission:

![git log --oneline on bugfix/mixtape branch showing separate fix commits](git-log-oneline-bugfix-mixtape.png)



## 1) Main files and responsibilities

### app.py
- Defines the Flask app factory `create_app`.
- Configures SQLAlchemy database URI, tracking flag, and secret key.
- Initializes the DB and registers four blueprints:
  - `/songs` (`routes/songs.py`)
  - `/playlists` (`routes/playlists.py`)
  - `/users` (`routes/users.py`)
  - `/feed` (`routes/feed.py`)
- Creates tables on startup inside app context.

### models.py
Defines all persistence models and association tables.

- Association tables:
  - `friendships`: self-referential many-to-many user friendships (bidirectional rows are inserted by seed script).
  - `song_tags`: many-to-many between songs and tags.
  - `playlist_entries`: many-to-many between playlists and songs with extra metadata (`position`, `added_by`, `added_at`).

- Core models:
  - `User`: profile identity + streak fields (`listening_streak`, `last_listened_at`) + relations to songs, ratings, events, notifications, playlists, and friends.
  - `Song`: shared music metadata + relation to tags, ratings, listening events.
  - `Tag`: normalized tag names.
  - `ListeningEvent`: append-only listen records (`user_id`, `song_id`, `listened_at`).
  - `Rating`: per-user-per-song rating with unique constraint `(user_id, song_id)`.
  - `Playlist`: playlist metadata and linked songs.
  - `Notification`: user-facing events with type/body/read status.

### routes/
Routes are intentionally thin: they parse request data, validate required fields, call one service function, and format JSON responses.

- `routes/songs.py`
  - `GET /songs/search?q=...` -> `search_service.search_songs`
  - `GET /songs/<song_id>` -> `search_service.get_song`
  - `POST /songs/<song_id>/rate` -> `notification_service.rate_song`
  - `POST /songs/<song_id>/listen` -> `streak_service.record_listening_event`

- `routes/playlists.py`
  - `POST /playlists/` -> `playlist_service.create_playlist`
  - `GET /playlists/<playlist_id>` -> `playlist_service.get_playlist`
  - `GET /playlists/<playlist_id>/songs` -> `playlist_service.get_playlist_songs`
  - `POST /playlists/<playlist_id>/songs` -> `notification_service.add_to_playlist`

- `routes/users.py`
  - `GET /users/<user_id>` reads model directly.
  - `GET /users/<user_id>/streak` -> `streak_service.get_streak`
  - `GET /users/<user_id>/notifications` -> `notification_service.get_notifications`
  - `POST /users/notifications/<notification_id>/read` -> `notification_service.mark_as_read`

- `routes/feed.py`
  - `GET /feed/<user_id>/listening-now` -> `feed_service.get_friends_listening_now`
  - `GET /feed/<user_id>/activity` -> `feed_service.get_activity_feed`

### services/
Business logic lives here.

- `services/streak_service.py`
  - `record_listening_event`: creates a `ListeningEvent`, updates streak, commits.
  - `update_listening_streak`: applies day-based streak rules.
  - `get_streak`: fetches current streak value.

- `services/feed_service.py`
  - `get_friends_listening_now`: friend-scoped + recency-filtered events, one latest event per friend.
  - `get_activity_feed`: friend-scoped latest N events regardless of recency.

- `services/search_service.py`
  - `search_songs`: title/artist substring search (case-insensitive), with tag join.
  - `get_song`: fetch single song details.

- `services/notification_service.py`
  - `create_notification`: persistence helper.
  - `add_to_playlist`: appends song to playlist and notifies original sharer (if different user).
  - `rate_song`: create/update song rating.
  - `get_notifications` + `mark_as_read`: retrieval and read-state mutation.

- `services/playlist_service.py`
  - `create_playlist`: validates creator and writes playlist.
  - `get_playlist_songs`: returns ordered songs for a playlist.
  - `get_playlist`: playlist metadata.
  - `get_user_playlists`: playlists by creator.

### seed_data.py
- Rebuilds DB and seeds realistic fixtures:
  - users + friendships
  - songs with varied tag cardinalities (0, 1, 3+)
  - listening events over recent and older windows
  - playlists with ordered entries
  - starter notifications
- Data is explicitly crafted to expose/search for service-layer bugs.

### tests/
- `tests/test_streaks.py`: consecutive-day, same-day, skipped-day, Sunday edge behavior.
- `tests/test_search.py`: duplicate prevention across songs with 0/1/multiple tags.
- `tests/test_playlists.py`: validates all songs returned and ordering.

## 2) Data flow trace (feature): friend rates my song and I should be notified

This is the implemented call chain in code and the expected side effect pattern.

1. Client sends `POST /songs/<song_id>/rate` with `user_id` and `score`.
2. `routes/songs.py` validates payload and calls `notification_service.rate_song(user_id, song_id, score)`.
3. `notification_service.rate_song`:
   - validates score range,
   - loads `Song` and `User`,
   - upserts a `Rating` row (update existing or create new),
   - commits and returns `Rating`.
4. Route serializes `rating.to_dict()` and returns `201`.

Observation: unlike playlist additions, the rating path currently does not call `create_notification` for the original sharer, which aligns with one of the listed open issues.

## 3) Data flow trace (feature): friend adds my song to a playlist and I get notified

1. Client sends `POST /playlists/<playlist_id>/songs` with `song_id` and `added_by`.
2. `routes/playlists.py` calls `notification_service.add_to_playlist`.
3. `notification_service.add_to_playlist`:
   - validates song, adder, and playlist existence,
   - appends song to `playlist.songs` if not already present,
   - commits playlist change,
   - if adder is not the original sharer, calls `create_notification(...)`.
4. Notification row is created and committed in `create_notification`.

## 4) Architectural patterns noticed

- Pattern: route handlers are mostly orchestration-only; service modules own business rules.
- Pattern: model `to_dict()` methods are the JSON contract boundary for responses.
- Pattern: service functions raise `ValueError` for domain problems; routes map these to 4xx responses.
- Pattern: DB writes generally commit inside each service function (unit-of-work per service call).
- Pattern: tests are service-centric and encode behavioral requirements as black-box assertions.
- Pattern: many features rely on association tables carrying extra semantics (`playlist_entries.position`, friendship graph symmetry).

## 5) Issue scan notes (all five)

I reviewed all five issue statements listed in `README.md` and matched each to its service file:
1. Streak resets unexpectedly -> `services/streak_service.py`
2. Listening-now includes stale users -> `services/feed_service.py`
3. Search duplicates songs -> `services/search_service.py`
4. Missing rating notification -> `services/notification_service.py`
5. Final playlist song missing -> `services/playlist_service.py`

Initial three picked for reproduction were #3, #4, #5. After a genuine attempt, #3 did not reproduce in the current test path, so I switched to #1 per project instructions.

Current three selected for fixes:
1. `streak_service.py` (Issue #1)
2. `notification_service.py` (Issue #4)
3. `playlist_service.py` (Issue #5)

## 6) Bug reproduction log (no code changes yet)

### Issue #1 — My listening streak keeps resetting
- how you reproduced it:
  - Ran: `pytest -q tests/test_streaks.py::test_streak_increments_on_sunday`
  - Test setup condition: user listens on Saturday, then Sunday (explicit Sunday edge case).
  - Trigger sequence:
    1. `update_listening_streak(user, saturday)` -> streak becomes 1.
    2. `update_listening_streak(user, sunday)` -> expected 2, actual 1.
  - Observed failure: assertion `assert u.listening_streak == 2` fails because streak resets to 1 on Sunday.

### Issue #4 — No notification when a friend rates my song
- how you reproduced it:
  - Used a minimal in-memory app context (SQLite memory DB) with two users:
    - song sharer: `owner`
    - friend/rater: `friend`
  - Created one song shared by `owner`.
  - Trigger sequence:
    1. Called `rate_song(friend_id, song_id, 5)`.
    2. Queried sharer's notifications with `get_notifications(owner_id)`.
  - Observed behavior:
    - Rating is saved.
    - Notification list is empty (`owner_notifications_count 0`).

### Issue #5 — Last song in playlist never shows up
- how you reproduced it:
  - Ran: `pytest -q tests/test_playlists.py::test_playlist_returns_all_songs`
  - Test setup condition: seeded playlist with exactly 5 ordered songs.
  - Trigger sequence:
    1. Call `get_playlist_songs(playlist_id)`.
    2. Count returned songs.
  - Observed failure: expected 5, actual 4 (`assert 4 == 5`), meaning final entry is dropped.

### Attempted but not currently reproducible in this path
- Issue #3 (`search_service.py`) attempt details:
  - Ran: `pytest -q tests/test_search.py::test_search_no_duplicates_multi_tag_song`
  - Also checked neighboring data conditions:
    - `test_search_no_duplicates_single_tag_song` passes.
    - `test_search_no_duplicates_no_tag_song` passes.
  - Result in this run: multi-tag duplicate assertion did not fail (test passed), so I switched one selected bug to Issue #1 as instructed.

## 7) Root cause analysis and navigation strategy (symptom -> root cause)

I used a top-down navigation strategy for each bug:
1. Start from the user symptom (or failing test).
2. Find the route handling that action.
3. Follow the service call from that route.
4. Compare intended behavior vs actual branch/return in that service.
5. Verify with controlled execution (targeted pytest or in-memory function call).

### Issue #1 RCA — streak resets on Sunday
- symptom:
  - `test_streak_increments_on_sunday` fails (`expected 2`, `got 1`) after Saturday -> Sunday updates.
- files I looked at and why:
  - `tests/test_streaks.py`: found the precise failing condition (Saturday then Sunday).
  - `routes/songs.py`: confirmed listening events flow through `POST /songs/<song_id>/listen` into streak service.
  - `services/streak_service.py`: traced `record_listening_event` -> `update_listening_streak`.
- call/data flow traced:
  1. Route `listen(...)` calls `record_listening_event(...)`.
  2. `record_listening_event` creates a `ListeningEvent` and calls `update_listening_streak(user, now)`.
  3. `update_listening_streak` computes `days_since_last` and branches.
- root cause:
  - In `update_listening_streak`, the increment branch is gated by:
    - `elif days_since_last == 1 and today.weekday() != 6:`
  - Sunday has `weekday() == 6`, so even valid consecutive-day listens on Sunday are forced into reset branch.
  - This adds a Sunday-specific exclusion that contradicts documented streak rules.

### Issue #4 RCA — no notification when rating a friend's song
- symptom:
  - Rating is created successfully, but the original sharer receives zero notifications.
- files I looked at and why:
  - `routes/songs.py`: found rating entrypoint `POST /songs/<song_id>/rate` calling `rate_song`.
  - `services/notification_service.py`: compared rating path with playlist-add path in the same module.
  - `models.py`: verified notification schema exists and is persisted via `Notification` model.
- call/data flow traced:
  1. Route `rate(...)` calls `rate_song(user_id, song_id, score)`.
  2. `rate_song` validates score and entities, upserts a `Rating`, commits, returns.
  3. No call to `create_notification(...)` exists in this path.
  4. In contrast, `add_to_playlist(...)` does call `create_notification(...)` for the song sharer.
- root cause:
  - Missing side effect: `rate_song` persists ratings but never emits notification records.
  - Structural mismatch between two friend-interaction paths in the same service module.

### Issue #5 RCA — last playlist song is always missing
- symptom:
  - Playlist seeded with 5 songs returns only 4 from `get_playlist_songs`.
- files I looked at and why:
  - `tests/test_playlists.py`: established expected behavior (all songs returned in order).
  - `routes/playlists.py`: confirmed endpoint `GET /playlists/<playlist_id>/songs` calls `get_playlist_songs`.
  - `services/playlist_service.py`: inspected query and return transformation.
- call/data flow traced:
  1. Route `get_songs(...)` calls `get_playlist_songs(playlist_id)`.
  2. Service queries and orders all playlist songs correctly via `playlist_entries.position`.
  3. Return statement slices the list with `songs[:-1]` before serialization.
- root cause:
  - Off-by-one truncation in return path:
    - `return [song.to_dict() for song in songs[:-1]]`
  - This always drops the final item, including non-empty valid playlists.

## 9) Fixed bug entries (required RCA format)

### 1. Issue number and title
Issue #5 — The last song in a playlist never shows up

### 2. How you reproduced it
- Ran: `pytest -q tests/test_playlists.py::test_playlist_returns_all_songs`
- Data condition: playlist seeded with 5 songs in `playlist_entries` positions 1..5.
- Trigger: called `get_playlist_songs(playlist_id)` and compared returned length.
- Observed: service returned 4 songs instead of 5.

### 3. How you found the root cause
- Navigation path (top-down):
  1. `tests/test_playlists.py` (failing expectation)
  2. `routes/playlists.py` (`GET /playlists/<playlist_id>/songs` entry)
  3. `services/playlist_service.py` (`get_playlist_songs`)
- Confidence moment: query itself returned all ordered songs, but the return expression sliced with `songs[:-1]`, which deterministically drops the last song.

### 4. The root cause
The service correctly queried and ordered all playlist songs, then mistakenly removed the final element during serialization by iterating over `songs[:-1]` rather than `songs`. This created a consistent off-by-one truncation where any non-empty playlist lost its last song.

### 5. Your fix and side-effect check
- Fix: changed `return [song.to_dict() for song in songs[:-1]]` to `return [song.to_dict() for song in songs]` in `get_playlist_songs`.
- Why this works: it preserves the full ordered query result without truncation.
- Side-effect checks run:
  - `pytest -q tests/test_playlists.py::test_playlist_returns_all_songs`
  - `pytest -q tests/test_playlists.py::test_playlist_returns_songs_in_order`
  - `pytest -q tests/test_playlists.py::test_empty_playlist_returns_empty_list`

### 1. Issue number and title
Issue #1 — My listening streak keeps resetting

### 2. How you reproduced it
- Ran: `pytest -q tests/test_streaks.py::test_streak_increments_on_sunday`
- Data condition: user with Saturday listen followed by Sunday listen.
- Trigger: two sequential calls to `update_listening_streak` with Saturday then Sunday datetimes.
- Observed: streak became `1` instead of incrementing to `2`.

### 3. How you found the root cause
- Navigation path (top-down):
  1. `tests/test_streaks.py` (Sunday-specific failing scenario)
  2. `routes/songs.py` (`POST /songs/<song_id>/listen` calls `record_listening_event`)
  3. `services/streak_service.py` (`record_listening_event` -> `update_listening_streak`)
- Confidence moment: found increment branch `elif days_since_last == 1 and today.weekday() != 6`, which excludes Sunday from consecutive-day increments.

### 4. The root cause
The streak logic added a weekday guard that blocks increments on Sundays. In Python, `datetime.weekday()` returns `6` on Sunday, so a valid consecutive-day transition into Sunday (`days_since_last == 1`) was incorrectly routed to the reset branch. This caused Sunday listens to reset streaks instead of incrementing them.

### 5. Your fix and side-effect check
- Fix: removed the unnecessary weekday condition, changing:
  - `elif days_since_last == 1 and today.weekday() != 6:`
  - to `elif days_since_last == 1:`
- Why this works: consecutive-day streak increments now depend only on date gap, which matches the documented streak rules.
- Side-effect checks run:
  - `pytest -q tests/test_streaks.py::test_streak_starts_at_1_for_new_user`
  - `pytest -q tests/test_streaks.py::test_streak_increments_on_consecutive_day`
  - `pytest -q tests/test_streaks.py::test_streak_does_not_double_count_same_day`
  - `pytest -q tests/test_streaks.py::test_streak_resets_after_skipped_day`
  - `pytest -q tests/test_streaks.py::test_streak_increments_on_sunday`

### 1. Issue number and title
Issue #4 — I got notified when a friend added my song to a playlist but not when they rated it

### 2. How you reproduced it
- Built minimal in-memory state with two users (`owner`, `friend`) and one song shared by `owner`.
- Called `rate_song(friend_id, song_id, 5)`.
- Queried `get_notifications(owner_id)`.
- Observed: rating was saved, but owner notification list remained empty.

### 3. How you found the root cause
- Navigation path (top-down):
  1. `routes/songs.py` (`POST /songs/<song_id>/rate` -> `rate_song`)
  2. `services/notification_service.py` (`rate_song` implementation)
  3. Same file's `add_to_playlist` as a known-good comparison path
- Confidence moment: `add_to_playlist` explicitly calls `create_notification(...)`, while `rate_song` commits and returns without any notification call.

### 4. The root cause
The rating flow persisted `Rating` records but never created a corresponding `Notification` for the original sharer. The code handled validation and upsert correctly, but omitted the notification side effect entirely, so no notification rows were produced for rating events.

### 5. Your fix and side-effect check
- Fix: in `rate_song`, after saving the rating, added:
  - conditional notification creation when `song.shared_by != user_id`
  - notification type `song_rated`
  - body including rater username, song title, and score
- Why this works: rating interactions now emit a notification to the song owner, matching expected behavior and existing interaction-notification pattern.
- Side-effect checks run:
  - `pytest -q tests/test_notifications.py::test_rate_song_creates_notification_for_sharer`
  - `pytest -q tests/test_notifications.py::test_rate_song_self_rating_does_not_notify`
  - `pytest -q tests/test_streaks.py`
  - `pytest -q tests/test_playlists.py`
