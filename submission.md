# Project 5 — Mixtape Bug Hunt: Submission

## AI Usage


I used an AI assistant mostly as a **reading buddy** to help me understand code someone else wrote. Following the patterns suggested in the project brief, I:

- **Asked for plain-English summaries** of each service file — "what is this file responsible for, and what does each function do?" — and then read the code myself to confirm the explanation was right.
- **Traced how features work end to end.** I asked it to follow two features step by step: (1) what happens when someone adds a song to a playlist, and (2) what happens when someone listens to a song. The "how it works" sections below come from those traces.
- **Clarified concepts I wasn't sure about**, like how Python numbers the days of the week, and why a database "join" can accidentally return the same item more than once.

**Where I double-checked or corrected the AI:**
I treated the AI's explanations as a starting point and confirmed every bug myself before trusting it. For the streak and playlist bugs I ran the existing test suites (`pytest`) and watched the specific test go from failing to passing. For the feed and notification bugs I reproduced them by hand in `flask shell`, calling the service functions directly and checking the before/after results — for example, picking a specific user (darius) whose friends' listen times sat on either side of the "recent" window so the feed bug would actually show.

A few things the AI did *not* predict, which I only caught by running the code:
- On the feed fix, my first attempt still showed the bug because the unit was wrong — I had changed the number but left it as `hours` instead of `minutes`. Reading the actual output (not the explanation) is what caught it.
- The `flask shell` doesn't reload a service file after you edit it — I had to fully restart the shell to see my changes take effect.
- My virtual environment didn't actually have the dependencies installed (they were in system Python), which only surfaced when `python seed_data.py` failed — I fixed it with `pip install -r requirements.txt` inside the venv.

So the AI was most useful for *reading and orienting*, but I verified each diagnosis and fix by running the tests or the code myself.

---

## Codebase Map

### The big picture

Mixtape is a small web app (built with **Flask**, a Python tool for making websites) where friends share songs, build playlists, and track listening streaks.

The code is organized into **three layers**. Every time you visit a page or click something, the request travels through them in the same order:

```
Your browser  →  routes/   →  services/   →  models.py  →  database
                 (the front     (the rules     (the shape    (where the
                  desk)          & decisions)   of the data)  data lives)
```

