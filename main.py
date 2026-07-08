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
    from filter_music import pre_classify
    events = _load_state("events")
    rules = _load_rules()
    _, needs_metadata, _ = pre_classify(events, rules)
    print(f"Pre-classify: {len(needs_metadata):,} events need metadata "
          f"({len(events) - len(needs_metadata):,} resolved without API)")
    meta_map = run(needs_metadata, dry_run=args.dry_run)
    _save_state("meta_map", meta_map)
    return meta_map


def cmd_filter(args: argparse.Namespace):
    from filter_music import pre_classify, run
    events = _load_state("events")
    rules = _load_rules()
    min_dur = None if args.no_duration_filter else MIN_DURATION_S
    max_dur = None if args.no_duration_filter else MAX_DURATION_S

    definite_music, needs_metadata, pre_dropped = pre_classify(events, rules)

    if args.no_api:
        print("--no-api: skipping metadata fetch, needs_metadata bucket dropped")
        meta_map = {}
    else:
        meta_map = _load_state("meta_map")

    confirmed_music, report, not_music_rows, undecided_events = run(
        needs_metadata, meta_map,
        definite_music_events=definite_music,
        pre_dropped_rows=pre_dropped,
        min_dur=min_dur, max_dur=max_dur,
    )
    _save_state("confirmed_music", confirmed_music)
    _save_state("not_music_rows", not_music_rows)
    _save_state("undecided_events", undecided_events)
    _save_state("needs_metadata", needs_metadata)
    return confirmed_music, report, not_music_rows, undecided_events


def cmd_normalize(args: argparse.Namespace):
    from normalize import run
    confirmed_music = _load_state("confirmed_music")
    rules = _load_rules()
    music_events = run(confirmed_music, rules=rules)
    _save_state("music_events", music_events)
    return music_events


def cmd_format(args: argparse.Namespace):
    from audit import (write_audit_needs_metadata, write_audit_needs_review,
                       write_audit_not_music, write_audit_ready, write_audit_undecided)
    from format_output import run
    music_events = _load_state("music_events")
    not_music_rows = _load_state("not_music_rows", required=False) or []
    undecided_events = _load_state("undecided_events", required=False) or []
    needs_metadata = _load_state("needs_metadata", required=False) or []
    print("\nWriting audit files:")
    write_audit_ready(music_events, OUT_DIR / "audit_ready.csv")
    write_audit_needs_review(music_events, OUT_DIR / "review")
    write_audit_not_music(not_music_rows, OUT_DIR / "audit_not_music.csv")
    write_audit_undecided(undecided_events, OUT_DIR / "audit_undecided.csv")
    write_audit_needs_metadata(needs_metadata, OUT_DIR / "audit_needs_metadata.csv")
    paths = run(music_events, fmt=args.format, chunk_size=args.chunk, out_dir=OUT_DIR)
    return paths


def cmd_validate(args: argparse.Namespace):
    """Two-pass artist validation: MusicBrainz local dump → Last.fm API."""
    import validate as val_mod
    from audit import write_audit_corrections, write_audit_unverified
    from config import CACHE_DB, LASTFM_API_KEY

    music_events = _load_state("music_events")

    mb_set = None
    mb_path = Path(args.mb_dump) if args.mb_dump else None
    if mb_path:
        if not mb_path.exists():
            print(f"Error: MB dump not found at {mb_path}", file=sys.stderr)
            sys.exit(1)
        print(f"Loading MusicBrainz dump: {mb_path}")
        mb_set = val_mod.load_mb_artists(mb_path)

    api_key = args.lastfm_key or LASTFM_API_KEY or None

    music_events = val_mod.run(
        music_events,
        mb_set=mb_set,
        lastfm_api_key=api_key,
        cache_db=CACHE_DB,
        fuzzy_threshold=args.fuzzy_threshold,
    )
    _save_state("music_events", music_events)

    print("\nWriting validation audit files:")
    write_audit_unverified(music_events, OUT_DIR / "audit_unverified.csv")
    write_audit_corrections(music_events, OUT_DIR / "audit_corrections.csv")


