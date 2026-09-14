#!/usr/bin/env python3
"""
confirm_download.py <video_id>

Run this after you've checked notifications.log (or your Slack/text)
and decided a flagged video is the right one to download.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from church_video_watcher import download_video, organize_download, load_json, save_json, CONFIG_PATH, STATE_PATH


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 confirm_download.py <video_id>")
        sys.exit(1)

    video_id = sys.argv[1]
    config = load_json(CONFIG_PATH, None)
    state = load_json(STATE_PATH, {"processed_ids": [], "pending": []})

    if config is None:
        print("Missing config.json. This watcher hasn't been set up for a "
              "church yet - run: python3 setup.py")
        sys.exit(1)

    if video_id not in state.get("pending", []):
        print("That video ID isn't in the pending list (check notifications.log).")
        sys.exit(1)

    path = download_video(video_id, config["output_dir"])
    state["pending"].remove(video_id)
    save_json(STATE_PATH, state)
    print("Downloaded and marked complete.")
    if path:
        organize_download(video_id, path)


if __name__ == "__main__":
    main()
