---
name: youtube-downloader-setup
description: Guided setup of the YouTube service video watcher for a church's channel, ending with an offer to backfill their whole sermon library as transcripts. This is used when the user wants to download videos from their YouTube channel but have not yet setup the prerequisites to do so. Use when the user says they want to set up / install / configure a YouTube watcher, or download their church's sermons from their YouTube channel automatically. Walks the user through prerequisites, making sure the church's Past Sermons folder exists in Files, collecting their channel, writing config.json (normalizing a pasted home link to the /videos tab), testing, and offering to schedule it. Creates the Past Sermons folder in Files if it is missing; downloads land there. Downloading the sermon is a prerequisite for creating sermon clips and the sermon wiki.
---

# YouTube Watcher Setup

Set up the `youtube-service-video-watcher` for a new church. This agent
ships unconfigured — no church, channel, or output folder is baked in
anywhere. This skill is the onboarding path that personalizes it: running
setup writes `config.json` (the only church-specific file), and until it
has been run the watcher scripts will refuse to run and point here.

The most common entry point is a fresh chat: the user says something like
"I want to download my sermons or sermon library from YouTube" or "I want
to download my sermon to make a clip". Drive the setup by talking to the
user — do NOT hand them a JSON file or a pile of commands.

This skill pairs with the `youtube-service-video-watcher` skill, whose scripts (and `setup.py`) live in `.agents/skills/youtube-service-video-watcher/`.

It also hands off to `sermon-library-downloader` at the end, which
backfills the church's existing sermons as transcripts to populate the
sermon wiki. Setup covers sermons from now on; the backfill covers
everything already posted.

## When to use

- User wants to set up a sermon wiki.
- User wants to create sermon clips and needs their sermon video"
- User wants to repurpose their sermon and needs the sermon video to do it.

## Where things live (Files vs. Wiki)

Mission Control gives this agent two homes, and they are not
interchangeable:

- **Files** — where agent *outputs* live. The downloaded sermon video and
  its `sermon.meta.json` go here, in the church's **Past Sermons** folder.
- **Wiki** — where the *sermon wiki* lives, in a folder of the same name
  ("Past Sermons"): the linked entries for sermons, themes, concepts,
  Scriptures and so on across the sermon library. The wiki describes and
  cross-references sermons; it does not store video files.

The folder is named exactly **"Past Sermons"** in both places — same name
in Files and in the Wiki, so the video files and the wiki entries line up.

This skill is responsible only for Files. Point `output_dir` at the "Past
Sermons" folder in Files, not at the Wiki folder of the same name.

## The User
- The user is a pastor. They are non-technical. 
- Provide the user with step by step non-technical instructions. Do not say "brew install yt-dlp". Tell them which app to open. Provide them with commands or URLs in a code block to make it easy for them to copy the command. Apps like terminal are intimidating and the user may feel using it is unsafe. So reassure them and explain in a short conversational way what the command does and why they need it. 

## Prerequisite check (do this first)

Check the environment:
- **The "Past Sermons" folder in Files.** Look for a folder named `Past
  Sermons` in Files. If it isn't there, create it with exactly that name
  (matching the wiki folder). Every download lands inside it, so it has
  to exist before the watcher runs. You only create this one top-level
  folder — the watcher builds the tree underneath it on the first
  download (see *Folder structure* below).
- Check `python3 --version` and `which yt-dlp` (bash). If either is missing, walk the user through installing it before continuing:
  - yt-dlp: `brew install yt-dlp` (Mac) or `pip install yt-dlp` (Windows)
  - Python 3: from python.org if `python3` is missing.

## The setup conversation (collect these from the user)

Ask one thing at a time, in plain language:

1. **Their church's YouTube channel.** They will almost always paste the whole channel homepage (e.g. `https://www.youtube.com/YourChurch` or `@YourChurch`) rather than the `/videos` tab. That is expected and fine —normalize it to the `/videos` tab automatically (see below). Reject anything that is a single video (`youtube.com/watch...`, `youtu.be/...`, `/shorts/...`) and ask for the channel instead.