- **routes/** = the *front desk*. It takes your request, checks it's valid, and passes it along. It doesn't make decisions itself.
- **services/** = the *brains*. This is where all the real logic happens (and where all five bugs live).
- **models.py** = the *filing system*. It defines what a User, Song, Playlist, etc. looks like.
- **database** = the actual saved data (a file called `mixtape.db`).

### What each file does

| File | What it's responsible for |
|------|---------------------------|
| **`app.py`** | Starts the app and connects the four sections of the site: `/songs`, `/playlists`, `/users`, and `/feed`. |
| **`models.py`** | Defines the "shape" of everything stored: users, songs, ratings, playlists, notifications, etc. |
| **`routes/songs.py`** | Handles searching for songs, viewing a song, rating a song, and recording a listen. |
| **`routes/playlists.py`** | Handles creating playlists and adding/viewing their songs. |
| **`routes/users.py`** | Handles user profiles, streaks, and notifications. |
| **`routes/feed.py`** | Handles the "Friends Listening Now" and activity feeds. |
| **`services/streak_service.py`** | Works out your listening streak (how many days in a row you've listened). |
| **`services/feed_service.py`** | Decides which friends count as "listening now." |
| **`services/search_service.py`** | Finds songs matching what you typed. |
| **`services/notification_service.py`** | Creates and fetches notifications; also adds songs to playlists and saves ratings. |
| **`services/playlist_service.py`** | Creates playlists and lists the songs inside one, in order. |
| **`seed_data.py`** | Fills the database with fake test data (users, songs, playlists) so there's something to work with. |
| **`tests/`** | Automated checks. Some of them already fail on purpose — they describe how the app *should* behave, which points straight at the bugs. |

### A note on how songs are stored in playlists

One detail worth knowing: a playlist doesn't just store "these songs in whatever order." There's a special connecting table called `playlist_entries` that also records a **position number** (1st, 2nd, 3rd...) for each song. So playlists have a real, intended order. This matters for one of the bugs.

### How it works — Example 1: adding a song to a playlist sends a notification

1. Someone sends a request to add a song to a playlist (`POST /playlists/<id>/songs`).
2. The **front desk** (`routes/playlists.py`) checks the request has a song and a user, then hands it to the **brains** (`notification_service.add_to_playlist`).
3. The service adds the song to the playlist.
4. Then it checks: *who originally shared this song?* If it wasn't the same person adding it, it creates a **notification** for the original sharer ("So-and-so added your song to a playlist").
5. That sharer sees the notification later when they check `/users/<id>/notifications`.

**The takeaway:** one simple action (adding a song) quietly triggers a second thing (a notification).

**Exact call chain (technical):** `POST /playlists/<id>/songs` → `routes/playlists.py: add_song()` → `notification_service.add_to_playlist()` → reads `Song.shared_by` → `create_notification()` writes a new `Notification` row → later read by `get_notifications()`.

### How it works — Example 2: listening to a song updates your streak

1. Someone sends a request that they listened to a song (`POST /songs/<id>/listen`).
2. The front desk (`routes/songs.py`) passes it to `streak_service.record_listening_event`.
3. That service does **two** things: it records the listen, *and* it recalculates the streak.
4. The streak logic compares the last time you listened to today:
   - Listened yesterday → streak goes up by 1
   - Skipped a day → streak resets to 1
   - Already listened today → no change

**The takeaway:** again, one action ("I listened") secretly does a second thing (updates the streak). Several bugs hide in these "secret second steps."

**Exact call chain (technical):** `POST /songs/<id>/listen` → `routes/songs.py: listen()` → `streak_service.record_listening_event()` → inserts a `ListeningEvent` row **and** calls `update_listening_streak()`, which changes `User.listening_streak` and `User.last_listened_at` → both saved in one commit → later read by `get_streak()`.

### Patterns I noticed

1. **The front desk stays simple; the brains do the work.** Every route just checks the request and passes it on — all the actual logic is in the `services/` files. So when a page misbehaves, the bug is almost always in the matching service file.
2. **Everything gets turned into the same format on the way out.** Every piece of data knows how to describe itself as a simple dictionary, which is what gets sent back to the browser.
3. **Big actions have hidden side effects.** Listening updates a streak; adding to a playlist sends a notification. The obvious action isn't the whole story.
4. **IDs are long random codes, not simple numbers.** Every user/song/playlist has a random ID (like `a3f9c2...`), so you can't just guess "user 1" — you have to look the ID up.

---

## Root Cause Analysis Entries

*(Fill in during Milestone 3 — one entry per bug I fix, at least 3. Each must cover all 5 fields.)* 

### Issue #1 — My listening streak keeps resetting

**How I reproduced it:**
I ran the app's built-in streak tests with `pytest tests/test_streaks.py -v`. Four passed, but one failed: `test_streak_increments_on_sunday`. That test acts out a user listening on Saturday (streak = 1) and then again on Sunday, so the streak should climb to 2. Instead it dropped back to 1 — it forgot the earlier day instead of counting it. So the bug only appears when a streak carries over into a **Sunday**; on every other day it works fine.

**How I found the root cause:**
The failing test told me exactly which file to open: `services/streak_service.py`. Inside it, one function decides whether your streak goes up, stays the same, or resets. I read it line by line. The rule for "the user listened again the next day, so add 1 to their streak" had a strange extra requirement stuck onto it: it would only add to the streak *if today was not Sunday*. That one extra requirement lined up perfectly with the "only breaks on Sundays" symptom, which is how I knew I'd found the real cause and not just a suspicious-looking area.

**The root cause:**
In Python, the days of the week are numbered, and **Sunday is number 6**. The code that should have said "if the user listened yesterday, add 1 to their streak" actually said "if the user listened yesterday **and today isn't Sunday**, add 1." So on Sundays, a perfectly normal next-day listen failed that check and fell through to the "the user skipped a day" branch, which resets the streak all the way back to 1. The day of the week has nothing to do with counting consecutive days, so that extra condition should never have been there in the first place.

**My fix and side-effect check:**
I deleted the extra "and today isn't Sunday" part of the rule, so now *any* next-day listen adds to the streak, no matter what day it is. To make sure I didn't break anything, I re-ran `pytest tests/test_streaks.py -v` and all 5 tests now pass. The other four cover the everyday cases — a brand-new user starting at 1, a normal next-day increase, listening twice in one day (which shouldn't double-count), and skipping a day (which should reset). They all still pass, so the rest of the streak logic still behaves correctly.

---

### Issue #2 — Friends Listening Now shows people from yesterday

**How I reproduced it:**
In `flask shell` I called `get_friends_listening_now()` for user **darius** against the seeded data. His friend **simone** had a listening event ~15 minutes old (genuinely "now"), while his friend **nova** had one ~2 hours old (not "now"). The feed returned **2** friends — both simone (21:54) *and* nova (20:09) — so someone who listened hours earlier still showed up in a "listening now" feed. A correct feed should have shown only simone.

```
TOTAL friends shown: 2
simone -> 2026-07-01T21:54:...
nova   -> 2026-07-01T20:09:...
```

**How I found the root cause:**
I opened `services/feed_service.py`, the file behind the feed. Right at the top it defines a setting called `RECENT_THRESHOLD` — the "how recent counts as *now*" window — and it was set to **24 hours**. The rest of the function was fine: it correctly grabs a friend's most recent listen and lists each friend once. The only thing deciding who counts as "listening now" was that one setting, and 24 hours is clearly far too long for "now." That single line matched the complaint exactly, which is how I knew it was the real cause.

**The root cause:**
The "listening now" feed treats anyone who listened within the last **24 hours** as currently listening, because the recency window was set to 24 hours. "Listening now" is supposed to mean the last several minutes, so friends who listened hours ago — even the night before — kept showing up as if they were playing music right now. That's the "shows people from yesterday" complaint.

**My fix and side-effect check:**
I changed the window from 24 hours to **30 minutes** (`timedelta(minutes=30)`), which matches how the seed data is built — one friend listening ~15 minutes ago (genuinely now) and another ~2 hours ago (not now). After re-seeding the data and restarting the shell, the feed for darius returned only **simone** (~15 min ago) and correctly dropped **nova** (~2 hours ago). Because this is a boundary bug, I checked both sides of the 30-minute line: the recent friend still appears, the older one no longer does. I also confirmed I only touched the "listening now" feed — the separate activity feed (`get_activity_feed`) is intentionally *not* time-filtered, and I left it alone.

---

### Issue #4 — I got notified when a friend added my song to a playlist but not when they rated it

**How I reproduced it:**
In `flask shell` I counted nova's notifications, had **darius** rate a song nova had shared ("Midnight Drive") via `rate_song()`, then counted again. The count stayed the same (1 → 1), so no notification was created for the rating — even though the seed data already contains a working "song added to playlist" notification for nova. This confirms notifications fire for one interaction (playlist adds) but not the other (ratings).

```
Song rated: Midnight Drive | BEFORE: 1 | AFTER: 1 | new notif? False
```

**How I found the root cause:**
The brief hinted this was "architectural, not a typo," so instead of hunting for a broken line I compared the two interactions side by side in `services/notification_service.py`. The function that adds a song to a playlist (`add_to_playlist`) does its main job and then has a clear block that says "if you weren't the one who shared this song, notify the original sharer." I then read the rating function (`rate_song`) and it had no such block at all — it saved the rating and returned. The notification step wasn't broken; it was simply never written. Comparing the working path to the missing one is what made it obvious.

**The root cause:**
Notifications for a rating were never created because `rate_song` was missing the "notify the sharer" step entirely. The playlist path had it; the rating path did not. So a user got an alert when a friend added their song to a playlist, but rating produced silence — not because of a bug in the notification code, but because that code was never called from the rating flow.

**My fix and side-effect check:**
I added a notification step to `rate_song` that mirrors the working playlist code: after the rating is saved, if the person rating isn't the person who shared the song, it creates a `song_rated` notification for the sharer ("So-and-so rated your song 'X' N stars"). I kept the same "don't notify yourself" guard the playlist code uses. Design choice: I notify on every rating submission (matching the proven playlist pattern) rather than only on brand-new ratings. I verified in a fresh shell: darius rating nova's song raised her notification count from 1 to 2, the new notification was the correct `song_rated` type addressed to nova, and a user rating *their own* song created no notification (the guard works). I didn't change how ratings are saved, so existing rating behavior is untouched.

---

### Issue #5 — The last song in a playlist never shows up (stretch)

**How I reproduced it:**
I ran the app's playlist tests with `pytest tests/test_playlists.py -v`. The test `test_playlist_returns_all_songs` builds a playlist with 5 songs and expects `get_playlist_songs` to return 5, but it returned 4 — the final song was missing. The other tests confirmed the songs that *were* returned came back in the right order, so nothing was scrambled; one song at the end was simply being dropped.

**How I found the root cause:**
I opened `services/playlist_service.py` and read `get_playlist_songs` from top to bottom. The database query itself was correct — it fetches every song in the playlist and sorts them by their position number. But the very last line, which turns those songs into the final list, ended with `songs[:-1]`. In Python, `[:-1]` means "everything except the last item." So after correctly fetching all the songs in order, the code deliberately chopped off the last one right before returning. The function's own description even says it returns *all* songs, so the code contradicted its own documentation — that mismatch is what confirmed it.

**The root cause:**
The final list was built with `songs[:-1]`, which excludes the last element. Because the songs are sorted by position, "the last element" is always the last song in the playlist — so the highest-positioned song was always thrown away. This is a classic off-by-one error: a 5-song playlist showed 4, a 7-song playlist showed 6, and so on.

**My fix and side-effect check:**
I removed the `[:-1]` so the function returns the full list of songs. I re-ran `pytest tests/test_playlists.py -v` and all 3 tests pass: the playlist now returns all 5 songs, still in correct position order, and — the important boundary case — an empty playlist still returns an empty list without error (the old `[:-1]` happened to work on an empty list too, so I confirmed I didn't break that edge case).

---

## git log screenshot

`git log --oneline` on the `bugfix/mixtape` branch, showing one separate `fix:` commit per bug:

![git log --oneline showing one commit per bug fix on bugfix/mixtape](assets/Screenshot%202026-07-01%20214835.png)
