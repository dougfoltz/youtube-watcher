---
name: sermon-library-downloader
description: Backfills a church's whole back catalogue of sermons as transcripts (no video) so the sermon wiki can be populated and the pastor can ask questions across years of preaching. Use when the user wants to download their sermon library, backfill past sermons, get transcripts for every sermon, populate or seed the sermon wiki, or "talk to" their sermon library. Works in resumable batches of 7 sermons, one batch per agent turn, and picks up where it left off across sessions. Requires the church's config.json from youtube-downloader-setup.
---

# Sermon Library Downloader

Walks a church's entire YouTube back catalogue, newest sermon first, and
saves a **transcript** for each one into the "Past Sermons" folder in
Files. This is what populates the sermon wiki, so the pastor can ask
questions across years of preaching ("what have I said about lament?",
"when did I last preach Romans 8?").

**No video is downloaded.** Only metadata and captions. A library of
several years of sermons would be hundreds of gigabytes as video; as
transcripts it is a few megabytes. The weekly watcher
(`youtube-service-video-watcher`) is what downloads video, and only for
the current week's sermon.

## When to use

- User wants their past sermons / sermon library / back catalogue available.
- User wants to populate, seed, or fill in the sermon wiki.
- User wants to ask questions across all their sermons.
- User just finished setup and said yes to downloading their library.

## The batching rule (read this first)

A church can have several years of sermons — often hundreds. Fetching
them all in one go would run past the end of an agent turn and lose
progress, so the work is deliberately chopped up:

- **One batch is at most 7 sermons.** 7 is a safe ceiling with headroom
  (13 at a time has worked in practice). The script enforces it; a larger
  `--batch-size` is refused.
- **One batch per agent turn.** Run `--batch` once, report what happened,
  and end the turn. Do **not** loop `--batch` several times in a single
  turn trying to finish faster — that is exactly what the cap exists to
  prevent.
- **Progress is saved after every batch**, so the next turn (or a whole
  new chat next week) resumes where this one stopped. Nothing is
  re-downloaded.

### Turn-by-turn protocol

