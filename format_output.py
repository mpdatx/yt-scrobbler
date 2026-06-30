"""Stage 5: Format music events into Scrubbler-compatible CSV/JSON chunks."""
import csv
import json
from datetime import timezone
from pathlib import Path
from typing import Iterable

from config import CHUNK_SIZE, OUT_DIR
from models import MusicEvent


def _to_epoch(event: MusicEvent) -> int:
    dt = event.watched_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def format_csv(events: list[MusicEvent], out_dir: Path, chunk_size: int) -> list[Path]:
    """Write chunked CSV files. Columns: Artist,Track,Album,Timestamp"""
    out_dir.mkdir(parents=True, exist_ok=True)
    sorted_events = sorted(events, key=lambda e: e.watched_at)
    paths: list[Path] = []
    for i in range(0, len(sorted_events), chunk_size):
        chunk = sorted_events[i : i + chunk_size]
        batch_num = (i // chunk_size) + 1
        path = out_dir / f"batch_{batch_num:03d}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["Artist", "Track", "Album", "Timestamp"])
            for e in chunk:
                writer.writerow([e.artist, e.track, e.album or "", _to_epoch(e)])
        paths.append(path)
    return paths


def format_json(events: list[MusicEvent], out_dir: Path, chunk_size: int) -> list[Path]:
    """Write chunked JSON files for Last.fm-Scrubbler-WPF File Parse."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sorted_events = sorted(events, key=lambda e: e.watched_at)
    paths: list[Path] = []
    for i in range(0, len(sorted_events), chunk_size):
        chunk = sorted_events[i : i + chunk_size]
        batch_num = (i // chunk_size) + 1
        path = out_dir / f"batch_{batch_num:03d}.json"
        records = [
            {
                "Artist": e.artist,
                "Track": e.track,
                "Album": e.album or "",
                "Timestamp": _to_epoch(e),
            }
            for e in chunk
        ]
        with path.open("w", encoding="utf-8") as fh:
            json.dump(records, fh, ensure_ascii=False, indent=2)
        paths.append(path)
    return paths


def run(
    events: list[MusicEvent],
    fmt: str = "csv",
    chunk_size: int = CHUNK_SIZE,
    out_dir: Path = OUT_DIR,
) -> list[Path]:
    if fmt == "csv":
        paths = format_csv(events, out_dir, chunk_size)
    elif fmt == "json":
        paths = format_json(events, out_dir, chunk_size)
    elif fmt == "both":
        paths = format_csv(events, out_dir, chunk_size) + format_json(events, out_dir, chunk_size)
    else:
        raise ValueError(f"Unknown format: {fmt!r}. Use 'csv', 'json', or 'both'.")

    print(f"\nOutput: {len(paths)} file(s) in {out_dir}")
    for p in paths:
        print(f"  {p.name}")
    return paths
