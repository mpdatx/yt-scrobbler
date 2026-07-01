from __future__ import annotations

"""Write audit CSVs after filter and normalize stages."""
import csv
from pathlib import Path

from models import MusicEvent


def _yt_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


_KEPT_FIELDS = [
    "video_id", "url", "artist", "track", "album",
    "confidence", "artist_validation", "artist_correction",
    "channel", "raw_title", "watched_at",
]

_UNVERIFIED_FIELDS = [
    "video_id", "url", "artist", "track", "album",
    "confidence", "artist_validation", "artist_correction",
    "channel", "raw_title", "watched_at",
]

_DROPPED_FIELDS = [
    "video_id", "url", "channel", "title", "reason", "watched_at",
]


def write_audit_kept(events: list[MusicEvent], path: Path) -> None:
    """Write every normalized music event to a CSV for spot-checking."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_KEPT_FIELDS)
        writer.writeheader()
        for e in events:
            writer.writerow({
                "video_id": e.video_id,
                "url": _yt_url(e.video_id),
                "artist": e.artist,
                "track": e.track,
                "album": e.album or "",
                "confidence": e.confidence,
                "artist_validation": e.artist_validation,
                "artist_correction": e.artist_correction or "",
                "channel": e.channel,
                "raw_title": e.raw_title,
                "watched_at": e.watched_at.isoformat(),
            })
    print(f"  Audit (kept)    → {path} ({len(events):,} rows)")


def write_audit_needs_metadata(events: list, path: Path) -> None:
    """Write deduplicated needs-metadata events so you can spot whitelist candidates
    before spending API quota on them."""
    from models import WatchEvent
    from filter_music import _WATCHED_PREFIX_RE

    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    unique_rows = []
    for e in events:
        if e.video_id not in seen:
            seen.add(e.video_id)
            unique_rows.append(e)

    unique_rows.sort(key=lambda e: e.channel)

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["video_id", "url", "channel", "title", "watched_at"])
        writer.writeheader()
        for e in unique_rows:
            writer.writerow({
                "video_id": e.video_id,
                "url": _yt_url(e.video_id),
                "channel": e.channel,
                "title": _WATCHED_PREFIX_RE.sub("", e.raw_title),
                "watched_at": e.watched_at.isoformat(),
            })
    print(f"  Audit (needs metadata) → {path} ({len(unique_rows):,} unique videos, {len(events):,} total events)")


def write_audit_dropped(dropped_rows: list[dict], path: Path) -> None:
    """Write every filtered-out event to a CSV to identify whitelist candidates."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Deduplicate by video_id — same video dropped many times is one entry to review
    seen: set[str] = set()
    unique_rows = []
    for row in dropped_rows:
        if row["video_id"] not in seen:
            seen.add(row["video_id"])
            unique_rows.append(row)

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_DROPPED_FIELDS)
        writer.writeheader()
        for row in unique_rows:
            writer.writerow({
                "video_id": row["video_id"],
                "url": _yt_url(row["video_id"]),
                "channel": row["channel"],
                "title": row["title"],
                "reason": row["reason"],
                "watched_at": row["watched_at"],
            })
    print(f"  Audit (dropped) → {path} ({len(unique_rows):,} unique videos, {len(dropped_rows):,} total events)")


def _write_validation_csv(events: list[MusicEvent], path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_UNVERIFIED_FIELDS)
        writer.writeheader()
        for e in events:
            writer.writerow({
                "video_id": e.video_id,
                "url": _yt_url(e.video_id),
                "artist": e.artist,
                "track": e.track,
                "album": e.album or "",
                "confidence": e.confidence,
                "artist_validation": e.artist_validation,
                "artist_correction": e.artist_correction or "",
                "channel": e.channel,
                "raw_title": e.raw_title,
                "watched_at": e.watched_at.isoformat(),
            })
    print(f"  Audit ({label:<18}) → {path} ({len(events):,} rows)")


def write_audit_unverified(events: list[MusicEvent], path: Path) -> None:
    """Write events whose artist couldn't be confirmed by MB or Last.fm."""
    subset = [e for e in events if e.artist_validation == "unverified"]
    _write_validation_csv(subset, path, "unverified")


def write_audit_corrections(events: list[MusicEvent], path: Path) -> None:
    """Write events where MB or Last.fm suggested a different artist spelling."""
    subset = [e for e in events if e.artist_correction]
    _write_validation_csv(subset, path, "corrections")