2. **Whether to download automatically.** Ask it in terms of the outcome,
   not the mechanism, and recommend yes:

   > When a new sermon is posted, should I download it for you
   > automatically, or just let you know it's ready and wait?

   Automatic is the default and the right answer for almost everyone: the
   video is simply there when they want to make clips. Do NOT ask
   "download without asking?" — that sounds risky and pushes a cautious
   pastor toward the answer that leaves them with manual commands to run.
   If they choose to be told first, honor it and explain they'll need to
   come back and ask you to fetch it.

Don't ask where to save the video — that's the "Past Sermons" folder in
Files, which you already located or created. Mention where it will land;
don't make it a question.


## Folder structure inside "Past Sermons"

Downloads are organized exactly the way the sermon wiki is organized —
by series, then by individual sermon:

```
Past Sermons/                       (in Files)
├── <Series Name>/                  ← the YouTube playlist the video belongs to
│   └── <YYYY-MM-DD - Sermon Title>/
│       ├── sermon.mp4
│       └── sermon.meta.json
└── _Standalone/<Sermon>/           ← sermon in no playlist
```

`organize_sermon.py` creates this automatically after each download —
the series comes from whichever channel playlist contains the video. You
don't pre-create series folders. `sermon.meta.json` is the contract the
wiki and clip skills read (`video_id`, `title`, `url`, `upload_date`,
`duration_seconds`, `series`, `series_playlist_id`, `downloaded_at`).

## Normalize the channel URL (important)

Users paste home links; the watcher needs the `/videos` tab so it only
sees real uploads, not Shorts. Apply these rules (or use the helper in
`setup.py`):

| User pastes | Normalize to |
|---|---|
| `https://www.youtube.com/YourChurch` | `https://www.youtube.com/YourChurch/videos` |
| `@YourChurch` | `https://www.youtube.com/@YourChurch/videos` |
| `https://www.youtube.com/@Handle` | `.../@Handle/videos` |
| `https://www.youtube.com/channel/UC...` | `.../channel/UC.../videos` |
| `https://www.youtube.com/c/Name` | `.../c/Name/videos` |
| `www.youtube.com/YourChurch` (no scheme) | `https://www.youtube.com/YourChurch/videos` |
| already ends in `/videos` | keep as-is |
| `watch?v=...`, `youtu.be/...`, `shorts/...` | NOT a channel — ask again |

The canonical logic lives in `setup.py` as `normalize_channel_url()`.

## Write config.json

Prefer running `setup.py` non-interactively so normalization and defaults are handled correctly:

```bash
cd /path/to/.agents/skills/youtube-service-video-watcher
python3 setup.py --yes \
  --channel "<normalized-or-pasted channel URL>" \
  --output-dir "<the church's Past Sermons folder in Files>" \
  [--min-duration 10] \
  [--title-keywords "sunday service"] \
  [--no-auto-download]
```

`--output-dir` is required and must be the Past Sermons folder in Files.
Auto-download is on unless you pass `--no-auto-download`.

`setup.py` normalizes the URL (if you passed the home link, it fixes it and prints what it used), writes `config.json`, and prints the result. Then read back `config.json` to confirm `channel_url` ends in `/videos`
and `output_dir` is the Past Sermons folder in Files.

(If setup.py is unavailable, write config.json yourself using this schema — and still apply the normalization rule above manually.)

```json
{
  "channel_url": "https://www.youtube.com/@YourChurch/videos",
  "title_keywords": [],
  "min_duration_minutes": 10,
  "auto_download": true,
  "match_strategy": "newest",
  "match_window_days": 0,
  "output_dir": "<the church's Past Sermons folder in Files>",
  "sermons_root": "",
  "max_playlists_to_scan": 25
}
```

