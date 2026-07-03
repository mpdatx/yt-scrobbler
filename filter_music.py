from __future__ import annotations

"""Stage 3: Filter watch events to music-only and emit a filter report."""
import logging
import re
from collections import Counter
from typing import TYPE_CHECKING, Iterable

from config import MAX_DURATION_S, MIN_DURATION_S, MUSIC_CATEGORY_ID
from models import VideoMeta, WatchEvent

if TYPE_CHECKING:
    from rules import Rules

# Takeout prepends a locale-specific "Watched " prefix to every title.
# Strip the most common variants as a best-effort for the --no-api path.
_WATCHED_PREFIX_RE = re.compile(
    r"^(?:Watched|Regardé|Angesehen|Bekeken|Reproduzido|Reproducido|"
    r"Visualizzato|Просмотрено|観た|시청함|已觀看)\s+",
    re.IGNORECASE,
)

log = logging.getLogger(__name__)


def _stub(event: WatchEvent) -> VideoMeta:
    """Minimal VideoMeta built from Takeout data alone."""
    return VideoMeta(
        video_id=event.video_id,
        category_id=None,
        api_title=_WATCHED_PREFIX_RE.sub("", event.raw_title),
        channel_title=event.channel,
        duration_s=None,
        unavailable=False,
    )


def pre_classify(
    events: list[WatchEvent],
    rules: Rules | None = None,
) -> tuple[list[WatchEvent], list[WatchEvent], list[dict]]:
    """Split events into (definite_music, needs_metadata, dropped_rows) using
    only Takeout data — no API call required.

    definite_music:  Topic channels + whitelisted channels/videos.
    needs_metadata:  Everything else; must go through the YouTube API.
    dropped_rows:    Blacklisted or title-skipped events (audit_dropped format).

    Deduplication of video IDs happens in fetch_metadata; pre_classify preserves
    all watch events so replay timestamps are not lost.
    """
    definite_music: list[WatchEvent] = []
    needs_metadata: list[WatchEvent] = []
    dropped_rows: list[dict] = []

    for event in events:
        channel = event.channel
        title = _WATCHED_PREFIX_RE.sub("", event.raw_title)

        if rules and rules.is_blacklisted(event.video_id, channel):
            dropped_rows.append(_drop_row(event, title, "blacklisted"))
            continue

        if rules and rules.title_should_skip(title):
            dropped_rows.append(_drop_row(event, title, "title_skip"))
            continue

        if is_topic_channel(channel) or (rules and rules.is_whitelisted(event.video_id, channel)):
            definite_music.append(event)
            continue

        needs_metadata.append(event)

    return definite_music, needs_metadata, dropped_rows


def meta_from_takeout(events: Iterable[WatchEvent]) -> dict[str, VideoMeta]:
    """Build minimal VideoMeta stubs from Takeout data alone (no API call).

    category_id is left None so the category filter never fires; only the
    Topic-channel override and whitelist rules can mark an entry as music.
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


def is_music(
    meta: VideoMeta,
    min_dur: int | None = MIN_DURATION_S,
    max_dur: int | None = MAX_DURATION_S,
) -> tuple[bool, str]:
    """Return (keep, reason_string). Does not consult rules — caller does that."""
    if meta.unavailable:
        return False, "unavailable"

    is_topic = is_topic_channel(meta.channel_title)
    cat_ok = meta.category_id == MUSIC_CATEGORY_ID

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
    needs_metadata_events: Iterable[WatchEvent],
    meta_map: dict[str, VideoMeta],
    definite_music_events: list[WatchEvent] | None = None,
    pre_dropped_rows: list[dict] | None = None,
    min_dur: int | None = MIN_DURATION_S,
    max_dur: int | None = MAX_DURATION_S,
) -> tuple[list[tuple[WatchEvent, VideoMeta]], dict, list[dict], list[WatchEvent]]:
    """Filter the needs_metadata bucket against the API-fetched meta_map.

    Returns (confirmed_music, report, not_music_rows, undecided_events).

    confirmed_music:  events with clear music signal → passed to normalize
    not_music_rows:   events with clear non-music signal → audit_not_music
    undecided_events: no metadata available (deleted/private/--no-api)
                      → audit_undecided; may resolve on a future run with API
    """
    confirmed_music: list[tuple[WatchEvent, VideoMeta]] = []
    not_music_rows: list[dict] = list(pre_dropped_rows or [])
    undecided_events: list[WatchEvent] = []
    reason_counts: Counter = Counter()

    # Seed counts from pre-classify drops
    for row in (pre_dropped_rows or []):
        reason_counts[f"not_music_{row['reason']}"] += 1

    # Definite music — use API metadata if available, fall back to stub
    for event in (definite_music_events or []):
        meta = meta_map.get(event.video_id) or _stub(event)
        confirmed_music.append((event, meta))
        reason = "topic" if is_topic_channel(event.channel) else "whitelisted"
        reason_counts[f"music_{reason}"] += 1

    # Ambiguous bucket — apply category/duration filter against fetched metadata
    for event in needs_metadata_events:
        meta = meta_map.get(event.video_id)
        api_title = (meta.api_title if meta else None) or event.raw_title

        if meta is None:
            # No metadata → undecided, not dropped
            reason_counts["undecided_no_meta"] += 1
            undecided_events.append(event)
            continue

        keep, reason = is_music(meta, min_dur, max_dur)
        if keep:
            confirmed_music.append((event, meta))
            reason_counts[f"music_{reason}"] += 1
        else:
            reason_counts[f"not_music_{reason}"] += 1
            not_music_rows.append(_drop_row(event, api_title, reason))

    total = sum(reason_counts.values())
    report = {
        "total_events": total,
        "confirmed_music": len(confirmed_music),
        "not_music": len(not_music_rows),
        "undecided": len(undecided_events),
        "breakdown": dict(reason_counts),
    }
    return confirmed_music, report, not_music_rows, undecided_events


def _drop_row(event: WatchEvent, title: str, reason: str) -> dict:
    return {
        "video_id": event.video_id,
        "channel": event.channel,
        "title": title,
        "reason": reason,
        "watched_at": event.watched_at.isoformat(),
    }


def run(
    needs_metadata_events: list[WatchEvent],
    meta_map: dict[str, VideoMeta],
    definite_music_events: list[WatchEvent] | None = None,
    pre_dropped_rows: list[dict] | None = None,
    min_dur: int | None = MIN_DURATION_S,
    max_dur: int | None = MAX_DURATION_S,
) -> tuple[list[tuple[WatchEvent, VideoMeta]], dict, list[dict], list[WatchEvent]]:
    confirmed_music, report, not_music_rows, undecided_events = filter_music(
        needs_metadata_events, meta_map,
        definite_music_events, pre_dropped_rows,
        min_dur, max_dur,
    )
    print(f"\nFilter report:")
    print(f"  Total watch events : {report['total_events']:,}")
    print(f"  Confirmed music    : {report['confirmed_music']:,}")
    print(f"  Not music          : {report['not_music']:,}")
    print(f"  Undecided          : {report['undecided']:,}")
    print("  Breakdown:")
    for k, v in sorted(report["breakdown"].items()):
        print(f"    {k:<30} {v:>8,}")
    return confirmed_music, report, not_music_rows, undecided_events
