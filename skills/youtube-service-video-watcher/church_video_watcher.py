#!/usr/bin/env python3
"""
church_video_watcher.py

Checks a YouTube channel for a new service video after each scheduled
service time, and either auto-downloads it (via yt-dlp) or flags it
for manual confirmation.

Run this on a schedule (cron), shortly after each expected service time.
It does NOT need a Google API key. It uses yt-dlp for both listing and
downloading, since you already have that installed.
"""

import json
import subprocess
import sys
import os
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
STATE_PATH = os.path.join(SCRIPT_DIR, "state.json")
LOG_PATH = os.path.join(SCRIPT_DIR, "notifications.log")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_recent_videos(channel_url, limit=5):
    """Cheap listing: video id + title only, no full metadata yet."""
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--playlist-end", str(limit),
        channel_url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp listing failed: {result.stderr.strip()}")
    videos = []
    for line in result.stdout.strip().splitlines():
        if line:
            videos.append(json.loads(line))
    return videos


def get_video_details(video_id):
    """Full metadata for one candidate: upload date, duration, title."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = ["yt-dlp", "--dump-json", "--skip-download", url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp detail fetch failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def matches_criteria(details, config):
    """Content-level checks only: title keywords + minimum duration.

    Recency ("when was it posted?") is handled separately in main() so the
    match_strategy can decide whether a recency window applies. Returns
    (ok_to_flag: bool, reason: str).
    """
    title = (details.get("title") or "").lower()
    duration = details.get("duration") or 0  # seconds

    keywords = [k.lower() for k in config.get("title_keywords", [])]
    if keywords and not any(k in title for k in keywords):
        return False, "title does not match expected keywords"

    min_minutes = config.get("min_duration_minutes", 10)
    if duration < min_minutes * 60:
        return False, f"duration {duration}s is under the {min_minutes} min threshold"

    return True, "matched"


def within_window(details, config, now):
    """For the 'window' strategy: True if the upload is recent enough."""
    upload_date = details.get("upload_date")
    if not upload_date:
        return True, "no upload date available"
    upload_dt = datetime.strptime(upload_date, "%Y%m%d")
    max_days = config.get("match_window_days", 0)
    days_old = (now.date() - upload_dt.date()).days
    if days_old > max_days:
        return False, f"uploaded {days_old} day(s) ago, outside the {max_days} day match window"
    return True, "matched"


def resolve_dir(path):
    """Expand ~ and make relative paths relative to this script's folder.

    This keeps output_dir working the same way for every user and under
    cron, where the current working directory is not the script folder.
    """
    path = os.path.expanduser(path or "")
    if not os.path.isabs(path):
        path = os.path.join(SCRIPT_DIR, path)
    return os.path.normpath(path)


# MP4-friendly format: best 1080p-or-lower MP4 video (H.264/AV1) + AAC
# (m4a) audio, merged into a single .mp4 file. Matches the selection used
# by the companion content pipeline.
FORMAT_SELECTOR = "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best"


def download_video(video_id, output_dir):
    """Download a video into output_dir and return the final file path."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    output_dir = resolve_dir(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    cmd = [
        "yt-dlp",
        "-f", FORMAT_SELECTOR,
        "--merge-output-format", "mp4",
        "-o", os.path.join(output_dir, "%(upload_date)s_%(title)s.%(ext)s"),
        "--print", "after_move:filepath",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    paths = [line for line in result.stdout.strip().splitlines() if line]
    return paths[-1] if paths else None


ORGANIZE_SCRIPT = os.path.join(SCRIPT_DIR, "organize_sermon.py")


def organize_download(video_id, source_path):
    """Move a just-downloaded sermon into its series folder (best effort).

    Runs organize_sermon.py, which matches the video to a channel playlist
    (the series) and writes sermon.meta.json. Failures leave the file in
    the flat download folder rather than losing it.
    """
    if not source_path or not os.path.exists(ORGANIZE_SCRIPT):
        return
    try:
        subprocess.run(
            [sys.executable, ORGANIZE_SCRIPT, video_id, "--source", source_path],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except Exception as e:
        notify(f"Could not organize {video_id}: {e}")


def notify(message):
    """
    Wire this up to Slack/text/email later. For now it prints and logs
    to a file so nothing is silently missed if you're not watching.
    """
    print(f"[NOTIFY] {message}")
    with open(LOG_PATH, "a") as f:
        f.write(f"{datetime.now().isoformat()} {message}\n")


def main():
    config = load_json(CONFIG_PATH, None)
    if config is None:
        print("Missing config.json next to this script. This watcher hasn't "
              "been set up for a church yet - run: python3 setup.py")
        sys.exit(1)

    state = load_json(STATE_PATH, {"processed_ids": [], "pending": []})
    now = datetime.now()
    strategy = config.get("match_strategy", "newest")

    try:
        candidates = get_recent_videos(config["channel_url"], limit=5)
    except RuntimeError as e:
        notify(f"Could not check channel: {e}")
        sys.exit(1)

    new_candidates = [v for v in candidates if v["id"] not in state["processed_ids"]]

    if not new_candidates:
        notify("No new videos found since last check.")
        return

    # "newest" flags only the single most recent upload that passes the
    # content checks (keyword + duration). Because we iterate newest first,
    # the first content-match is the newest one; everything else is marked
    # processed so older videos never get re-flagged on later runs.
    flagged = False

    for video in new_candidates:
        try:
            details = get_video_details(video["id"])
        except RuntimeError as e:
            notify(f"Could not fetch details for {video['id']}: {e}")
            continue

        state["processed_ids"].append(video["id"])

        ok, reason = matches_criteria(details, config)
        if not ok:
            notify(f"Skipped '{details.get('title')}': {reason}")
            continue

        # Content looks like a service video. Apply strategy-specific rules.
        if strategy == "window":
            ok_w, reason_w = within_window(details, config, now)
            if not ok_w:
                notify(f"Skipped '{details.get('title')}': {reason_w}")
                continue
        else:  # "newest"
            if flagged:
                notify(f"Skipped '{details.get('title')}': already flagged the newest service video")
                continue

        flagged = True
        if config.get("auto_download", True):
            notify(f"Auto-downloading: {details['title']}")
            path = download_video(video["id"], config["output_dir"])
            notify(f"Downloaded: {details['title']}")
            if path:
                organize_download(video["id"], path)
        else:
            state["pending"].append(video["id"])
            notify(
                f"Possible service video found: '{details['title']}' "
                f"(id: {video['id']}).\n"
                f"Watch: https://www.youtube.com/watch?v={video['id']}\n"
                f"To download it, run: python3 confirm_download.py {video['id']}"
            )

    save_json(STATE_PATH, state)


if __name__ == "__main__":
    main()
