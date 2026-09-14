#!/usr/bin/env python3
"""Backfill a church's sermon library as transcripts. No video is downloaded.

Three phases, meant to be run as separate agent turns:

    python3 backfill_library.py --plan     # enumerate the channel, build the queue
    python3 backfill_library.py --batch    # process the next 7 sermons
    python3 backfill_library.py --status    # progress, without doing any work

`--batch` is deliberately small and resumable: it handles at most 7
sermons (MAX_BATCH) and then stops, so one batch fits comfortably inside a
single time-limited agent turn. Run it again for the next 7, until
`--status` reports nothing remaining.

Church settings come from the watcher skill's config.json (`channel_url`,
`output_dir`, `min_duration_minutes`), so the library lands in the same
"Past Sermons" folder in Files, in the same <Series>/<Sermon>/ tree, as
sermons captured by the weekly watcher.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHER_DIR = os.path.normpath(
    os.path.join(SCRIPT_DIR, os.pardir, "youtube-service-video-watcher")
)
DEFAULT_CONFIG = os.path.join(WATCHER_DIR, "config.json")
STATE_PATH = os.path.join(SCRIPT_DIR, "backfill_state.json")
LOG_PATH = os.path.join(SCRIPT_DIR, "backfill.log")
PARSE_SCRIPT = os.path.join(SCRIPT_DIR, "parse_transcript.py")

# The tested-safe ceiling. 13 has worked in practice; 7 leaves headroom so
# a batch never runs past the end of an agent turn.
MAX_BATCH = 7
DEFAULT_MAX_PLAYLISTS = 100


# --------------------------------------------------------------------------
# small helpers (mirrors of the watcher's, so this script stands alone)
# --------------------------------------------------------------------------

def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (ValueError, OSError):
        return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def log(message):
    print(message)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
    except OSError:
        pass


def resolve_dir(path):
    path = os.path.expanduser(path or "")
    if not os.path.isabs(path):
        path = os.path.join(SCRIPT_DIR, path)
    return os.path.normpath(path)


def sanitize(name):
    name = re.sub(r'[\\/:*?"<>|]', "", name or "")
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name[:120] or "Untitled"


def run_json(cmd, timeout=300):
    """Run a yt-dlp command that emits one JSON object per line."""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()[:400]}")
    rows = []
    for line in result.stdout.strip().splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def format_date(raw):
    raw = str(raw or "")
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 else ""


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------

def enumerate_uploads(channel_url):
    """Every upload on the channel, newest first (id + title + duration)."""
    rows = run_json(
        ["yt-dlp", "--flat-playlist", "--dump-json", channel_url], timeout=900
    )
    uploads = []
    for row in rows:
        vid = row.get("id")
        if vid:
            uploads.append({
                "id": vid,
                "title": row.get("title") or "",
                "duration": row.get("duration"),
            })
    return uploads


def build_series_map(channel_url, max_playlists):
    """video_id -> {series, playlist_id}, from one pass over the playlists.

    Built once during --plan and cached in the state file. Looking the
    series up per sermon instead would re-scan every playlist for every
    video, which is unusably slow across a whole library.

    A video in several playlists takes the first one found (playlists come
    back newest-first), so a sermon in both "Romans" and "Best of 2025"
    files under whichever YouTube lists first.
    """
    base = channel_url.rstrip("/")
    if base.endswith("/videos"):
        base = base[: -len("/videos")]
    try:
        playlists = run_json(
            ["yt-dlp", "--flat-playlist", "--dump-json", base + "/playlists"],
            timeout=300,
        )
    except RuntimeError as e:
        log(f"Warning: could not list playlists ({e}); every sermon will be standalone.")
        return {}

    series_by_video = {}
    scanned = 0
    for entry in playlists[:max_playlists]:
        pid, ptitle = entry.get("id"), entry.get("title")
        if not pid:
            continue
        try:
            members = run_json(
                ["yt-dlp", "--flat-playlist", "--dump-json",
                 f"https://www.youtube.com/playlist?list={pid}"],
                timeout=300,
            )
        except RuntimeError:
            continue  # unreadable playlist: skip, don't abort the plan
        scanned += 1
        for member in members:
            vid = member.get("id")
            if vid and vid not in series_by_video:
                series_by_video[vid] = {"series": ptitle, "playlist_id": pid}
    log(f"Scanned {scanned} playlist(s); mapped {len(series_by_video)} videos to a series.")
    return series_by_video


def scan_existing(root):
    """What's already on disk: video_id -> {dir, has_transcript}."""
    existing = {}
    if not os.path.isdir(root):
        return existing
    for dirpath, _dirnames, filenames in os.walk(root):
        if "sermon.meta.json" not in filenames:
            continue
        meta = load_json(os.path.join(dirpath, "sermon.meta.json"), {})
        vid = meta.get("video_id")
        if vid:
            existing[vid] = {
                "dir": dirpath,
                "has_transcript": "sermon.transcript.txt" in filenames,
            }
    return existing


