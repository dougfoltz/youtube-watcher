---
name: youtube-service-video-watcher
description: Watches a church's YouTube channel and flags the newly posted weekly service video so nobody has to check manually. Use when the user wants to detect when a new service/sermon video is uploaded, download it (automatically or after confirming), or be alerted the moment it appears. Covers setup, configuration, matching logic, testing against a past video, and scheduling. Requires only yt-dlp (no Google API key).
---

# YouTube Service Video Watcher

This skill watches a YouTube channel for newly posted service videos and
flags them shortly after each service, so a church doesn't have to check
manually. It lists recent uploads with `yt-dlp`, screens candidates, and
either auto-downloads a match or queues it for manual confirmation.

This skill is the operating manual for the actual scripts.

## When to use this skill

- User wants to be notified when the new Sunday/service video is posted.
- User wants to watch a church's weekly video automatically and flag or
  download it.
- User asks to set up a scheduled check of a YouTube channel.

## Files (all live together in this skill's folder)

- `church_video_watcher.py` — the main check script (run on a schedule)
- `confirm_download.py <video_id>` — download a flagged video after you
  confirm it's the right one
- `organize_sermon.py <video_id> --source <path>` — move a finished
  download into `<sermons_root>/<Series>/<Sermon>/` and write
  `sermon.meta.json`
- `setup.py` — interactive, guided creation of `config.json` (the
  recommended onboarding path for most users)
- `config.example.json` — hand-editable reference template
- `config.json` — the live runtime config (created by setup, not shared)
- `state.json` (created at runtime) — tracks `processed_ids` and `pending`
- `notifications.log` (created at runtime) — append-only log of every run

The scripts use `os.path.dirname(__file__)` to find config/state/log, so
keep all three script/config files together in one folder. No Google API
key is needed; everything goes through `yt-dlp`.

## Prerequisites

- Python 3 (`python3 --version`)
- `yt-dlp` on your PATH — `brew install yt-dlp` on Mac, `pip install
  yt-dlp` on Windows.

## How it works (church_video_watcher.py)

1. Reads `config.json`.
2. Lists the channel's 5 most recent uploads via
   `yt-dlp --flat-playlist --dump-json --playlist-end 5` (the limit is
   hardcoded to 5 in `get_recent_videos`).
3. Skips any id already in `state.json` `processed_ids`.
4. For each new candidate, fetches full metadata
   (`--dump-json --skip-download`) and applies `matches_criteria`
   (content checks only):
   - **Title keywords** — if `title_keywords` is non-empty, the title must
     contain at least one (case-insensitive).
   - **Min duration** — must be at least `min_duration_minutes`
     (default 10).
5. Applies the **match strategy** (see below) to decide whether to flag.
6. On a match:
   - `auto_download: true` → downloads to `output_dir`.
   - otherwise → adds the id to `state["pending"]` and logs the YouTube
     URL + the `confirm_download.py` command to run.
7. Everything is written to `notifications.log` via `notify()`.

## Config (config.json)

`config.example.json` is the shareable template. On a new machine/drop,
copy it to `config.json` and edit the two fields that differ per user —
the channel to watch and where downloads go:

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

- `channel_url` — must end in `/videos`.
- `title_keywords` — leave `[]` unless service videos share a phrase
  (e.g. `["sunday service"]`).
- `match_strategy` — how the watcher decides a video is "the" new one:
  - **`newest` (recommended default)** — flag the single most recent
    upload that passes the title/duration check, no matter how old. This
    is the flexible option: the person schedules the cron for when they
    *know* the video is ready, so the watcher just picks the newest one.
    Works for Sunday-evening churches, Monday uploads, etc. The watcher
    never re-flags an older video it already marked processed.
  - **`window`** — only flag uploads within `match_window_days`. Use only
    if you specifically want a strict recency guard.
- `match_window_days` — only used by the `window` strategy (old behavior:
  `0` = only today's uploads; raise temporarily to test against a past
  video). Ignored when `match_strategy` is `newest`.
- `auto_download` — keep `false` until the user has seen it catch a few
  real services, then flip to `true`.
- `output_dir` — each person's own folder. `~` is expanded to their home
  directory, and a relative path (e.g. `Videos/foundry-service-videos`)
  is resolved against the script's own folder, so it works no matter what
  directory the schedule runs from.

Note: `videos_to_check` is documented in the guide but not read by this
script — it always checks the 5 most recent uploads.

### Sharing this as a template

When giving this to someone else, share the skill folder — **not** your
`config.json`, which holds your own channel and paths. The other user
onboards in two minutes:

1. Installs yt-dlp and Python.
2. Runs `python3 setup.py` — it prompts for their church's channel URL
   and their output folder, and writes `config.json` with sensible
   defaults for everything else. (Advanced users can instead copy
   `config.example.json` to `config.json` and edit the two fields by
   hand.)
3. Runs `python3 church_video_watcher.py`.

The scripts find `config.json` next to themselves, so the folder is
self-contained and portable across machines.

## Daily use

