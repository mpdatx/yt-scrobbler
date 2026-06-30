from __future__ import annotations
"""Stage 1: Parse Google Takeout watch-history.json into WatchEvent objects."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, urlparse

from models import WatchEvent

log = logging.getLogger(__name__)


def _extract_video_id(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        if parsed.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
            qs = parse_qs(parsed.query)
            ids = qs.get("v")
            return ids[0] if ids else None
        if parsed.hostname in ("youtu.be",):
            return parsed.path.lstrip("/") or None
    except Exception:
        pass
    return None


def _parse_timestamp(ts: str) -> datetime:
    # Takeout uses RFC 3339 / ISO 8601 with Z suffix.
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def parse_takeout(path: Path) -> Iterator[WatchEvent]:
    """Stream-parse watch-history.json, yielding one WatchEvent per watchable entry."""
    skipped_no_url = 0
    skipped_no_id = 0
    yielded = 0

    with path.open("rb") as fh:
        try:
            import ijson  # type: ignore

            entries = ijson.items(fh, "item")
        except ImportError:
            log.debug("ijson not available, falling back to json.load")
            fh.seek(0)
            entries = json.load(fh)

        for entry in entries:
            title_url = entry.get("titleUrl", "")
            if not title_url:
                skipped_no_url += 1
                continue

            video_id = _extract_video_id(title_url)
            if not video_id:
                skipped_no_id += 1
                continue

            raw_title = entry.get("title", "")
            subtitles = entry.get("subtitles") or []
            channel = subtitles[0].get("name", "") if subtitles else ""
            time_str = entry.get("time", "")
            if not time_str:
                skipped_no_url += 1
                continue

            try:
                watched_at = _parse_timestamp(time_str)
            except ValueError:
                skipped_no_url += 1
                continue

            yielded += 1
            yield WatchEvent(
                video_id=video_id,
                raw_title=raw_title,
                channel=channel,
                watched_at=watched_at,
            )

    log.info(
        "parse_takeout: yielded=%d skipped_no_url=%d skipped_no_id=%d",
        yielded,
        skipped_no_url,
        skipped_no_id,
    )


def run(takeout_path: Path) -> list[WatchEvent]:
    events = list(parse_takeout(takeout_path))
    print(f"Parsed {len(events):,} watch events from {takeout_path}")
    return events