def do_plan(config, args):
    channel_url = config.get("channel_url")
    output_dir = resolve_dir(config.get("output_dir"))
    min_minutes = int(config.get("min_duration_minutes", 10) or 0)

    if not channel_url:
        log("config.json has no channel_url. Run the watcher's setup.py first.")
        return 1
    if not os.path.isdir(output_dir):
        log(f"The Past Sermons folder does not exist: {output_dir}\n"
            "Create it in Files (or fix output_dir in config.json) before planning.")
        return 1

    log(f"Enumerating uploads on {channel_url} ...")
    try:
        uploads = enumerate_uploads(channel_url)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        log(f"Could not list the channel: {e}")
        return 1
    log(f"Channel has {len(uploads)} upload(s).")

    state = load_json(STATE_PATH, {})
    # A different channel invalidates everything we knew.
    if state.get("channel_url") not in (None, channel_url):
        log("Channel changed since the last plan; starting a fresh backfill.")
        state = {}

    completed = set(state.get("completed", []))
    no_captions = set(state.get("no_captions", []))

    series_map = state.get("series_by_video") or {}
    if args.refresh_series or not series_map:
        series_map = build_series_map(channel_url, args.max_playlists)
    else:
        log(f"Reusing cached series map ({len(series_map)} videos).")

    existing = scan_existing(output_dir)
    log(f"Found {len(existing)} sermon(s) already in the Past Sermons folder.")

    queue, skipped_short, already_done = [], 0, 0
    for upload in uploads:
        vid = upload["id"]
        duration = upload.get("duration")
        # Shorts / bumpers: only trust a duration we actually have.
        if duration is not None and min_minutes and duration < min_minutes * 60:
            skipped_short += 1
            continue
        on_disk = existing.get(vid)
        if on_disk and on_disk["has_transcript"]:
            already_done += 1
            completed.add(vid)
            continue

        # The Past Sermons folder is the source of truth, not this script's
        # state file: no transcript on disk means the sermon still needs
        # one, whatever an earlier run recorded. That keeps the backfill
        # honest if a folder is deleted, moved, or only half-written.
        completed.discard(vid)

        if vid in no_captions and not args.retry_no_captions:
            continue
        series = series_map.get(vid) or {}
        queue.append({
            "id": vid,
            "title": upload["title"],
            "duration": duration,
            "series": series.get("series"),
            "playlist_id": series.get("playlist_id"),
            # Set when the video is already filed: the transcript joins it
            # in that folder rather than starting a second one.
            "existing_dir": on_disk["dir"] if on_disk else None,
        })

    state = {
        "channel_url": channel_url,
        "planned_at": datetime.now().isoformat(timespec="seconds"),
        "queue": queue,
        "completed": sorted(completed),
        "no_captions": sorted(no_captions - set(q["id"] for q in queue)),
        # A fresh plan clears the failure log: the queue it just built is
        # the new source of truth, and anything still missing is retried.
        "failed": [],
        "series_by_video": series_map,
    }
    save_json(STATE_PATH, state)

    log("")
    log("Plan:")
    log(f"  already have a transcript : {already_done}")
    log(f"  skipped as too short      : {skipped_short} (under {min_minutes} min)")
    log(f"  no captions on YouTube    : {len(state['no_captions'])}")
    log(f"  queued to download        : {len(queue)}")
    if queue:
        batches = (len(queue) + MAX_BATCH - 1) // MAX_BATCH
        log(f"  -> {batches} batch(es) of up to {MAX_BATCH}")
        log(f"  newest queued: {queue[0]['title'][:70]}")
        log(f"  oldest queued: {queue[-1]['title'][:70]}")
    log("")
    log(next_step_line(len(queue)))
    return 0


# --------------------------------------------------------------------------
# one sermon
# --------------------------------------------------------------------------