Run the watcher by hand to test, or let a scheduler call it:
```bash
python3 church_video_watcher.py
```
Check `notifications.log`. On a match without auto-download it prints
something like:
```
Possible service video found: 'Sunday Service - ...' (id: ABC123xyz).
Watch: https://www.youtube.com/watch?v=ABC123xyz
To download it, run: python3 confirm_download.py ABC123xyz
```

Confirm and download a flagged video (it must be in the pending list):
```bash
python3 confirm_download.py ABC123xyz
```
This downloads to `output_dir` and removes the id from `pending`.

Downloads use yt-dlp's MP4 format selector
(`bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best`
with `--merge-output-format mp4`), so each video is saved as a single
`.mp4` at up to 1080p (H.264 or AV1 video + AAC audio), matching the
companion content pipeline's expected input.

Note: the source codec (H.264 vs AV1) intentionally does not matter here.
The companion flow decodes the downloaded MP4 and always re-encodes to
H.264 (`-c:v libx264`) for the finished clip. So keep this selector as a
plain "best ≤1080p MP4" — do NOT tighten it to a specific codec. Avoid
`--force-keyframes-at-cuts`, which misbehaves on AV1 sources; normal
`--download-sections` trimming works on both.

## Organized output (series / sermon folders)

After a download, `organize_sermon.py` moves the video into a tidy tree
under `output_dir/<sermons_root>/` and writes machine-readable metadata
next to it:

```
output_dir/
└── Sermons/                              (config: sermons_root, default "Sermons")
    ├── <Series Name>/                    ← the YouTube playlist the video belongs to
    │   └── <YYYY-MM-DD Sermon Title>/
    │       ├── sermon.mp4
    │       └── sermon.meta.json
    └── _Standalone/<Sermon>/             ← no series (video in no playlist)
```

**How the series is decided:** the video is matched against the channel's
playlists — whichever playlist contains it is the series, and the playlist
title becomes the series folder. A video in *no* playlist is a standalone
sermon → `_Standalone`. Playlists are scanned newest-first up to
`max_playlists_to_scan` (default 25). This assumes the church keeps each
sermon series as a YouTube playlist (the common pattern; WOLCTV does).

**`sermon.meta.json` is the contract other agents read:** `video_id`,
`title`, `url`, `upload_date`, `duration_seconds`, `series`,
`series_playlist_id`, `downloaded_at`. A folder containing
`sermon.meta.json` (or `sermon.mp4`) directly is a sermon; a folder
containing sermon folders is a series.

Organizing runs automatically after `confirm_download.py` and after
auto-downloads. If it fails it leaves the file in the flat `output_dir`
rather than losing it.

## Testing against a real past video

Don't wait for next Sunday. With the default `newest` strategy:

1. Delete `state.json` if it exists.
2. Run `python3 church_video_watcher.py`.
3. `notifications.log` should show the most recent service-length upload
   flagged as a match, and older qualifying uploads skipped with
   "already flagged the newest service video".

It doesn't matter how old the newest upload is — `newest` flags whatever
the most recent qualifying video happens to be.

(For the `window` strategy only: temporarily set `"match_window_days": 7`,
delete `state.json`, run, then reset it back to `0`.)

If nothing flags, the usual culprits are `min_duration_minutes` being too
strict or a `title_keywords` entry that's too specific.

## Scheduling

Use the machine's built-in scheduler. E.g. for a 10am Sunday service,
check at 1pm and 4pm (day `0` = Sunday):
```bash
which python3            # note the path
export EDITOR=nano
crontab -e               # add lines like:
# 0 13 * * 0 /usr/local/bin/python3 /path/to/this/folder/church_video_watcher.py >> /path/to/watcher.log 2>&1
# 0 16 * * 0 /usr/local/bin/python3 /path/to/this/folder/church_video_watcher.py >> /path/to/watcher.log 2>&1
```
`Control+O`, Enter to save, `Control+X` to exit, then `crontab -l` to
confirm. The computer must be on and awake at the scheduled times.

## Notifications

The current code's `notify()` writes to the log file and prints to
stdout only. Slack/email/text are **not yet wired in** — the code contains
a comment to add them later (Slack Incoming Webhook and Twilio text). Those
are manual follow-ups to the code.

## Troubleshooting

- **"No new videos found since last check"** — normal if nothing was
  posted between runs; not an error.
- **A video wasn't flagged or skipped at all** — only the 5 most recent
  uploads are checked; if the newest service video is beyond that, run
  more frequently or adjust the hardcoded `limit=5` in
  `get_recent_videos`.
- **Skipped "already flagged the newest service video"** — expected under
  `newest`: only the single most recent qualifying upload gets flagged.
- **Skipped "outside the match window"** — the `window` strategy and
  `match_window_days` is `0`; expected for older uploads under that mode.
- **Stuck in a `~`-filled editor (vim)** — type `:q!` + Enter, then set
  `export EDITOR=nano` before `crontab -e`.
- **yt-dlp "command not found"** — `yt-dlp` isn't installed or isn't on
  PATH; install it (see Prerequisites).
