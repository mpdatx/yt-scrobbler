from __future__ import annotations

"""Stage 4: Normalize video title/channel into artist + track pairs."""
import csv
import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from config import CONF_FALLBACK, CONF_PARSED, CONF_TOPIC
from models import Disposition, MusicEvent, ReviewReason, VideoMeta, WatchEvent

if TYPE_CHECKING:
    from rules import Rules

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
_UNICODE_APOS_RE = re.compile(r"[''ʼ]")
_UNICODE_QUOT_RE = re.compile("[“”]")

# Trailing punctuation / whitespace
_TRAIL_RE = re.compile(r"[\s,|]+$")
_LEAD_RE = re.compile(r"^[\s,|]+")

# Disposition-signal patterns (applied during normalize)
_FULL_ALBUM_RE = re.compile(
    r"\bfull\s+(?:album|ep|discography|lp)\b"
    r"|\bcomplete\s+(?:album|discography)\b"
    r"|\bside\s+[ab]\b",
    re.IGNORECASE,
)
_OST_RE = re.compile(
    r"\b(?:ost|original\s+(?:sound\s*track|score)|game\s+(?:music|ost|soundtrack)"
    r"|(?:official\s+)?(?:video\s+)?game\s+music|anime\s+(?:ost|soundtrack)"
    r"|(?:film|movie|tv|television)\s+(?:score|soundtrack))\b",
    re.IGNORECASE,
)
_COMPILATION_RE = re.compile(
    r"\bvarious\s+artists?\b|\bv\.?\s*a\.?\b|\bmulti[\s-]artist\b",
    re.IGNORECASE,
)
# Duration threshold above which we flag as possible full album (20 min)
_FULL_ALBUM_DURATION_S = 20 * 60


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
    if channel.endswith(" - Topic"):
        channel = channel[: -len(" - Topic")]
    return channel.strip()


def normalize_one(
    event: WatchEvent,
    meta: VideoMeta,
    rules: Rules | None = None,
) -> tuple[str, str, str | None, str]:  # artist, track, album, confidence
    """Extract artist, track, album, and confidence from a single music event."""
    title = meta.api_title or event.raw_title
    channel = meta.channel_title or event.channel

    # 1. Video-level override — highest priority
    if rules:
        override = rules.get_video_override(event.video_id)
        if override:
            return (
                override.get("artist", channel),
                override.get("track", title),
                override.get("album"),
                "override",
            )

    # 2. Title pattern rules (strip / extract) — run before everything else
    #    so later stages see a clean title.
    if rules:
        title, artist_from_title, track_from_title = rules.apply_title_rules(title)
        if artist_from_title is not None or track_from_title is not None:
            return (
                _clean_field(artist_from_title or channel),
                _clean_field(track_from_title or title),
                None,
                "title_pattern",
            )

    # 3. Topic channel
    if channel.endswith(" - Topic"):
        artist = _strip_topic_suffix(channel)
        track = _clean_field(title)
        return artist, track, None, CONF_TOPIC

    # 4. Channel artist map — apply before title split so the mapped name is used
    mapped_artist: str | None = None
    if rules:
        mapped_artist = rules.get_channel_artist(channel)

    # 5. "Artist - Track" split
    parts = _DASH_SPLIT_RE.split(title, maxsplit=1)
    if len(parts) == 2:
        raw_artist = _clean_field(parts[0])
        track = _clean_field(parts[1])
        artist = mapped_artist or raw_artist
        if artist and track and len(artist) < 120 and len(track) < 200:
            return artist, track, None, CONF_PARSED

    # 6. Fallback: channel (or mapped name) = artist, full cleaned title = track
    artist = mapped_artist or _clean_field(channel) or channel
    track = _clean_field(title)
    return artist, track, None, CONF_FALLBACK


def _detect_disposition(
    title: str,
    channel: str,
    confidence: str,
    duration_s: int | None,
    rules: Rules | None = None,
) -> tuple[str, str | None]:
    """Return (disposition, review_reason).

    Rules-based signals (from rules.yaml) take priority over hardcoded patterns.
    """
    # 1. Rules-based channel signal
    if rules:
        reason = rules.get_channel_review_reason(channel)
        if reason:
            return Disposition.NEEDS_REVIEW, reason

    # 2. Rules-based title signal
    if rules:
        reason = rules.get_title_review_reason(title)
        if reason:
            return Disposition.NEEDS_REVIEW, reason

    # 3. Hardcoded title patterns (defaults when no rules configured)
    full_album_threshold = (
        rules.full_album_duration_s if rules else _FULL_ALBUM_DURATION_S
    )
    if _COMPILATION_RE.search(title):
        return Disposition.NEEDS_REVIEW, ReviewReason.COMPILATION
    if _FULL_ALBUM_RE.search(title) or (
        duration_s is not None and duration_s > full_album_threshold
    ):
        return Disposition.NEEDS_REVIEW, ReviewReason.FULL_ALBUM
    if _OST_RE.search(title):
        return Disposition.NEEDS_REVIEW, ReviewReason.OST

    # 4. Parse quality signal
    if confidence == CONF_FALLBACK:
        return Disposition.NEEDS_REVIEW, ReviewReason.POOR_TITLE

    return Disposition.READY, None


def normalize(
    pairs: Iterable[tuple[WatchEvent, VideoMeta]],
    rules: Rules | None = None,
) -> list[MusicEvent]:
    results: list[MusicEvent] = []

    for event, meta in pairs:
        artist, track, album, confidence = normalize_one(event, meta, rules)
        raw_title = meta.api_title or event.raw_title
        channel = meta.channel_title or event.channel
        disposition, review_reason = _detect_disposition(
            raw_title, channel, confidence, meta.duration_s, rules
        )
        music_event = MusicEvent(
            video_id=event.video_id,
            artist=artist,
            track=track,
            album=album,
            watched_at=event.watched_at,
            confidence=confidence,
            raw_title=raw_title,
            channel=meta.channel_title or event.channel,
            disposition=disposition,
            review_reason=review_reason,
        )
        results.append(music_event)

    return results


def run(
    pairs: list[tuple[WatchEvent, VideoMeta]],
    rules: Rules | None = None,
) -> list[MusicEvent]:
    results = normalize(pairs, rules=rules)
    conf_counts: dict[str, int] = {}
    for e in results:
        conf_counts[e.confidence] = conf_counts.get(e.confidence, 0) + 1
    print(f"\nNormalize: {len(results):,} music events")
    for conf, count in sorted(conf_counts.items()):
        print(f"  {conf:<10} {count:>8,}")
    return results
