from __future__ import annotations

"""Stage 5b: Two-pass artist validation.

Pass 1 — MusicBrainz local dump (offline, no rate limit):
  Exact case-folded match → mb_exact
  Fuzzy match via rapidfuzz WRatio ≥ threshold → mb_fuzzy (correction = closest MB name)

Pass 2 — Last.fm artist.getCorrection (online, cached in SQLite):
  Only runs for artists that didn't match MB.
  Found → lastfm (correction set if Last.fm suggests a different spelling)
  Not found → unverified

Corrections are noted on MusicEvent.artist_correction but never auto-applied;
the user reviews audit_unverified.csv and audit_corrections.csv to decide.
"""
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

from models import Disposition, MusicEvent, ReviewReason

log = logging.getLogger(__name__)

_FUZZY_THRESHOLD = 88  # WRatio score 0–100; tune down if you want more fuzzy hits


# ---------------------------------------------------------------------------
# MusicBrainz local pass
# ---------------------------------------------------------------------------

def load_mb_artists(dump_path: Path) -> set[str]:
    """Load artist names into a case-folded set from a preprocessed names file or raw dump.

    Preferred input: data/mb_artist_names.txt produced by preprocess_mb.py
    (one case-folded name per line — loads in seconds).

    Also accepts raw .jsonl / .json / .gz / .zst dumps, but those are slow for
    large files; run preprocess_mb.py once instead.
    """
    # Fast path: plain text file (one name per line, already case-folded)
    if dump_path.suffix.lower() == ".txt":
        names: set[str] = set()
        with dump_path.open(encoding="utf-8") as fh:
            for line in fh:
                name = line.strip()
                if name:
                    names.add(name)
        log.info("Loaded %d MB artist names from %s", len(names), dump_path)
        return names

    # Slow path: raw JSON/JSONL dump — stream with ijson if available
    import json

    names = set()

    def _ingest(line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return
        if n := obj.get("name"):
            names.add(n.casefold())
        if sn := obj.get("sort-name"):
            names.add(sn.casefold())
        for alias in obj.get("aliases") or []:
            if an := alias.get("name"):
                names.add(an.casefold())

    suffixes = [s.lower() for s in dump_path.suffixes]

    if ".zst" in suffixes:
        try:
            import zstandard  # type: ignore
        except ImportError:
            raise ImportError(
                "Install zstandard to read .zst files:  uv pip install zstandard"
            )
        import io
        with dump_path.open("rb") as fh:
            dctx = zstandard.ZstdDecompressor()
            with dctx.stream_reader(fh) as reader:
                for line in io.TextIOWrapper(reader, encoding="utf-8"):
                    _ingest(line)
    elif ".gz" in suffixes:
        import gzip
        with gzip.open(dump_path, "rt", encoding="utf-8") as fh:
            for line in fh:
                _ingest(line)
    else:
        with dump_path.open(encoding="utf-8") as fh:
            for line in fh:
                _ingest(line)

    log.info("Loaded %d MB artist name/alias entries from %s", len(names), dump_path)
    return names


def _mb_match(
    artist: str, mb_set: set[str], threshold: int
) -> tuple[str, Optional[str]]:
    """Return (level, suggestion).

    level: "mb_exact" | "mb_fuzzy" | "no_match"
    suggestion: closest MB name for fuzzy hits (already case-folded), else None
    """
    folded = artist.casefold()
    if folded in mb_set:
        return "mb_exact", None

    try:
        from rapidfuzz import fuzz, process  # type: ignore
        result = process.extractOne(
            folded, mb_set, scorer=fuzz.WRatio, score_cutoff=threshold
        )
        if result:
            matched, _score, _ = result
            return "mb_fuzzy", matched
    except ImportError:
        log.warning("rapidfuzz not installed; skipping fuzzy MB pass")

    return "no_match", None


# ---------------------------------------------------------------------------
# Last.fm pass
# ---------------------------------------------------------------------------

def _ensure_cache(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS artist_validation (
            artist_lower TEXT PRIMARY KEY,
            correction   TEXT,
            fetched_at   TEXT NOT NULL
        )
    """)
    conn.commit()


def _lfm_correction(
    artist: str,
    api_key: str,
    conn: sqlite3.Connection,
    rate_delay: float = 0.2,
) -> Optional[str]:
    """Return Last.fm's canonical name for *artist*, or None if unknown.

    Empty string stored in cache means Last.fm knows the artist but has no
    correction; we return None for that too (artist name is already fine).
    Results are cached forever in the SQLite artist_validation table.
    """
    import requests

    key = artist.casefold()
    row = conn.execute(
        "SELECT correction FROM artist_validation WHERE artist_lower = ?", (key,)
    ).fetchone()
    if row is not None:
        return row[0] or None  # "" → None

    try:
        resp = requests.get(
            "https://ws.audioscrobbler.com/2.0/",
            params={
                "method": "artist.getCorrection",
                "artist": artist,
                "api_key": api_key,
                "format": "json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        correction_obj = (data.get("corrections") or {}).get("correction")
        canonical = (correction_obj or {}).get("artist", {}).get("name", "") if correction_obj else ""

        from datetime import datetime, timezone
        conn.execute(
            "INSERT OR REPLACE INTO artist_validation (artist_lower, correction, fetched_at) "
            "VALUES (?, ?, ?)",
            (key, canonical, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        time.sleep(rate_delay)
        return canonical or None

    except Exception as exc:
        log.warning("Last.fm lookup failed for %r: %s", artist, exc)
        return None


# ---------------------------------------------------------------------------
# Two-pass orchestration
# ---------------------------------------------------------------------------

def run(
    music_events: list[MusicEvent],
    mb_set: Optional[set[str]],
    lastfm_api_key: Optional[str],
    cache_db: Path,
    fuzzy_threshold: int = _FUZZY_THRESHOLD,
) -> list[MusicEvent]:
    """Validate artist names in-place; return the same list."""
    unique_artists = list({e.artist for e in music_events})
    print(f"\nValidating {len(unique_artists):,} unique artists "
          f"across {len(music_events):,} events...")

    # --- Pass 1: MusicBrainz ---
    mb_results: dict[str, tuple[str, Optional[str]]] = {}
    if mb_set:
        print(f"  Pass 1 — MusicBrainz ({len(mb_set):,} names loaded)...")
        for artist in unique_artists:
            mb_results[artist] = _mb_match(artist, mb_set, fuzzy_threshold)
        exact  = sum(1 for l, _ in mb_results.values() if l == "mb_exact")
        fuzzy  = sum(1 for l, _ in mb_results.values() if l == "mb_fuzzy")
        missed = len(unique_artists) - exact - fuzzy
        print(f"    mb_exact={exact:,}  mb_fuzzy={fuzzy:,}  unmatched={missed:,}")
    else:
        print("  Pass 1 — MusicBrainz skipped (no dump path configured)")
        for artist in unique_artists:
            mb_results[artist] = ("no_match", None)

    # --- Pass 2: Last.fm — only for unmatched artists ---
    needs_lfm = [a for a, (lvl, _) in mb_results.items() if lvl == "no_match"]
    lfm_results: dict[str, Optional[str]] = {}

    if lastfm_api_key and needs_lfm:
        print(f"  Pass 2 — Last.fm ({len(needs_lfm):,} artists to check)...")
        conn = sqlite3.connect(cache_db)
        _ensure_cache(conn)
        try:
            cached = 0
            fetched = 0
            for i, artist in enumerate(needs_lfm, 1):
                cached_before = conn.execute(
                    "SELECT 1 FROM artist_validation WHERE artist_lower = ?",
                    (artist.casefold(),),
                ).fetchone()
                result = _lfm_correction(artist, lastfm_api_key, conn)
                lfm_results[artist] = result
                if cached_before:
                    cached += 1
                else:
                    fetched += 1
                if i % 200 == 0:
                    print(f"    {i:,}/{len(needs_lfm):,}  "
                          f"(cache hits so far: {cached:,}, API calls: {fetched:,})")
        finally:
            conn.close()
        found = sum(1 for v in lfm_results.values() if v is not None)
        unverified = len(needs_lfm) - found
        print(f"    lastfm={found:,}  unverified={unverified:,}")
    elif needs_lfm:
        print(f"  Pass 2 — Last.fm skipped (no LASTFM_API_KEY); "
              f"{len(needs_lfm):,} artists remain unverified")

    # --- Apply results ---
    for event in music_events:
        lvl, suggestion = mb_results.get(event.artist, ("no_match", None))
        if lvl == "mb_exact":
            event.artist_validation = "mb_exact"
        elif lvl == "mb_fuzzy":
            event.artist_validation = "mb_fuzzy"
            event.artist_correction = suggestion  # closest MB name (case-folded)
        else:
            lfm = lfm_results.get(event.artist)
            if lfm is not None:
                event.artist_validation = "lastfm"
                if lfm.casefold() != event.artist.casefold():
                    event.artist_correction = lfm  # Last.fm's canonical spelling
            else:
                event.artist_validation = "unverified"
                # Downgrade disposition: unverified artist on a ready event → needs_review
                if event.disposition == Disposition.READY:
                    event.disposition = Disposition.NEEDS_REVIEW
                    event.review_reason = ReviewReason.UNVERIFIED_ARTIST

    # Summary
    from collections import Counter
    val_counts = Counter(e.artist_validation for e in music_events)
    disp_counts = Counter(e.disposition for e in music_events)
    print("\n  Validation summary (events):")
    for tier in ("mb_exact", "mb_fuzzy", "lastfm", "unverified"):
        print(f"    {tier:<20} {val_counts[tier]:>8,}")
    print("\n  Disposition after validation:")
    for d in ("ready", "needs_review"):
        print(f"    {d:<20} {disp_counts[d]:>8,}")

    return music_events
