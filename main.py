#!/usr/bin/env python3
from __future__ import annotations

"""Orchestrator: YouTube Takeout → Last.fm scrobble backfill."""
import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from config import CACHE_DB, CHUNK_SIZE, MAX_DURATION_S, MIN_DURATION_S, OUT_DIR


def _load_rules():
    from rules import load_rules
    return load_rules()


def cmd_parse(args: argparse.Namespace):
    from parse_takeout import run
    events = run(Path(args.takeout))
    _save_state("events", events)
    return events


def cmd_fetch(args: argparse.Namespace):
    from fetch_metadata import run
    events = _load_state("events")
    meta_map = run(events, dry_run=args.dry_run)
    _save_state("meta_map", meta_map)
    return meta_map


def cmd_filter(args: argparse.Namespace):
    from filter_music import meta_from_takeout, run
    events = _load_state("events")
    if args.no_api:
        print("--no-api: filtering on Topic-channel signal and whitelist rules only")
        meta_map = meta_from_takeout(events)
    else:
        meta_map = _load_state("meta_map")
    min_dur = None if args.no_duration_filter else MIN_DURATION_S
    max_dur = None if args.no_duration_filter else MAX_DURATION_S
    rules = _load_rules()
    kept, report, dropped_rows = run(events, meta_map, min_dur=min_dur, max_dur=max_dur, rules=rules)
    _save_state("kept_pairs", kept)
    _save_state("dropped_rows", dropped_rows)
    return kept, report, dropped_rows


def cmd_normalize(args: argparse.Namespace):
    from normalize import run
    kept = _load_state("kept_pairs")
    rules = _load_rules()
    music_events = run(kept, rules=rules)
    _save_state("music_events", music_events)
    return music_events


def cmd_format(args: argparse.Namespace):
    from audit import write_audit_dropped, write_audit_kept
    from format_output import run
    music_events = _load_state("music_events")
    dropped_rows = _load_state("dropped_rows", required=False) or []
    print("\nWriting audit files:")
    write_audit_kept(music_events, OUT_DIR / "audit_kept.csv")
    write_audit_dropped(dropped_rows, OUT_DIR / "audit_dropped.csv")
    paths = run(music_events, fmt=args.format, chunk_size=args.chunk, out_dir=OUT_DIR)
    return paths


