from __future__ import annotations
"""Stage 2: Fetch YouTube Data API metadata with SQLite cache."""
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Iterable

import requests

from config import CACHE_DB, YOUTUBE_API_KEY
from models import VideoMeta, WatchEvent

log = logging.getLogger(__name__)

VIDEOS_ENDPOINT = "https://www.googleapis.com/youtube/v3/videos"
BATCH_SIZE = 50
_ISO_RE = None  # lazy


def _init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS video_meta (
            video_id      TEXT PRIMARY KEY,
            category_id   TEXT,
            api_title     TEXT,
            channel_title TEXT,
            duration_s    INTEGER,
            tags_json     TEXT DEFAULT '[]',
            unavailable   INTEGER DEFAULT 0,
            fetched_at    TEXT
        )
        """
    )
    conn.commit()
    return conn


def _duration_to_seconds(iso: str) -> int | None:
    """Convert ISO 8601 duration (PT4M33S) to total seconds."""
    import re

    m = re.fullmatch(
        r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or ""
    )
    if not m:
        return None
    days, hours, minutes, seconds = (int(x or 0) for x in m.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _row_to_meta(row: sqlite3.Row) -> VideoMeta:
    return VideoMeta(
        video_id=row["video_id"],
        category_id=row["category_id"],
        api_title=row["api_title"],
        channel_title=row["channel_title"],
        duration_s=row["duration_s"],
        tags=json.loads(row["tags_json"] or "[]"),
        unavailable=bool(row["unavailable"]),
    )


def _fetch_batch(
    ids: list[str], api_key: str, session: requests.Session
) -> dict[str, dict]:
    """Call videos.list for up to 50 IDs. Returns {video_id: item_dict}."""
    backoff = 2
    for attempt in range(5):
        try:
            resp = session.get(
                VIDEOS_ENDPOINT,
                params={
                    "part": "snippet,contentDetails",
                    "id": ",".join(ids),
                    "key": api_key,
                },
                timeout=30,
            )
            if resp.status_code == 403:
                log.error("YouTube API quota exceeded (403). Stopping fetch.")
                raise SystemExit(1)
            if resp.status_code == 400:
                log.warning("Bad request for batch %s: %s", ids[:3], resp.text[:200])
                return {}
            resp.raise_for_status()
            data = resp.json()
            return {item["id"]: item for item in data.get("items", [])}
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt == 4:
                raise
            log.warning("Network error (%s), retrying in %ds", exc, backoff)
            time.sleep(backoff)
            backoff *= 2
    return {}


def fetch_metadata(
    events: Iterable[WatchEvent],
    db_path: Path = CACHE_DB,
    api_key: str = YOUTUBE_API_KEY,
    dry_run: bool = False,
) -> dict[str, VideoMeta]:
    """Return a mapping of video_id → VideoMeta for all events. Caches to SQLite."""
    conn = _init_db(db_path)
    conn.row_factory = sqlite3.Row

    # Collect unique video IDs
    all_ids = list({e.video_id for e in events})

    # Partition into cached vs. missing
    cached: dict[str, VideoMeta] = {}
    missing: list[str] = []
    for vid in all_ids:
        row = conn.execute(
            "SELECT * FROM video_meta WHERE video_id = ?", (vid,)
        ).fetchone()
        if row:
            cached[vid] = _row_to_meta(row)
        else:
            missing.append(vid)

    log.info(
        "fetch_metadata: total_unique=%d cached=%d missing=%d",
        len(all_ids),
        len(cached),
        len(missing),
    )

    if not missing:
        conn.close()
        return cached

    if dry_run:
        log.info("dry_run=True, skipping API calls for %d missing IDs", len(missing))
        conn.close()
        return cached

    if not api_key:
        log.warning("No YOUTUBE_API_KEY set; skipping API fetch for %d IDs", len(missing))
        conn.close()
        return cached

    session = requests.Session()
    fetched: dict[str, VideoMeta] = {}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    batches = [missing[i : i + BATCH_SIZE] for i in range(0, len(missing), BATCH_SIZE)]
    for i, batch in enumerate(batches):
        log.debug("API batch %d/%d (%d IDs)", i + 1, len(batches), len(batch))
        items = _fetch_batch(batch, api_key, session)

        for vid in batch:
            if vid in items:
                item = items[vid]
                snippet = item.get("snippet", {})
                cd = item.get("contentDetails", {})
                duration_s = _duration_to_seconds(cd.get("duration", ""))
                meta = VideoMeta(
                    video_id=vid,
                    category_id=snippet.get("categoryId"),
                    api_title=snippet.get("title"),
                    channel_title=snippet.get("channelTitle"),
                    duration_s=duration_s,
                    tags=snippet.get("tags") or [],
                    unavailable=False,
                )
            else:
                # Deleted / private — record as unavailable so we skip forever
                meta = VideoMeta(
                    video_id=vid,
                    category_id=None,
                    api_title=None,
                    channel_title=None,
                    duration_s=None,
                    unavailable=True,
                )

            conn.execute(
                """
                INSERT OR REPLACE INTO video_meta
                    (video_id, category_id, api_title, channel_title, duration_s,
                     tags_json, unavailable, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meta.video_id,
                    meta.category_id,
                    meta.api_title,
                    meta.channel_title,
                    meta.duration_s,
                    json.dumps(meta.tags),
                    1 if meta.unavailable else 0,
                    now,
                ),
            )
            fetched[vid] = meta

        conn.commit()

    conn.close()
    print(
        f"fetch_metadata: {len(cached)} cached, {len(fetched)} fetched "
        f"({len(missing) - len(fetched)} unavailable/deleted)"
    )
    return {**cached, **fetched}


def run(events: list[WatchEvent], dry_run: bool = False) -> dict[str, VideoMeta]:
    return fetch_metadata(events, dry_run=dry_run)
