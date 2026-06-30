"""Stage 3: Filter watch events to music-only and emit a filter report."""
import logging
from collections import Counter
from typing import Iterable

from config import CONF_TOPIC, MAX_DURATION_S, MIN_DURATION_S, MUSIC_CATEGORY_ID
from models import VideoMeta, WatchEvent

log = logging.getLogger(__name__)


def is_topic_channel(channel_title: str | None) -> bool:
    return bool(channel_title) and channel_title.endswith(" - Topic")


def is_music(meta: VideoMeta, min_dur: int | None = MIN_DURATION_S, max_dur: int | None = MAX_DURATION_S) -> tuple[bool, str]:
    """Return (keep, reason_string)."""
    if meta.unavailable or meta.category_id is None:
        return False, "unavailable"

    is_topic = is_topic_channel(meta.channel_title)
    cat_ok = meta.category_id == MUSIC_CATEGORY_ID

    if not cat_ok and not is_topic:
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
