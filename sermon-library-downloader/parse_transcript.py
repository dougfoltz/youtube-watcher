#!/usr/bin/env python3
"""Turn a YouTube json3 caption file into transcript / words / sentences.

    python3 parse_transcript.py --json3 IN.en.json3 \
        --transcript OUT.transcript.txt \
        --words OUT.words.json \
        --sentences OUT.sentences.json

`transcript.txt` is one numbered sentence per line:

    [012 @ 03:45] and that is what grace looks like

The sentence index is the shared handle every downstream consumer uses
(wiki entries, clip boundaries, quote lookups), so for a given captions
file it must stay stable. Re-running this script on the same json3 always
produces the same indices.

Why sentences are inferred: YouTube's *automatic* captions carry no
punctuation, so sentences are recovered from speech pauses and length,
not from periods. Churches that upload their own caption file do get real
punctuation, and that is used when present.
"""

import argparse
import json
import os
import re
import sys

# A gap this long between words reads as a sentence break.
PAUSE_MS = 700
# Never let an inferred sentence run longer than this.
MAX_WORDS = 32
# ...but don't break on a pause until the sentence has at least this many
# words, or a hesitation mid-phrase would shatter it into fragments.
MIN_WORDS = 4

TERMINAL_PUNCT = re.compile(r"[.!?]['\")\]]*$")


def load_words(json3_path):
    """Extract word-level timings from a json3 caption file.

    json3 events hold `segs`, each seg one word with `tOffsetMs` relative
    to the event's `tStartMs`. Events flagged `aAppend` are YouTube's
    rolling-caption repeats of text already emitted, so they are skipped.
    """
    with open(json3_path) as f:
        data = json.load(f)

    words = []
    for event in data.get("events") or []:
        if event.get("aAppend"):
            continue
        segs = event.get("segs")
        if not segs:
            continue
        base = event.get("tStartMs") or 0
        event_end = base + (event.get("dDurationMs") or 0)
        for seg in segs:
            text = (seg.get("utf8") or "").strip()
            if not text:
                continue  # newline / spacer segments
            words.append({
                "word": text,
                "start_ms": base + (seg.get("tOffsetMs") or 0),
                "_event_end": event_end,
            })

    words.sort(key=lambda w: w["start_ms"])

    # Drop exact repeats (same word at the same timestamp) that some
    # auto-caption files emit.
    deduped = []
    for w in words:
        if deduped and w["word"] == deduped[-1]["word"] and w["start_ms"] == deduped[-1]["start_ms"]:
            continue
        deduped.append(w)

    # End of a word = start of the next, bounded by its own event's end.
    for i, w in enumerate(deduped):
        nxt = deduped[i + 1]["start_ms"] if i + 1 < len(deduped) else None
        end = w.pop("_event_end") or w["start_ms"]
        if nxt is not None:
            end = min(end, nxt) if end > w["start_ms"] else nxt
        w["end_ms"] = max(end, w["start_ms"])
        w["i"] = i
    return deduped


def group_sentences(words):
    """Group words into sentences using punctuation, then pauses, then length."""
    sentences = []
    current = []

    def flush():
        if not current:
            return
        sentences.append({
            "i": len(sentences),
            "start_ms": current[0]["start_ms"],
            "end_ms": current[-1]["end_ms"],
            "word_start": current[0]["i"],
            "word_end": current[-1]["i"],
            "text": " ".join(w["word"] for w in current),
        })
        current.clear()

    for idx, word in enumerate(words):
        if current:
            gap = word["start_ms"] - current[-1]["end_ms"]
            if gap >= PAUSE_MS and len(current) >= MIN_WORDS:
                flush()
        current.append(word)
        if TERMINAL_PUNCT.search(word["word"]) and len(current) >= 2:
            flush()
        elif len(current) >= MAX_WORDS:
            flush()

    flush()
    return sentences


def format_timestamp(ms):
    total = int(ms // 1000)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def write_outputs(words, sentences, transcript_path, words_path, sentences_path):
    for sentence in sentences:
        sentence["start"] = format_timestamp(sentence["start_ms"])

    if transcript_path:
        os.makedirs(os.path.dirname(os.path.abspath(transcript_path)), exist_ok=True)
        with open(transcript_path, "w") as f:
            for s in sentences:
                f.write(f"[{s['i']:03d} @ {s['start']}] {s['text']}\n")

    if words_path:
        with open(words_path, "w") as f:
            json.dump(words, f, indent=2)
            f.write("\n")

    if sentences_path:
        with open(sentences_path, "w") as f:
            json.dump(sentences, f, indent=2)
            f.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json3", required=True, help="Input .json3 caption file")
    parser.add_argument("--transcript", help="Output numbered-sentence text file")
    parser.add_argument("--words", help="Output word-timing JSON")
    parser.add_argument("--sentences", help="Output sentence JSON")
    args = parser.parse_args()

    if not os.path.exists(args.json3):
        print(f"Caption file not found: {args.json3}")
        sys.exit(1)

    words = load_words(args.json3)
    if not words:
        print(f"No caption text found in {args.json3}")
        sys.exit(2)

    sentences = group_sentences(words)
    write_outputs(words, sentences, args.transcript, args.words, args.sentences)
    print(f"{len(words)} words -> {len(sentences)} sentences "
          f"({format_timestamp(sentences[-1]['end_ms'])} of speech)")


if __name__ == "__main__":
    main()