## Download Defaults
   - Minimum minutes to count as a service — default 10 (filters Shorts).
   - Auto-download — default **yes**. This is what makes the agent usable
     for a non-technical pastor: the sermon is already downloaded and
     filed when they come looking for it. With it off, a scheduled run
     does nothing until someone comes back and asks. The protection
     against grabbing the wrong video is the duration and keyword
     filters, which run before any download either way.
   - Title keywords — default none
   - `sermons_root` — default empty, so series folders sit directly in
     the Past Sermons folder.

## Verify it works (run a check)

After config.json is written, run the watcher once to confirm it can read
their channel:

```bash
cd /path/to/.agents/skills/youtube-service-video-watcher
python3 church_video_watcher.py
```

**With auto-download on, this first run really does download their most
recent sermon.** That is intentional and usually exactly what they want —
it's the sermon they came here to work with — but tell them it's
happening first, since a full service is a large file and takes a few
minutes. If they'd rather not fetch anything yet, run setup with
`--no-auto-download`, verify, then turn it on.

Check that it screened their recent uploads sensibly: with `newest` it
takes the most recent qualifying video and cleanly skips Shorts and older
uploads. Explain the result in plain language, and where the file landed.

**Do not delete `state.json` after this run.** It records which videos
have been handled; wiping it makes the next run treat the same sermon as
new and download it a second time. (Deleting it is a deliberate
re-test-from-scratch move, not cleanup.) Leaving `notifications.log` in
place is fine — it's the running history of what the watcher has done.

## Offer to schedule it

Ask if they want the watcher to run automatically. Recommend running on(or right after) their service day — e.g. a Monday 8am check catches a Sunday service regardless of exactly when it was posted.

- In this workspace: create a weekly Mission Control automation
  (`create_automation`) for the day/time they choose.

## How downloads work

Explain it in plain language, matching what they chose:

- **Automatic (the default).** Each new sermon is downloaded and filed on
  its own, into `<Series>/<YYYY-MM-DD - Sermon Title>/sermon.mp4` inside
  the Past Sermons folder in Files. There is nothing for them to run or
  remember. Tell them where to look and that it appears after each
  scheduled check.
- **Tell me first.** The watcher notes the new sermon in
  `notifications.log` and waits. They come back and ask you to download
  it (you run `python3 confirm_download.py <id>`). Be explicit that the
  video won't exist until they do.

Either way, videos are saved as 1080p MP4 and organized the same way, and
each sermon gets a `sermon.meta.json` next to it.

## Offer to download their sermon library (do this before wrapping up)

Setup only covers sermons *from now on*. Once `config.json` is written and
verified, offer to go back and pull in everything already on their
channel, and say why it's worth doing:

> I can also go back through your channel and pull in your past sermons —
> just the transcripts, not the videos. That's what fills in your sermon
> wiki, so you can ask me things like "what have I preached about lament?"
> or "when did I last teach Romans 8?" across your whole library. Want me
> to start?

Points to make, briefly and in their language:

- **Transcripts only.** No video files, so it doesn't fill up their
  storage. Years of sermons come to a few megabytes.
- **It happens in chunks.** You work through about 7 sermons at a time and
  pick up where you left off, so it may span several exchanges (or days)
  for a big library. They don't have to sit and wait.
- **They can stop and resume any time**, including in a new chat. Nothing
  is ever downloaded twice.

If they say yes, hand off to the `sermon-library-downloader` skill and
follow its turn-by-turn protocol: `--plan` on this turn, then one
`--batch` per turn after that. If they say no or "later", leave it — the
weekly watcher still works on its own, and they can ask for the library
backfill whenever they want.

## Wrap up

Summarize succinctly: the channel being watched, where sermons land (the
Past Sermons folder in Files, organized by series), whether downloads
happen automatically, the next scheduled run if any, and whether the
library backfill is under way (and how far along) or still available to
start later.

