# AI Usage
During this project, I collaborated with an AI assistant, Gemini, to help navigate the codebase, understand data flow, and trace bugs.
1. **Interactive Shell Tracing:** For Issue #2 and Issue #4, there were no automated tests available. I used Gemini to help me write short Python scripts to run in the `flask shell` so I could manually query the database and verify the exact state of the `listened_at` timestamps and user notification counts.
2. **Codebase Navigation & Comparison:** For Issue #4, Gemini guided me through a side-by-side comparison of `add_to_playlist()` and `rate_song()` inside `services/notification_service.py` to identify what architectural pattern was missing from the rating logic.
3. **Course Correction & Verification:** Gemini was helpful but occasionally tried to skip critical steps. For example, when debugging Issue #2 and Issue #4, Gemini immediately suggested looking at the code to find the fix. I had to explicitly course-correct it and insist that we strictly adhere to the project's engineering methodology of *reproducing the bug first* before looking at any files. I also independently verified all of its code suggestions by running the local test suite and checking the browser endpoints myself.

# Codebase Map

**Main Files & Roles:**
* **`app.py`:** This file creates the application using Flask. It boots up the server, connects to the database, and registers the Blueprints. app.py imports the routes (like songs_bp) and attaches them to the app so the server knows where to send web traffic.
* **`models.py`:** This file defines the database schema using SQLAlchemy. It includes models like User and Song, as well as association tables like friendships and song_tags which are used to link many-to-many relationships together.
* **`routes/` (The Controllers):** These files (like songs.py and playlists.py) handle the web layer: incoming HTTP requests, JSON payloads, and HTTP status codes. Pattern: They do not contain business logic; they parse the request and immediately delegate the work to the services layer.
* **`services/` (The Brains):** These files (like search_service.py and streak_service.py) handle the core business logic. They do not interact directly with the web server; instead, they do the raw math, query and update the database, and format the data to return to the routes.

**Data Flow Trace: A user listens to a song**
1. The Request: When a user listens to a song, the POST /<song_id>/listen endpoint in routes/songs.py receives the incoming JSON request and extracts the user_id and song_id.
2. The Handoff: The route calls the record_listening_event() function located in services/streak_service.py.
3. The Business Logic: record_listening_event() creates a new listening event record in the database. It then calls update_listening_streak() to update the user's current streak and last listened date/time.
4. The Response: The listen function in routes/songs.py receives the event object back from the service and returns jsonify(event.to_dict()). The route sends back the data about the listening event itself (when it happened and what song it was) to the client.

### Issue #5 - The last song in a playlist never shows up

* **How I reproduced it:** I ran the test file, `test_playlists.py`, and the test failure output was `AssertionError: assert 4 == 5. Right contains one more item: 'Track 5'`. The test added 5 songs to a playlist, but the code only returned 4. Track 5 was missing.
* **How I found the root cause:** I traced the failing test to `get_playlist_songs()` in `services/playlist_service.py`. I checked its return statement.
* **The root cause:** The original return statement for that function was `return [song.to_dict() for song in songs[:-1]]`, meaning that because of the `[:-1]` slice, every song in the playlist except the last added song was being returned. 
* **My fix and side-effect check:** I removed `[:-1]` from the return statement. After running `test_playlists.py` again, the test passed and Track 5 was included, confirming the function now returns all songs in the playlist without breaking the ordering.

### Issue #1 - My listening streak keeps resetting

* **How I reproduced it:** I ran the test file, `test_streaks.py`, and the test failure output was `assert 1 == 2 + where 1 = <User...>.listening_streak`. The test explicitly simulates a user listening on Saturday and then again on Sunday, expecting the streak to go up to 2, but instead it resets to 1.
* **How I found the root cause:** I traced the failing test to `update_listening_streak()` in `services/streak_service.py`, specifically checking the `elif` statement controlling the daily increment.
* **The root cause:** The `elif` statement included `and today.weekday() != 6`. In Python, Sunday is represented by `6`. When a user listens on Sunday, `6 != 6` evaluates to `False`. This causes the code to skip the `user.listening_streak += 1` increment entirely and fall to the `else` block, which hard-resets the streak to `1`.
* **My fix and side-effect check:** I removed the `and today.weekday() != 6` condition from the `elif` statement. Re-running `test_streaks.py` resulted in all 5 tests passing. This confirmed the streak increments correctly across the Saturday-to-Sunday boundary without breaking existing streak logic.

### Issue #2 - Friends Listening Now shows people from yesterday

* **How I reproduced it:** I navigated to the `/feed/<user_id>/listening-now` endpoint in the browser using a seeded user's ID. The JSON response returned a list of friends with `listened_at` timestamps from several hours prior, proving the endpoint was pulling in stale data rather than current activity.
* **How I found the root cause:** I investigated `services/feed_service.py` and looked for the logic that defines the timeframe for the `get_friends_listening_now()` function.
* **The root cause:** At the top of the file, `RECENT_THRESHOLD` was hardcoded to `timedelta(hours=24)`. The database query uses this threshold to filter recent events, meaning anyone who listened to a song in the last 24 hours (including yesterday) was erroneously included in the "Listening Now" feed.
* **My fix and side-effect check:** I changed `RECENT_THRESHOLD` to `timedelta(minutes=15)`. I refreshed the browser endpoint and verified that the feed count dropped to 0, confirming that old listening events from earlier in the day were properly filtered out.

### Issue #4 - I got notified when a friend added my song to a playlist but not when they rated it

* **How I reproduced it:** I opened the interactive Flask shell and wrote a script to simulate a user ("nova") rating a song shared by a friend. I checked the friend's notification count before and after the rating occurred, and it remained at 0, confirming the notification was never generated.
* **How I found the root cause:** I performed a side-by-side comparison of `add_to_playlist()` and `rate_song()` inside `services/notification_service.py`. 
* **The root cause:** The `rate_song()` function was completely missing the code block to generate a notification. It successfully saved the rating to the database but returned the object without ever calling `create_notification()`.
* **My fix and side-effect check:** I added an `if` statement to check that the rater is not the original sharer, followed by a call to `create_notification()` passing in the sharer's ID, a `"song_rated"` type, and a descriptive body message. Re-running the shell script confirmed the sharer's notification count successfully incremented from 0 to 1.