**Turn 1 — plan only.**
```bash
cd /path/to/.agents/skills/sermon-library-downloader
python3 backfill_library.py --plan
```
This enumerates every upload on the channel, maps each one to its series
(one pass over the channel's playlists, cached), works out which sermons
are already in the Past Sermons folder, and builds the queue newest-first.
On a large channel this alone can take a few minutes, which is why it gets
its own turn. Report the totals to the user in plain language ("You have
214 sermons; 0 are downloaded so far; that's 31 batches") and then stop.

**Every following turn — exactly one batch.**
```bash
python3 backfill_library.py --batch
```
Report which sermons were saved and how many remain, then end the turn.
The script's last line always tells you the state, e.g.:
```
NEXT: 187 sermon(s) still queued (27 more batch(es)). Run: python3 backfill_library.py --batch
```
Close the turn by telling the user the count and inviting them to
continue — they say "keep going" (or a scheduled automation fires) and you
run the next batch. When the queue empties the line reads `NEXT: nothing
left to download`, and you tell them the library is complete.

**Resuming in a new chat.** Don't re-plan. Check where things stand:
```bash
python3 backfill_library.py --status
```
Re-run `--plan` only to pick up sermons posted since the last plan, or
after the channel changes.

## Prerequisites

- The church must be set up: `config.json` in the sibling
  `youtube-service-video-watcher` folder supplies `channel_url`,
  `output_dir` (the "Past Sermons" folder in Files), and
  `min_duration_minutes`. If it's missing, run `youtube-downloader-setup`
  first — this skill refuses to guess.
- `yt-dlp` and Python 3 on PATH (the setup skill checks both).
- The "Past Sermons" folder must exist in Files.

## Files

- `backfill_library.py` — the driver (`--plan`, `--batch`, `--status`)
- `parse_transcript.py` — json3 captions → transcript / words / sentences
- `backfill_state.json` (runtime) — the queue and what's been done
- `backfill.log` (runtime) — append-only history of every run

## What gets saved per sermon

Into the same `<Series>/<YYYY-MM-DD - Sermon Title>/` tree the weekly
watcher uses, inside the "Past Sermons" folder:

| File | What it is |
|---|---|
| `sermon.transcript.txt` | one numbered sentence per line: `[012 @ 03:45] text` |
| `sermon.sentences.json` | per sentence: index, start/end ms, text, word range |
| `sermon.words.json` | per word: index, word, start/end ms |
| `sermon.captions.en.json3` | the raw caption file from YouTube |
| `sermon.info.json` | the raw yt-dlp metadata |
| `sermon.meta.json` | the contract other skills read (below) |

`sermon.meta.json` carries the watcher's fields (`video_id`, `title`,
`url`, `upload_date`, `duration_seconds`, `series`, `series_playlist_id`,
`downloaded_at`) plus: `speaker`, `has_transcript`, `sentence_count`,
`transcript_source` (`uploaded` when the church supplied its own captions,
`youtube-auto` otherwise), `has_video`, and `backfilled_at`.

The **sentence index is the shared handle** — wiki entries, quotes, and
clip boundaries all refer to sentences by index, so it must stay stable.
Re-running `parse_transcript.py` on the same captions file reproduces the
same indices.

Note on quality: YouTube's automatic captions have **no punctuation**, so
sentences are inferred from speech pauses and length. They're good enough
to search, quote, and summarize, but they are not a clean manuscript. When
a church uploads its own caption file, that is used instead and reads far
better (`transcript_source: uploaded`).

## How it decides what's missing

The **Past Sermons folder is the source of truth**, not the state file. A
sermon counts as done only if a folder for its `video_id` contains
`sermon.transcript.txt`. So:

- Deleting a sermon folder makes it get downloaded again on the next `--plan`.
- A sermon the watcher already filed as *video only* gets its transcript
  added to that same folder — no duplicate folder, and its existing
  `downloaded_at` is preserved.
- Uploads shorter than `min_duration_minutes` are skipped as Shorts or
  bumpers.
- Series comes from whichever channel playlist contains the video; a
  sermon in no playlist lands in `_Standalone/`. A sermon in several
  playlists takes the first one YouTube lists.

## Sermons without captions

Some videos have none — YouTube usually generates captions within a few
hours of upload, and some older videos never get them. These are recorded
separately, reported in each summary, and **not** retried on every batch
(they'd fail forever). To try them again later, after YouTube has caught
up:
```bash
python3 backfill_library.py --plan --retry-no-captions
```
Tell the user plainly: those sermons can't join the wiki until captions
exist, and it isn't something you can fix from here.

## Failures

A sermon that errors (private, deleted, region-blocked, a network blip)
goes to the back of the queue for **one** retry on a later batch. A second
failure drops it and says so. A fresh `--plan` clears the failure log and
retries everything still missing.

## Talking to the user

The user is a non-technical pastor. Don't narrate commands or paste log
output. Say what happened in their terms:

> Downloaded 7 more sermons — you're 42 of 214 in, about a quarter of the
> way. Want me to keep going?

Lead with progress, give a sense of how much is left, and make continuing
a single easy "yes". If they ask why it's in chunks, say each run has a
time limit so you work through the library a few at a time and never lose
your place.

## Troubleshooting

- **"No config.json"** — the church hasn't been set up; run
  `youtube-downloader-setup` first.
- **"The Past Sermons folder does not exist"** — create it in Files, or
  fix `output_dir` in `config.json`.
- **`--plan` says 0 queued but sermons are clearly missing** — they were
  probably filtered as too short; check `min_duration_minutes`, or look at
  the `no_captions` count in `--status`.
- **Every sermon lands in `_Standalone`** — the channel doesn't organize
  sermons into playlists, or the playlists couldn't be read. Harmless; the
  transcripts are still correct. `--plan --refresh-series` rebuilds the map.
- **A batch seems to hang** — a single sermon fetch can take a while on a
  slow connection. Let the batch finish; progress is saved per batch, and
  the next turn resumes.
