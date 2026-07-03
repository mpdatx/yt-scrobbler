from __future__ import annotations

"""Write audit CSVs after filter, normalize, and validate stages."""
import csv
from collections import Counter
from pathlib import Path

from models import MusicEvent, WatchEvent


def _yt_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


_MUSIC_EVENT_FIELDS = [
    "video_id", "url", "artist", "track", "album",
    "confidence", "disposition", "review_reason",
    "artist_validation", "artist_correction",
    "channel", "raw_title", "watched_at",
]

_NOT_MUSIC_FIELDS = [
    "video_id", "url", "channel", "title", "reason", "watched_at",
]

_UNDECIDED_FIELDS = [
    "video_id", "url", "channel", "title", "watched_at",
]


def _write_music_event_csv(events: list[MusicEvent], path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_MUSIC_EVENT_FIELDS)
        writer.writeheader()
        for e in events:
            writer.writerow({
                "video_id": e.video_id,
                "url": _yt_url(e.video_id),
                "artist": e.artist,
                "track": e.track,
                "album": e.album or "",
                "confidence": e.confidence,
                "disposition": e.disposition,
                "review_reason": e.review_reason or "",
                "artist_validation": e.artist_validation,
                "artist_correction": e.artist_correction or "",
                "channel": e.channel,
                "raw_title": e.raw_title,
                "watched_at": e.watched_at.isoformat(),
            })
    print(f"  {label:<35} → {path} ({len(events):,} rows)")


# ---------------------------------------------------------------------------
# Per-bucket writers
# ---------------------------------------------------------------------------

def write_audit_ready(events: list[MusicEvent], path: Path) -> None:
    """All scrobble-ready events — the full picture of what will be scrobbled."""
    from models import Disposition
    subset = [e for e in events if e.disposition == Disposition.READY]
    _write_music_event_csv(subset, path, "Audit (ready)")


def write_audit_needs_review(events: list[MusicEvent], out_dir: Path) -> None:
    """One CSV per review_reason so each queue is actionable independently."""
    from models import Disposition
    subset = [e for e in events if e.disposition == Disposition.NEEDS_REVIEW]
    if not subset:
        print(f"  Audit (needs_review)                → (none)")
        return
    by_reason: dict[str, list[MusicEvent]] = {}
    for e in subset:
        key = e.review_reason or "unknown"
        by_reason.setdefault(key, []).append(e)
    out_dir.mkdir(parents=True, exist_ok=True)
    for reason, rows in sorted(by_reason.items()):
        path = out_dir / f"needs_review_{reason}.csv"
        _write_music_event_csv(rows, path, f"Audit (needs_review/{reason})")


def write_audit_not_music(not_music_rows: list[dict], path: Path) -> None:
    """Events confirmed as not music — deduplicated by video_id."""
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    unique: list[dict] = []
    for row in not_music_rows:
        if row["video_id"] not in seen:
            seen.add(row["video_id"])
            unique.append(row)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_NOT_MUSIC_FIELDS)
        writer.writeheader()
        for row in unique:
            writer.writerow({
                "video_id": row["video_id"],
                "url": _yt_url(row["video_id"]),
                "channel": row["channel"],
                "title": row["title"],
                "reason": row["reason"],
                "watched_at": row["watched_at"],
            })
    print(f"  {'Audit (not_music)':<35} → {path} "
          f"({len(unique):,} unique videos, {len(not_music_rows):,} total events)")


def write_audit_undecided(undecided_events: list[WatchEvent], path: Path) -> None:
    """Events with no metadata — deduplicated, sorted by channel."""
    from filter_music import _WATCHED_PREFIX_RE
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    unique: list[WatchEvent] = []
    for e in undecided_events:
        if e.video_id not in seen:
            seen.add(e.video_id)
            unique.append(e)
    unique.sort(key=lambda e: e.channel)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_UNDECIDED_FIELDS)
        writer.writeheader()
        for e in unique:
            writer.writerow({
                "video_id": e.video_id,
                "url": _yt_url(e.video_id),
                "channel": e.channel,
                "title": _WATCHED_PREFIX_RE.sub("", e.raw_title),
                "watched_at": e.watched_at.isoformat(),
            })
    print(f"  {'Audit (undecided)':<35} → {path} "
          f"({len(unique):,} unique videos, {len(undecided_events):,} total events)")


def write_audit_needs_metadata(events: list[WatchEvent], path: Path) -> None:
    """Pre-filter view: videos that will need API metadata (before fetch stage)."""
    from filter_music import _WATCHED_PREFIX_RE
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    unique: list[WatchEvent] = []
    for e in events:
        if e.video_id not in seen:
            seen.add(e.video_id)
            unique.append(e)
    unique.sort(key=lambda e: e.channel)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["video_id", "url", "channel", "title", "watched_at"])
        writer.writeheader()
        for e in unique:
            writer.writerow({
                "video_id": e.video_id,
                "url": _yt_url(e.video_id),
                "channel": e.channel,
                "title": _WATCHED_PREFIX_RE.sub("", e.raw_title),
                "watched_at": e.watched_at.isoformat(),
            })
    print(f"  {'Audit (needs_metadata)':<35} → {path} "
          f"({len(unique):,} unique videos, {len(events):,} total events)")


def write_audit_corrections(events: list[MusicEvent], path: Path) -> None:
    """Events where MB or Last.fm suggested a different artist spelling."""
    subset = [e for e in events if e.artist_correction]
    _write_music_event_csv(subset, path, "Audit (corrections)")


# ---------------------------------------------------------------------------
# Backward-compat aliases
# ---------------------------------------------------------------------------

def write_audit_kept(events: list[MusicEvent], path: Path) -> None:
    """Alias for write_audit_ready — all music events regardless of disposition."""
    _write_music_event_csv(events, path, "Audit (all music events)")


def write_audit_dropped(not_music_rows: list[dict], path: Path) -> None:
    """Alias for write_audit_not_music."""
    write_audit_not_music(not_music_rows, path)


def write_audit_unverified(events: list[MusicEvent], path: Path) -> None:
    subset = [e for e in events if e.artist_validation == "unverified"]
    _write_music_event_csv(subset, path, "Audit (unverified)")
