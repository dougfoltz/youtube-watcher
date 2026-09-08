---
name: youtube-watcher-setup
description: Guided setup of the YouTube service video watcher for a church's channel. Use when the user says they want to set up / install / configure a YouTube watcher, watch their church's YouTube channel automatically, or "set up a youtube watcher." Walks the user through prerequisites, collecting their channel and output folder, writing config.json (normalizing a pasted home link to the /videos tab), testing, and offering to schedule it. Depends on the youtube-service-video-watcher folder being present in the workspace.
---

# YouTube Watcher Setup

Set up the `youtube-service-video-watcher` for a new church. The most
common entry point is a fresh chat: the user says something like "I want
to set up a YouTube watcher" or "can we watch my church's YouTube
channel?" Drive the setup by talking to the user — do NOT hand them a JSON
file or a pile of commands.

This skill pairs with the `youtube-service-video-watcher` skill, whose
scripts (and `setup.py`) live in
`.agents/skills/youtube-service-video-watcher/`.

## When to use

- User wants to set up watching their church's YouTube channel.
- User asks "how do I get notified when my church posts the service video?"
- User opens a fresh chat and wants a YouTube watcher configured.

## Prerequisite check (do this first)

Check the environment:
- Locate the watcher folder — confirm the skill is present. If the
  `youtube-service-video-watcher` folder is missing, tell the user the
  watcher itself isn't installed yet and stop rather than improvising.
- Check `python3 --version` and `which yt-dlp` (bash). If either is
  missing, walk the user through installing it before continuing:
  - yt-dlp: `brew install yt-dlp` (Mac) or `pip install yt-dlp` (Windows)
  - Python 3: from python.org if `python3` is missing.

## The setup conversation (collect these from the user)

Ask one thing at a time, in plain language:

1. **Their church's YouTube channel.** They will almost always paste the
   whole channel homepage (e.g. `https://www.youtube.com/WOLCTV` or
   `@WOLCTV`) rather than the `/videos` tab. That is expected and fine —
   normalize it to the `/videos` tab automatically (see below). Reject
   anything that is a single video (`youtube.com/watch...`, `youtu.be/...`,
   `/shorts/...`) and ask for the channel instead.

2. **Where videos should be saved.** Default: `~/Videos/<Church> Service
   Videos`. Accept `~` paths.

3. **Optional** (announce defaults, only ask if the user wants to tweak):
   - Minimum minutes to count as a service — default 10 (filters Shorts).
   - Auto-download without asking — default no.
   - Title keywords — default none (only if their service videos share a
     phrase and they want to be extra precise).

## Normalize the channel URL (important)

Users paste home links; the watcher needs the `/videos` tab so it only
sees real uploads, not Shorts. Apply these rules (or use the helper in
`setup.py`):

| User pastes | Normalize to |
|---|---|
| `https://www.youtube.com/WOLCTV` | `https://www.youtube.com/WOLCTV/videos` |
| `@WOLCTV` | `https://www.youtube.com/@WOLCTV/videos` |
| `https://www.youtube.com/@Handle` | `.../@Handle/videos` |
| `https://www.youtube.com/channel/UC...` | `.../channel/UC.../videos` |
| `https://www.youtube.com/c/Name` | `.../c/Name/videos` |
| `www.youtube.com/YourChurch` (no scheme) | `https://www.youtube.com/YourChurch/videos` |
| already ends in `/videos` | keep as-is |
| `watch?v=...`, `youtu.be/...`, `shorts/...` | NOT a channel — ask again |

The canonical logic lives in `setup.py` as `normalize_channel_url()`.

## Write config.json

Prefer running `setup.py` non-interactively so normalization and defaults
are handled correctly. From the watcher folder:

```bash
cd /path/to/.agents/skills/youtube-service-video-watcher
python3 setup.py --yes \
  --channel "<normalized-or-pasted channel URL>" \
  --output-dir "<their folder>" \
  [--min-duration 10] \
  [--title-keywords "sunday service"] \
  [--auto-download]
```

`setup.py` normalizes the URL (if you passed the home link, it fixes it
and prints what it used), writes `config.json`, and prints the result.
Then read back `config.json` to confirm `channel_url` ends in `/videos`
and `output_dir` is what they asked for.

(If setup.py is unavailable, write config.json yourself using this
schema — and still apply the normalization rule above manually.)

```json
{
  "channel_url": "https://www.youtube.com/@YourChurch/videos",
  "title_keywords": [],
  "min_duration_minutes": 10,
  "auto_download": false,
  "match_strategy": "newest",
  "match_window_days": 0,
  "output_dir": "~/Videos/MyChurch-service-videos"
}
```

## Verify it works (run a check)

After config.json is written, run the watcher once to confirm it can read
their channel:

```bash
cd /path/to/.agents/skills/youtube-service-video-watcher
rm -f state.json          # so this run considers everything "new"
python3 church_video_watcher.py
```

Check that it lists/screens their recent uploads. With `newest`, it
should flag the most recent qualifying video (or cleanly skip Shorts and
older ones). Explain the result to the user in plain language — what it
flagged (if anything) and what "no new videos / skipped" means is normal.

Then remove the test `state.json`/`notifications.log` so their working
folder starts clean, unless the user wants to keep visibly watching.

## Offer to schedule it

Ask if they want the watcher to run automatically. Recommend running on
(or right after) their service day — e.g. a Monday 8am check catches a
Sunday service regardless of exactly when it was posted.

- In this workspace: create a weekly Mission Control automation
  (`create_automation`) for the day/time they choose.
- On their own machine: a local cron job (documented in the watcher
  skill's Scheduling section).

## Confirm and download

Tell them how downloads work: the watcher flags a video and writes the
video id to `notifications.log`; they confirm with
`python3 confirm_download.py <id>` (or flip `auto_download` to true once
they're comfortable). Downloads are saved as 1080p MP4 and automatically
organized into `Sermons/<Series>/<Sermon>/sermon.mp4` with a
`sermon.meta.json` (series is matched from the channel's playlists;
standalone sermons land in `Sermons/_Standalone/`).

## Wrap up

Summarize succinctly: channel being watched, where videos land, the next
scheduled run (if any), and how to download the first video.