def cmd_triage(args: argparse.Namespace):
    """Interactively review unclassified channels and add rules."""
    from triage import run_triage
    needs_metadata = _load_state("needs_metadata", required=False)
    if not needs_metadata:
        print("No needs_metadata state found. Run 'parse' (and optionally 'filter') first.")
        return
    run_triage(needs_metadata, top_n=args.top_n, sample_size=args.sample)


def cmd_report(args: argparse.Namespace):
    """Dry-run summary — no output files written."""
    from collections import Counter

    from filter_music import filter_music
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

    if meta_map or no_api:
        from filter_music import pre_classify
        min_dur = None if args.no_duration_filter else MIN_DURATION_S
        max_dur = None if args.no_duration_filter else MAX_DURATION_S
        definite_music, needs_metadata, pre_dropped = pre_classify(events, rules)
        confirmed_music, report, _, undecided = filter_music(
            needs_metadata, meta_map,
            definite_music_events=definite_music,
            pre_dropped_rows=pre_dropped,
            min_dur=min_dur, max_dur=max_dur,
        )
        print(f"  Confirmed music{api_note:<28}: {report['confirmed_music']:,}")
        print(f"  Not music             : {report['not_music']:,}")
        print(f"  Undecided             : {report['undecided']:,}")
        print(f"  Category breakdown:")
        for k, v in sorted(report["breakdown"].items()):
            print(f"    {k:<32} {v:>8,}")

        if confirmed_music:
            music_events = normalize(confirmed_music, rules=rules)
            from models import Disposition
            ready = sum(1 for e in music_events if e.disposition == Disposition.READY)
            needs_review = sum(1 for e in music_events if e.disposition == Disposition.NEEDS_REVIEW)
            conf = Counter(e.confidence for e in music_events)
            print(f"\n  Disposition after normalize:")
            print(f"    ready        {ready:>8,}")
            print(f"    needs_review {needs_review:>8,}")
            print(f"\n  Confidence tiers:")
            for c, n in sorted(conf.items()):
                print(f"    {c:<10} {n:>8,}")
            batches = -(-ready // CHUNK_SIZE)
            print(f"\n  Estimated import batches ({CHUNK_SIZE}/file): {batches}")
    elif not no_api:
        print("  (Run 'fetch' first to see filter/confidence stats)")
    print()


def cmd_run(args: argparse.Namespace):
    """Run all stages end-to-end."""
    from audit import (write_audit_corrections, write_audit_needs_metadata,
                       write_audit_needs_review, write_audit_not_music,
                       write_audit_ready, write_audit_undecided, write_audit_unverified)
    from filter_music import pre_classify
    from filter_music import run as filter_run
    from format_output import run as format_run
    from normalize import run as normalize_run
    from parse_takeout import run as parse_run

    rules = _load_rules()

    events = parse_run(Path(args.takeout))
    _save_state("events", events)

    definite_music, needs_metadata, pre_dropped = pre_classify(events, rules)
    unique_needs = len({e.video_id for e in needs_metadata})
    print(f"Pre-classify: {len(definite_music):,} definite music, "
          f"{unique_needs:,} unique IDs need metadata, "
          f"{len(pre_dropped):,} events dropped early")

    _save_state("needs_metadata", needs_metadata)

    if args.no_api:
        print("--no-api: skipping Stage 2")
        meta_map = {}
    else:
        from fetch_metadata import run as fetch_run
        meta_map = fetch_run(needs_metadata, dry_run=getattr(args, "dry_run", False))
        _save_state("meta_map", meta_map)

    min_dur = None if args.no_duration_filter else MIN_DURATION_S
    max_dur = None if args.no_duration_filter else MAX_DURATION_S
    confirmed_music, _, not_music_rows, undecided_events = filter_run(
        needs_metadata, meta_map,
        definite_music_events=definite_music,
        pre_dropped_rows=pre_dropped,
        min_dur=min_dur, max_dur=max_dur,
    )
    _save_state("confirmed_music", confirmed_music)
    _save_state("not_music_rows", not_music_rows)
    _save_state("undecided_events", undecided_events)

    music_events = normalize_run(confirmed_music, rules=rules)

    # Optional validation pass
    mb_dump = getattr(args, "mb_dump", None)
    lastfm_key = getattr(args, "lastfm_key", None)
    fuzzy_threshold = getattr(args, "fuzzy_threshold", 88)
    if mb_dump or lastfm_key:
        import validate as val_mod
        from config import LASTFM_API_KEY
        mb_set = None
        if mb_dump:
            mb_path = Path(mb_dump)
            if not mb_path.exists():
                print(f"Error: MB dump not found at {mb_path}", file=sys.stderr)
                sys.exit(1)
            print(f"Loading MusicBrainz dump: {mb_path}")
            mb_set = val_mod.load_mb_artists(mb_path)
        api_key = lastfm_key or LASTFM_API_KEY or None
        music_events = val_mod.run(
            music_events,
            mb_set=mb_set,
            lastfm_api_key=api_key,
            cache_db=CACHE_DB,
            fuzzy_threshold=fuzzy_threshold,
        )

    _save_state("music_events", music_events)

    print("\nWriting audit files:")
    write_audit_ready(music_events, OUT_DIR / "audit_ready.csv")
    write_audit_needs_review(music_events, OUT_DIR / "review")
    write_audit_not_music(not_music_rows, OUT_DIR / "audit_not_music.csv")
    write_audit_undecided(undecided_events, OUT_DIR / "audit_undecided.csv")
    write_audit_needs_metadata(needs_metadata, OUT_DIR / "audit_needs_metadata.csv")
    if mb_dump or lastfm_key:
        write_audit_unverified(music_events, OUT_DIR / "audit_unverified.csv")
        write_audit_corrections(music_events, OUT_DIR / "audit_corrections.csv")

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

    # validate
    p_val = sub.add_parser("validate", help="Stage 5b: two-pass artist validation (MB + Last.fm)")
    p_val.add_argument("--mb-dump", default=None,
                       help="Path to MusicBrainz artist JSON dump (.jsonl/.gz/.zst)")
    p_val.add_argument("--lastfm-key", default=None,
                       help="Last.fm API key (overrides LASTFM_API_KEY env var)")
    p_val.add_argument("--fuzzy-threshold", type=int, default=88,
                       help="rapidfuzz WRatio threshold for MB fuzzy match (default 88)")

    # triage
    p_triage = sub.add_parser("triage", help="Interactively whitelist/blacklist unclassified channels")
    p_triage.add_argument("--top-n", type=int, default=30,
                          help="Review the top N channels by event count (default 30)")
    p_triage.add_argument("--sample", type=int, default=5,
                          help="Sample titles to show per channel (default 5)")

    # report
    p_report = sub.add_parser("report", help="Dry-run summary")
    _add_common(p_report)

    # run (all stages)
    p_run = sub.add_parser("run", help="Run all stages end-to-end")
    p_run.add_argument("--takeout", default="watch-history.json")
    p_run.add_argument("--format", choices=["csv", "json", "both"], default="csv")
    p_run.add_argument("--chunk", type=int, default=CHUNK_SIZE)
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--mb-dump", default=None,
                       help="Path to MB artist names file to enable validation pass")
    p_run.add_argument("--lastfm-key", default=None,
                       help="Last.fm API key for validation pass (overrides env var)")
    p_run.add_argument("--fuzzy-threshold", type=int, default=88,
                       help="rapidfuzz WRatio threshold for MB fuzzy match (default 88)")
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
        "validate": cmd_validate,
        "triage": cmd_triage,
        "report": cmd_report,
        "run": cmd_run,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
