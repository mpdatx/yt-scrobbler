from __future__ import annotations
"""Stage 3: Filter watch events to music-only and emit a filter report."""
import logging
from collections import Counter
from typing import Iterable

import re

from config import CONF_TOPIC, MAX_DURATION_S, MIN_DURATION_S, MUSIC_CATEGORY_ID
from models import VideoMeta, WatchEvent

# Takeout prepends a locale-specific "Watched " prefix to every title.
# Strip the most common variants as a best-effort for the --no-api path.
_WATCHED_PREFIX_RE = re.compile(
    r"^(?:Watched|Regardé|Angesehen|Bekeken|Reproduzido|Reproducido|"
    r"Visualizzato|Просмотрено|観た|시청함|已觀看)\s+",
    re.IGNORECASE,
)

log = logging.getLogger(__name__)


def meta_from_takeout(events: Iterable[WatchEvent]) -> dict[str, VideoMeta]:
    """Build minimal VideoMeta stubs from Takeout data alone (no API call).

    category_id is left None so the category filter never fires; only the
    Topic-channel override can mark an entry as music.
    """
    stubs: dict[str, VideoMeta] = {}
    for e in events:
        if e.video_id not in stubs:
            stubs[e.video_id] = VideoMeta(
                video_id=e.video_id,
                category_id=None,
                api_title=_WATCHED_PREFIX_RE.sub("", e.raw_title),
                channel_title=e.channel,
                duration_s=None,
                unavailable=False,
            )
    return stubs


def is_topic_channel(channel_title: str | None) -> bool:
    return bool(channel_title) and channel_title.endswith(" - Topic")


def is_music(meta: VideoMeta, min_dur: int | None = MIN_DURATION_S, max_dur: int | None = MAX_DURATION_S) -> tuple[bool, str]:
    """Return (keep, reason_string)."""
    if meta.unavailable:
        return False, "unavailable"

    is_topic = is_topic_channel(meta.channel_title)
    cat_ok = meta.category_id == MUSIC_CATEGORY_ID

    # Topic-channel is a hard override regardless of whether we have a category ID
    if not cat_ok and not is_topic:
        if meta.category_id is None:
            return False, "no_metadata"
        return False, f"category_{meta.category_id}"

    dur = meta.duration_s
    if dur is not None:
        if min_dur is not None and dur < min_dur:
            return False, "too_short"
        if max_dur is not None and dur > max_dur:
            return False, "too_long"

    return True, "topic" if is_topic else "music_category"


def filter_music(
    events: Iterable[WatchEvent],
    meta_map: dict[str, VideoMeta],
    min_dur: int | None = MIN_DURATION_S,
    max_dur: int | None = MAX_DURATION_S,
) -> tuple[list[tuple[WatchEvent, VideoMeta]], dict]:
    """Return (kept_pairs, report_dict)."""
    kept: list[tuple[WatchEvent, VideoMeta]] = []
    reason_counts: Counter = Counter()
    total = 0

    for event in events:
        total += 1
        meta = meta_map.get(event.video_id)
        if meta is None:
            reason_counts["no_meta"] += 1
            continue

        keep, reason = is_music(meta, min_dur, max_dur)
        if keep:
            kept.append((event, meta))
            reason_counts[f"kept_{reason}"] += 1
        else:
            reason_counts[f"dropped_{reason}"] += 1

    report = {
        "total_events": total,
        "kept": len(kept),
        "dropped": total - len(kept),
        "breakdown": dict(reason_counts),
    }
    return kept, report


def run(
    events: list[WatchEvent],
    meta_map: dict[str, VideoMeta],
    min_dur: int | None = MIN_DURATION_S,
    max_dur: int | None = MAX_DURATION_S,
) -> tuple[list[tuple[WatchEvent, VideoMeta]], dict]:
    kept, report = filter_music(events, meta_map, min_dur, max_dur)
    print(f"\nFilter report:")
    print(f"  Total watch events : {report['total_events']:,}")
    print(f"  Kept (music)       : {report['kept']:,}")
    print(f"  Dropped            : {report['dropped']:,}")
    print("  Breakdown:")
    for k, v in sorted(report["breakdown"].items()):
        print(f"    {k:<30} {v:>8,}")
    return kept, report