def derive_speaker(title, channel):
    """Best-effort speaker from the title, else the channel name.

    Church titles commonly read "Sermon Title || Pastor Jane Doe" or
    "Sermon Title with Pastor Jane Doe". Anything less clear falls back to
    the channel, which is right more often than a bad guess.
    """
    if "||" in title:
        tail = title.split("||")[-1].strip()
        if tail:
            return tail
    match = re.search(r"\bwith\s+([^|•\-–—]{3,60})$", title, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return channel or None


def fetch_sermon(video_id, work_dir):
    """Metadata + English captions for one video. Never downloads video.

    Both --write-subs and --write-auto-subs are passed so a caption file
    the church uploaded itself (punctuated, more accurate) wins over
    YouTube's automatic one.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "en.*,en",
        "--sub-format", "json3",
        "-o", os.path.join(work_dir, "src"),
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    info_path = os.path.join(work_dir, "src.info.json")
    if not os.path.exists(info_path):
        raise RuntimeError(result.stderr.strip()[:400] or "no metadata returned")

    info = load_json(info_path, {})
    captions = sorted(
        f for f in os.listdir(work_dir)
        if f.startswith("src.") and f.endswith(".json3")
    )
    caption_path = os.path.join(work_dir, captions[0]) if captions else None

    # Filenames are identical for uploaded and automatic captions, so the
    # only way to tell them apart is which track the metadata lists.
    has_manual = any(k.lower().startswith("en") for k in (info.get("subtitles") or {}))
    source = "uploaded" if has_manual else "youtube-auto"
    return info, info_path, caption_path, source


def process_one(entry, output_dir, sermons_root):
    """Fetch + file one sermon. Returns (status, detail)."""
    video_id = entry["id"]
    with tempfile.TemporaryDirectory(prefix="sermon-backfill-") as work_dir:
        try:
            info, info_path, caption_path, caption_source = fetch_sermon(video_id, work_dir)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            return "failed", str(e)

        title = info.get("title") or entry.get("title") or "Untitled"
        upload_date = format_date(info.get("upload_date"))

        if caption_path is None:
            # Not a failure worth retrying on a loop: YouTube may simply
            # not have generated captions (it usually does within hours of
            # an upload, and some older videos never get them).
            return "no_captions", title

        series = entry.get("series")
        series_dir = sanitize(series) if series else "_Standalone"
        folder = f"{upload_date} - {sanitize(title)}" if upload_date else sanitize(title)
        # If the weekly watcher already filed this sermon's video, add the
        # transcript to that folder instead of creating a second one whose
        # name might differ by a character.
        target_dir = entry.get("existing_dir") or os.path.join(
            output_dir, sermons_root, series_dir, folder
        )
        os.makedirs(target_dir, exist_ok=True)

        captions_dest = os.path.join(target_dir, "sermon.captions.en.json3")
        shutil.copyfile(caption_path, captions_dest)
        shutil.copyfile(info_path, os.path.join(target_dir, "sermon.info.json"))

        parsed = subprocess.run(
            [sys.executable, PARSE_SCRIPT,
             "--json3", captions_dest,
             "--transcript", os.path.join(target_dir, "sermon.transcript.txt"),
             "--words", os.path.join(target_dir, "sermon.words.json"),
             "--sentences", os.path.join(target_dir, "sermon.sentences.json")],
            capture_output=True, text=True, timeout=300,
        )
        if parsed.returncode != 0:
            return "failed", f"transcript parse failed: {parsed.stdout.strip() or parsed.stderr.strip()[:200]}"

        sentences = load_json(os.path.join(target_dir, "sermon.sentences.json"), [])
        meta_path = os.path.join(target_dir, "sermon.meta.json")
        meta = load_json(meta_path, {})  # preserve fields if a video already filed here
        meta.update({
            "video_id": video_id,
            "title": title,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "upload_date": upload_date or None,
            "duration_seconds": info.get("duration"),
            "series": series,
            "series_playlist_id": entry.get("playlist_id"),
            "speaker": derive_speaker(title, info.get("channel")),
            "has_transcript": True,
            "sentence_count": len(sentences),
            "transcript_source": caption_source,
            "has_video": os.path.exists(os.path.join(target_dir, "sermon.mp4")),
            "backfilled_at": datetime.now().isoformat(timespec="seconds"),
        })
        meta.setdefault("downloaded_at", meta["backfilled_at"])
        save_json(meta_path, meta)
        return "done", os.path.relpath(target_dir, output_dir)


# --------------------------------------------------------------------------
# batching
# --------------------------------------------------------------------------

def next_step_line(remaining):
    if remaining <= 0:
        return "NEXT: nothing left to download - the sermon library is complete."
    batches = (remaining + MAX_BATCH - 1) // MAX_BATCH
    return (f"NEXT: {remaining} sermon(s) still queued ({batches} more batch(es)). "
            "Run: python3 backfill_library.py --batch")


def do_batch(config, args):
    state = load_json(STATE_PATH, None)
    if not state or "queue" not in state:
        log("No backfill plan yet. Run: python3 backfill_library.py --plan")
        return 1

    size = min(args.batch_size, MAX_BATCH)
    queue = state.get("queue", [])
    if not queue:
        log(next_step_line(0))
        return 0

    output_dir = resolve_dir(config.get("output_dir"))
    sermons_root = config.get("sermons_root", "") or ""
    batch, rest = queue[:size], queue[size:]

    log(f"Processing {len(batch)} sermon(s); {len(rest)} will remain after this batch.")
    done, no_caps, failed = [], [], []

    for entry in batch:
        label = (entry.get("title") or entry["id"])[:70]
        if args.dry_run:
            log(f"  [dry-run] would fetch: {label}")
            continue
        status, detail = process_one(entry, output_dir, sermons_root)
        if status == "done":
            done.append(entry["id"])
            log(f"  transcript saved: {label}  ->  {detail}")
        elif status == "no_captions":
            no_caps.append(entry["id"])
            log(f"  no captions available: {label}")
        else:
            failed.append({"id": entry["id"], "title": entry.get("title"), "error": detail})
            log(f"  FAILED: {label}  ({detail})")

    if args.dry_run:
        log("[dry-run] state unchanged.")
        return 0

    # Anything that failed goes to the back of the queue for one more try
    # on a later batch; a second failure is reported and dropped.
    retry, permanent = [], []
    previously_failed = {f["id"] for f in state.get("failed", [])}
    for failure in failed:
        entry = next((e for e in batch if e["id"] == failure["id"]), None)
        if failure["id"] in previously_failed or entry is None:
            permanent.append(failure)
        else:
            retry.append(entry)

    state["queue"] = rest + retry
    state["completed"] = sorted(set(state.get("completed", [])) | set(done))
    state["no_captions"] = sorted(set(state.get("no_captions", [])) | set(no_caps))
    state["failed"] = state.get("failed", []) + failed
    state["last_batch_at"] = datetime.now().isoformat(timespec="seconds")
    save_json(STATE_PATH, state)

    log("")
    log(f"Batch complete: {len(done)} transcript(s) saved, "
        f"{len(no_caps)} without captions, {len(failed)} failed.")
    if permanent:
        log(f"  {len(permanent)} sermon(s) failed twice and were dropped from the queue.")
    if retry:
        log(f"  {len(retry)} sermon(s) requeued for one retry.")
    log(f"Library totals: {len(state['completed'])} transcript(s) saved so far.")
    log(next_step_line(len(state["queue"])))
    return 0


def do_status(config, args):
    state = load_json(STATE_PATH, None)
    if not state:
        log("No backfill has been planned yet. Run: python3 backfill_library.py --plan")
        return 0
    queue = state.get("queue", [])
    log(f"Channel        : {state.get('channel_url')}")
    log(f"Planned at     : {state.get('planned_at')}")
    log(f"Last batch     : {state.get('last_batch_at') or '(none yet)'}")
    log(f"Transcripts    : {len(state.get('completed', []))} saved")
    log(f"No captions    : {len(state.get('no_captions', []))}")
    log(f"Failed         : {len(state.get('failed', []))}")
    log(f"Still queued   : {len(queue)}")
    if queue:
        log(f"Next up        : {queue[0].get('title', '')[:70]}")
    log(next_step_line(len(queue)))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="Enumerate the channel and build the queue.")
    mode.add_argument("--batch", action="store_true", help=f"Process the next {MAX_BATCH} queued sermons.")
    mode.add_argument("--status", action="store_true", help="Show progress without doing any work.")
    parser.add_argument("--config", default=DEFAULT_CONFIG,
                        help="Path to the watcher's config.json (default: the sibling watcher skill).")
    parser.add_argument("--batch-size", type=int, default=MAX_BATCH,
                        help=f"Sermons per batch, 1-{MAX_BATCH} (default and maximum {MAX_BATCH}).")
    parser.add_argument("--max-playlists", type=int, default=DEFAULT_MAX_PLAYLISTS,
                        help="Playlists to scan when building the series map.")
    parser.add_argument("--refresh-series", action="store_true",
                        help="Rebuild the cached playlist/series map during --plan.")
    parser.add_argument("--retry-no-captions", action="store_true",
                        help="Requeue sermons previously found to have no captions.")
    parser.add_argument("--dry-run", action="store_true", help="With --batch, show what would be fetched.")
    args = parser.parse_args()

    if args.batch_size < 1 or args.batch_size > MAX_BATCH:
        print(f"--batch-size must be between 1 and {MAX_BATCH}. Batches are capped so "
              "one batch always finishes inside a single agent turn.")
        return 1

    config = load_json(args.config, None)
    if config is None:
        print(f"No config.json at {args.config}\n"
              "This church hasn't been set up yet - run the youtube-downloader-setup "
              "skill (or the watcher's setup.py) first.")
        return 1

    if args.plan:
        return do_plan(config, args)
    if args.batch:
        return do_batch(config, args)
    return do_status(config, args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled. Progress through the last completed sermon is saved.")
        sys.exit(1)
