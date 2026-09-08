#!/usr/bin/env python3
"""Organize a downloaded sermon into <sermons_root>/<Series>/<Sermon>/.

Moves a freshly downloaded service video into a tidy folder tree and
writes a machine-readable sermon.meta.json alongside it, so other agents
can find the video, its series, and its metadata without re-parsing
YouTube.

How the series is decided:
  * The sermon video is matched against the channel's playlists. Whichever
    playlist contains the video id is treated as the series (its title
    becomes the series folder). This assumes the church keeps each sermon
    series as a YouTube playlist, which is the common pattern.
  * If the video is in no playlist, it is a standalone sermon → it goes
    under <sermons_root>/_Standalone/<Sermon>/.

Usage:
    python3 organize_sermon.py <video_id> --source <path/to/sermon.mp4>
    python3 organize_sermon.py <video_id> --source <path> --dry-run   # preview only
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from church_video_watcher import load_json, CONFIG_PATH  # noqa: E402

DEFAULT_SERMONS_ROOT = "Sermons"
DEFAULT_MAX_PLAYLISTS_SCAN = 25


def run_json(cmd):
    """Run a yt-dlp command and return the parsed JSON line(s)."""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed ({' '.join(cmd)}): {result.stderr.strip()}")
    out = []
    for line in result.stdout.strip().splitlines():
        if line:
            out.append(json.loads(line))
    return out


def sanitize(name):
    """Turn a playlist/video title into a safe folder name."""
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name[:120] or "Untitled"


def get_video_info(video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    rows = run_json(["yt-dlp", "--dump-json", "--skip-download", url])
    return rows[0]


def get_channel_playlists(channel_url):
    """Return [{id, title}] for the channel. Swap the /videos tab for /playlists."""
    base = channel_url.rstrip("/")
    if base.endswith("/videos"):
        base = base[: -len("/videos")]
    playlists_url = base + "/playlists"
    entries = run_json(["yt-dlp", "--flat-playlist", "--dump-json", playlists_url])
    return [{"id": e.get("id"), "title": e.get("title")} for e in entries if e.get("id")]


def video_in_playlist(video_id, playlist_id):
    url = f"https://www.youtube.com/playlist?list={playlist_id}"
    entries = run_json(["yt-dlp", "--flat-playlist", "--dump-json", url])
    return any(e.get("id") == video_id for e in entries)


def find_series(video_id, playlists, max_scan):
    """Return (series_title, playlist_id) or (None, None) if standalone."""
    for pl in playlists[:max_scan]:
        try:
            if video_in_playlist(video_id, pl["id"]):
                return pl["title"], pl["id"]
        except RuntimeError:
            continue  # skip playlists we can't read; don't abort
    return None, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video_id", help="YouTube video id of the downloaded sermon")
    parser.add_argument("--source", required=True, help="Path to the downloaded sermon file")
    parser.add_argument("--dry-run", action="store_true", help="Preview the target path without moving anything")
    args = parser.parse_args()

    config = load_json(CONFIG_PATH, {})
    output_dir = os.path.expanduser(config.get("output_dir", ""))
    sermons_root = config.get("sermons_root", DEFAULT_SERMONS_ROOT)
    max_scan = int(config.get("max_playlists_to_scan", DEFAULT_MAX_PLAYLISTS_SCAN))

    source = os.path.abspath(os.path.expanduser(args.source))
    if not os.path.exists(source):
        print(f"Source file not found: {source}")
        sys.exit(1)

    # --- video metadata ------------------------------------------------
    try:
        info = get_video_info(args.video_id)
    except RuntimeError as e:
        print(f"Could not fetch video metadata: {e}")
        sys.exit(1)
    title = info.get("title") or "Untitled"
    upload_date = str(info.get("upload_date") or "")
    if len(upload_date) == 8:
        upload_date = f"{upload_date[0:4]}-{upload_date[4:6]}-{upload_date[6:8]}"

    # --- series (any playlist the video belongs to = series) -----------
    series_title, series_playlist_id = None, None
    try:
        playlists = get_channel_playlists(config.get("channel_url", ""))
        series_title, series_playlist_id = find_series(args.video_id, playlists, max_scan)
    except RuntimeError as e:
        print(f"Warning: could not look up playlists ({e}); treating as standalone.")

    if series_title:
        series_dir = os.path.join(sermons_root, sanitize(series_title))
    else:
        series_dir = os.path.join(sermons_root, "_Standalone")

    sermon_folder = f"{upload_date} - {sanitize(title)}" if upload_date else sanitize(title)
    target_dir = os.path.join(output_dir, series_dir, sermon_folder)
    target_file = os.path.join(target_dir, "sermon.mp4")

    meta = {
        "video_id": args.video_id,
        "title": title,
        "url": f"https://www.youtube.com/watch?v={args.video_id}",
        "upload_date": upload_date or None,
        "duration_seconds": info.get("duration"),
        "series": series_title,
        "series_playlist_id": series_playlist_id,
        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
    }

    print(f"series          : {series_title or '(standalone)'}")
    print(f"sermon folder   : {sermon_folder}")
    print(f"target directory: {target_dir}")

    if args.dry_run:
        print("[dry-run] no changes made.")
        return

    os.makedirs(target_dir, exist_ok=True)
    shutil.move(source, target_file)
    with open(os.path.join(target_dir, "sermon.meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    print(f"Moved -> {target_file}")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        sys.exit(1)
