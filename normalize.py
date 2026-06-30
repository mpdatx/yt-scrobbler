"""Stage 4: Normalize video title/channel into artist + track pairs."""
import csv
import re
import unicodedata
from pathlib import Path
from typing import Iterable

from config import CONF_FALLBACK, CONF_PARSED, CONF_TOPIC
from models import MusicEvent, VideoMeta, WatchEvent

# ---------------------------------------------------------------------------
# Cleaning patterns
# ---------------------------------------------------------------------------

# Junk suffixes/parentheticals to strip (case-insensitive, after extraction)
_STRIP_PATTERNS = [
    r"\(official\s+(?:music\s+)?video\)",
    r"\[official\s+(?:music\s+)?video\]",
    r"\(official\s+audio\)",
    r"\[official\s+audio\]",
    r"\(official\s+lyric\s+video\)",
    r"\[official\s+lyric\s+video\]",
    r"\(lyri[ck]s?\s*video\)",
    r"\[lyri[ck]s?\s*video\]",
    r"\(lyri[ck]s?\)",
    r"\[lyri[ck]s?\]",
    r"\(visuali[sz]er\)",
    r"\[visuali[sz]er\]",
    r"\((?:full\s+)?(?:official\s+)?(?:hd|4k|1080p|720p)\)",
    r"\[(?:full\s+)?(?:official\s+)?(?:hd|4k|1080p|720p)\]",
    r"\bofficial\s+(?:music\s+)?video\b",
    r"\bofficial\s+audio\b",
    r"\bofficial\s+lyric\s+video\b",
    r"\(official\)",
    r"\[official\]",
    r"\blyric\s+video\b",
    r"\blyrics\b",
    r"\bvisualizer\b",
    r"\bprovided\s+to\s+youtube.*$",
    r"\bauto-generated\s+by\s+youtube.*$",
    r"#\w+",  # hashtags at end of title
]
_STRIP_RE = re.compile(
    "|".join(_STRIP_PATTERNS), re.IGNORECASE | re.UNICODE
)

# Dashes that indicate artist–track splits (em-dash, en-dash, hyphen-minus with spaces)
_DASH_SPLIT_RE = re.compile(r"\s+[-–—]\s+")

# Normalize unicode dashes/quotes
_UNICODE_DASH_RE = re.compile(r"[–—]")
_UNICODE_APOS_RE = re.compile(r"[‘’ʼ]")
_UNICODE_QUOT_RE = re.compile(r"[“”]")

# Prefix junk like "Official" at start of a string
_LEADING_JUNK_RE = re.compile(r"^(?:official\s+)?", re.IGNORECASE)

# Trailing punctuation / whitespace
_TRAIL_RE = re.compile(r"[\s,|]+$")
_LEAD_RE = re.compile(r"^[\s,|]+")


def _unicode_normalize(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = _UNICODE_DASH_RE.sub("-", s)
    s = _UNICODE_APOS_RE.sub("'", s)
    s = _UNICODE_QUOT_RE.sub('"', s)
    return s


def _clean_field(s: str) -> str:
    """Strip junk markers, normalize whitespace."""
    s = _unicode_normalize(s)
    s = _STRIP_RE.sub(" ", s)
    s = re.sub(r"\s{2,}", " ", s)
    s = _TRAIL_RE.sub("", s)
    s = _LEAD_RE.sub("", s)
    return s.strip()


def _strip_topic_suffix(channel: str) -> str:
    return channel.removesuffix(" - Topic").strip()


def normalize_one(
    event: WatchEvent, meta: VideoMeta
) -> tuple[str, str, str]:  # artist, track, confidence
    """Extract artist and track from a single music event."""
    title = meta.api_title or event.raw_title
    channel = meta.channel_title or event.channel

    # 1. Topic channel — highest confidence
    if channel.endswith(" - Topic"):
        artist = _strip_topic_suffix(channel)
        track = _clean_field(title)
        return artist, track, CONF_TOPIC

    # 2. "Artist - Track" split
    parts = _DASH_SPLIT_RE.split(title, maxsplit=1)
    if len(parts) == 2:
        artist = _clean_field(parts[0])
        track = _clean_field(parts[1])
        # Sanity: both sides non-empty and not extremely long
        if artist and track and len(artist) < 120 and len(track) < 200:
            return artist, track, CONF_PARSED

    # 3. Fallback: channel = artist, full cleaned title = track
    artist = _clean_field(channel) or channel
    track = _clean_field(title)
    return artist, track, CONF_FALLBACK


def normalize(
    pairs: Iterable[tuple[WatchEvent, VideoMeta]],
    review_path: Path | None = None,
) -> list[MusicEvent]:
    results: list[MusicEvent] = []
    low_conf: list[dict] = []

    for event, meta in pairs:
        artist, track, confidence = normalize_one(event, meta)
        music_event = MusicEvent(
            video_id=event.video_id,
            artist=artist,
            track=track,
            album=None,
            watched_at=event.watched_at,
            confidence=confidence,
            raw_title=meta.api_title or event.raw_title,
            channel=meta.channel_title or event.channel,
        )
        results.append(music_event)
        if confidence == CONF_FALLBACK:
            low_conf.append(
                {
                    "video_id": event.video_id,
                    "raw_title": meta.api_title or event.raw_title,
                    "channel": meta.channel_title or event.channel,
                    "artist": artist,
                    "track": track,
                    "confidence": confidence,
                    "watched_at": event.watched_at.isoformat(),
                }
            )

    if review_path and low_conf:
        review_path.parent.mkdir(parents=True, exist_ok=True)
        with review_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["video_id", "raw_title", "channel", "artist", "track", "confidence", "watched_at"],
            )
            writer.writeheader()
            writer.writerows(low_conf)
        print(f"  Low-confidence entries written to {review_path} ({len(low_conf):,} rows)")

    return results


def run(
    pairs: list[tuple[WatchEvent, VideoMeta]],
    review_path: Path | None = None,
) -> list[MusicEvent]:
    results = normalize(pairs, review_path=review_path)
    conf_counts = {}
    for e in results:
        conf_counts[e.confidence] = conf_counts.get(e.confidence, 0) + 1
    print(f"\nNormalize: {len(results):,} music events")
    for conf, count in sorted(conf_counts.items()):
        print(f"  {conf:<10} {count:>8,}")
    return results