def cmd_report(args: argparse.Namespace):
    """Dry-run summary — no output files written."""
    from collections import Counter

    from filter_music import filter_music, meta_from_takeout
    from normalize import normalize

    events = _load_state("events")
    no_api = getattr(args, "no_api", False)

    if no_api:
        meta_map = meta_from_takeout(events)
        api_note = " (Topic-channel only, no API)"
    else:
        meta_map = _load_state("meta_map", required=False) or {}
        api_note = ""

    rules = _load_rules()
    unique_ids = len({e.video_id for e in events})
    cached = sum(1 for e in events if e.video_id in meta_map)

    print("\n=== Pipeline Report ===")
    print(f"  Total watch events    : {len(events):,}")
    print(f"  Unique video IDs      : {unique_ids:,}")
    if not no_api:
        print(f"  Cache hit rate        : {cached/unique_ids*100:.1f}%" if unique_ids else "  Cache hit rate        : N/A")

    if meta_map:
        min_dur = None if args.no_duration_filter else MIN_DURATION_S
        max_dur = None if args.no_duration_filter else MAX_DURATION_S
        kept, report, _ = filter_music(events, meta_map, min_dur, max_dur, rules)
        print(f"  Music events kept{api_note:<26}: {report['kept']:,}")
        print(f"  Dropped               : {report['dropped']:,}")
        print(f"  Category breakdown:")
        for k, v in sorted(report["breakdown"].items()):
            print(f"    {k:<32} {v:>8,}")

        if kept:
            music_events = normalize(kept, rules=rules)
            conf = Counter(e.confidence for e in music_events)
            print(f"\n  Confidence tiers:")
            for c, n in sorted(conf.items()):
                print(f"    {c:<10} {n:>8,}")
            batches = -(-len(music_events) // CHUNK_SIZE)
            print(f"\n  Estimated import batches ({CHUNK_SIZE}/file): {batches}")
    elif not no_api:
        print("  (Run 'fetch' first to see filter/confidence stats)")
    print()


def cmd_run(args: argparse.Namespace):
    """Run all stages end-to-end."""
    from audit import write_audit_dropped, write_audit_kept
    from filter_music import meta_from_takeout
    from filter_music import run as filter_run
    from format_output import run as format_run
    from normalize import run as normalize_run
    from parse_takeout import run as parse_run

    rules = _load_rules()

    events = parse_run(Path(args.takeout))
    _save_state("events", events)

    if args.no_api:
        print("--no-api: skipping Stage 2, filtering on Topic-channel signal only")
        meta_map = meta_from_takeout(events)
    else:
        from fetch_metadata import run as fetch_run
        meta_map = fetch_run(events, dry_run=getattr(args, "dry_run", False))
        _save_state("meta_map", meta_map)

    min_dur = None if args.no_duration_filter else MIN_DURATION_S
    max_dur = None if args.no_duration_filter else MAX_DURATION_S
    kept, _, dropped_rows = filter_run(events, meta_map, min_dur=min_dur, max_dur=max_dur, rules=rules)
    _save_state("kept_pairs", kept)
    _save_state("dropped_rows", dropped_rows)

    music_events = normalize_run(kept, rules=rules)
    _save_state("music_events", music_events)

    print("\nWriting audit files:")
    write_audit_kept(music_events, OUT_DIR / "audit_kept.csv")
    write_audit_dropped(dropped_rows, OUT_DIR / "audit_dropped.csv")

    format_run(music_events, fmt=args.format, chunk_size=args.chunk, out_dir=OUT_DIR)


# ---------------------------------------------------------------------------
# Simple pickle-based inter-stage state
# ---------------------------------------------------------------------------
_STATE_DIR = Path(".pipeline_state")


def _save_state(key: str, obj) -> None:
    import pickle
    _STATE_DIR.mkdir(exist_ok=True)
    (_STATE_DIR / f"{key}.pkl").write_bytes(pickle.dumps(obj))


def _load_state(key: str, required: bool = True):
    import pickle
    path = _STATE_DIR / f"{key}.pkl"
    if not path.exists():
        if required:
            print(f"Error: pipeline state '{key}' not found. Run earlier stages first.", file=sys.stderr)
            sys.exit(1)
        return None
    return pickle.loads(path.read_bytes())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_common(p: argparse.ArgumentParser):
    p.add_argument("--no-duration-filter", action="store_true",
                   help="Disable min/max duration filters")
    p.add_argument("--no-api", action="store_true",
                   help="Skip YouTube API; filter on Topic-channel signal from Takeout data only")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yt-scrobbler",
        description="Convert YouTube Takeout history to Last.fm scrobbles",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # parse
    p_parse = sub.add_parser("parse", help="Stage 1: parse watch-history.json")
    p_parse.add_argument("--takeout", default="watch-history.json",
                         help="Path to watch-history.json")

    # fetch
    p_fetch = sub.add_parser("fetch", help="Stage 2: fetch YouTube metadata (cached)")
    p_fetch.add_argument("--dry-run", action="store_true",
                         help="Skip API calls, use cache only")

    # filter
    p_filter = sub.add_parser("filter", help="Stage 3: filter to music")
    _add_common(p_filter)

    # normalize
    sub.add_parser("normalize", help="Stage 4: extract artist/track")

    # format
    p_format = sub.add_parser("format", help="Stage 5: write output files + audit CSVs")
    p_format.add_argument("--format", choices=["csv", "json", "both"], default="csv")
    p_format.add_argument("--chunk", type=int, default=CHUNK_SIZE,
                          help=f"Entries per output file (default {CHUNK_SIZE})")

    # report
    p_report = sub.add_parser("report", help="Dry-run summary")
    _add_common(p_report)

    # run (all stages)
    p_run = sub.add_parser("run", help="Run all stages end-to-end")
    p_run.add_argument("--takeout", default="watch-history.json")
    p_run.add_argument("--format", choices=["csv", "json", "both"], default="csv")
    p_run.add_argument("--chunk", type=int, default=CHUNK_SIZE)
    p_run.add_argument("--dry-run", action="store_true")
    _add_common(p_run)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "parse": cmd_parse,
        "fetch": cmd_fetch,
        "filter": cmd_filter,
        "normalize": cmd_normalize,
        "format": cmd_format,
        "report": cmd_report,
        "run": cmd_run,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
