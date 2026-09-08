#!/usr/bin/env python3
"""Interactive setup for the YouTube Service Video Watcher.

Walks a user through creating config.json for their own church.

    python3 setup.py                    # guided, prompts for each value
    python3 setup.py --yes \
        --channel "https://www.youtube.com/WOLCTV" \
        --output-dir "~/Videos/WOLC Service Videos"

Agent / power-user mode: pass --channel (and optionally --output-dir,
--min-duration, --title-keywords, --auto-download, --yes) to configure
non-interactively. Missing optional values fall back to sensible defaults.

Advanced users can also edit config.json directly (see config.example.json
for the full field reference).
"""

import argparse
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

YOUTUBE_HOST_RE = re.compile(r"^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)", re.IGNORECASE)
NOT_A_CHANNEL = ("/watch", "/shorts/", "playlist", "/live/", "/embed/")


def normalize_channel_url(url):
    """Return the channel's /videos URL, or None if not a channel URL.

    People usually paste the whole channel homepage
    (https://www.youtube.com/WOLCTV or @WOLCTV) instead of the /videos
    tab. Normalize those to the uploads tab so the watcher only sees real
    uploads, not Shorts.
    """
    if not url:
        return None
    url = url.strip()
    if url.startswith("@"):  # bare handle like @YourChurch
        url = "https://www.youtube.com/" + url
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    base = url.split("?")[0].rstrip("/")
    low = base.lower()

    # Something we can't treat as a channel → reject. (Note: youtu.be
    # links are always single videos, never channels.)
    if any(tok in low for tok in NOT_A_CHANNEL):
        return None
    if "youtu.be" in low:
        return None
    if not YOUTUBE_HOST_RE.match(base):
        return None

    if base.endswith("/videos"):
        return base

    # Channel page patterns we can safely append /videos to:
    #   youtube.com/@handle, youtube.com/channel/UC..., youtube.com/c/name,
    #   and a bare custom name like youtube.com/YourChurch.
    if ("youtube.com/@" in low
            or "/channel/" in low
            or "/c/" in low
            or low.count("/") == 3):
        return base + "/videos"

    return None


def prompt(prompt_text, default=None):
    if default is not None:
        prompt_text = f"{prompt_text} [{default}]: "
    else:
        prompt_text = f"{prompt_text}: "
    value = input(prompt_text).strip()
    return value if value else default


def prompt_yes_no(prompt_text, default=True):
    label = "Y/n" if default else "y/N"
    value = input(f"{prompt_text} [{label}]: ").strip().lower()
    if value in ("y", "yes"):
        return True
    if value in ("n", "no"):
        return False
    return default


def ask_channel_interactive(existing):
    print("\nWhich YouTube channel should be watched?")
    print("  Paste the channel link, e.g. https://www.youtube.com/WOLCTV")
    print("  (a homepage link like that is fine - we'll point it at /videos)")
    default_channel = (existing or {}).get("channel_url", "")
    while True:
        raw = prompt("Channel URL", default=default_channel or None)
        normalized = normalize_channel_url(raw)
        if normalized is None:
            print("  That doesn't look like a YouTube channel URL. Try again.")
            continue
        if normalized != raw.strip().rstrip("/"):
            print(f"  Using {normalized}")
        return normalized


def build_config(args, existing):
    min_minutes = args.min_duration
    keywords = args.title_keywords
    if keywords:
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    return {
        "channel_url": args.channel,
        "title_keywords": keywords or [],
        "min_duration_minutes": min_minutes,
        "auto_download": args.auto_download,
        "match_strategy": "newest",
        "match_window_days": 0,
        "output_dir": args.output_dir,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", help="Church YouTube channel URL (normalized to /videos).")
    parser.add_argument("--output-dir", help="Folder to save videos (default: ~/Videos/MyChurch-service-videos).")
    parser.add_argument("--min-duration", type=int, default=10, help="Minimum minutes to count as a service (default 10).")
    parser.add_argument("--title-keywords", default="", help="Comma-separated title keywords (optional).")
    parser.add_argument("--auto-download", action="store_true", help="Download automatically without asking.")
    parser.add_argument("--yes", action="store_true", help="Overwrite existing config.json without prompting.")
    args = parser.parse_args()

    existing = None
    if os.path.exists(CONFIG_PATH):
        try:
            existing = json.load(open(CONFIG_PATH))
        except Exception:
            existing = None

    # ---- Interactive mode (no --channel) --------------------------------
    if not args.channel:
        print("=" * 60)
        print("YouTube Service Video Watcher - setup")
        print("=" * 60)
        if existing is not None and not prompt_yes_no(
            "config.json already exists. Overwrite it with new values?", default=False
        ):
            print("Keeping existing config.json. Nothing changed.")
            return

        channel = ask_channel_interactive(existing)
        default_dir = (existing or {}).get("output_dir", "~/Videos/MyChurch-service-videos")
        out_dir = prompt("Where should videos be saved?", default=default_dir)

        default_min = (existing or {}).get("min_duration_minutes", 10)
        min_minutes = prompt(
            "Ignore uploads shorter than this many minutes? (filters Shorts)",
            default=default_min,
        )
        try:
            min_minutes = int(min_minutes)
        except ValueError:
            min_minutes = 10
        min_minutes = max(1, min_minutes)

        default_kw = (existing or {}).get("title_keywords", [])
        kw_prompt = "Title keywords? Comma-separated, blank = none"
        if default_kw:
            kw_prompt += f" [current: {', '.join(default_kw)}]"
        kw_raw = input(kw_prompt + ": ").strip()
        keywords = []
        if kw_raw:
            keywords = [k.strip() for k in kw_raw.split(",") if k.strip()]
        elif default_kw:
            keywords = default_kw

        auto_download = prompt_yes_no(
            "Download videos automatically without asking?", default=False
        )

        args = argparse.Namespace(
            channel=channel,
            output_dir=out_dir or "~/Videos/MyChurch-service-videos",
            min_duration=min_minutes,
            title_keywords=",".join(keywords) if keywords else "",
            auto_download=auto_download,
        )

    # ---- Non-interactive (agent / power-user) ---------------------------
    else:
        channel = normalize_channel_url(args.channel)
        if channel is None:
            print("That URL doesn't look like a YouTube channel (homepage, "
                  "@handle, or channel link required).")
            sys.exit(1)
        if channel != args.channel.strip().rstrip("/"):
            print(f"Channel URL normalized to: {channel}")
        args.channel = channel
        if not args.output_dir:
            args.output_dir = (existing or {}).get(
                "output_dir", "~/Videos/MyChurch-service-videos"
            )
        if existing is not None and not args.yes:
            print("config.json already exists. Pass --yes to overwrite it.")
            return
        # Interactive title-keywords handling only happens in interactive mode;
        # --title-keywords fills the value here.

    config = build_config(args, existing)

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    print("\nSaved config.json:")
    print("  " + json.dumps(config, indent=2).replace("\n", "\n  "))
    print("\n" + "=" * 60)
    print("Setup complete. To start watching, run:")
    print("    python3 church_video_watcher.py")
    print("Check notifications.log for what it found.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled. No changes were finalized.")
        sys.exit(1)
