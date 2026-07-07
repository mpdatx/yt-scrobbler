#!/usr/bin/env python3
from __future__ import annotations

"""One-time script: extract artist names from a MusicBrainz JSON dump.

Streams the (large) input file with ijson so it never loads fully into memory,
writes one case-folded name per line to data/mb_artist_names.txt.

Usage:
    python preprocess_mb.py path/to/artist.json
    python preprocess_mb.py path/to/artist.json --out data/mb_artist_names.txt
"""
import argparse
import sys
from pathlib import Path


def extract_names(src: Path, dst: Path) -> None:
    try:
        import ijson  # type: ignore
    except ImportError:
        print("ijson is required: uv pip install ijson", file=sys.stderr)
        sys.exit(1)

    dst.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    count = 0

    print(f"Streaming {src} ...")

    with src.open("rb") as fh, dst.open("w", encoding="utf-8") as out:
        # Try array of objects first (item = each element of the top-level array)
        try:
            for obj in ijson.items(fh, "item"):
                for field in ("name", "sort-name", "sort_name"):
                    if v := obj.get(field):
                        folded = v.casefold()
                        if folded not in seen:
                            seen.add(folded)
                            out.write(folded + "\n")
                for alias in obj.get("aliases") or []:
                    if v := alias.get("name"):
                        folded = v.casefold()
                        if folded not in seen:
                            seen.add(folded)
                            out.write(folded + "\n")
                count += 1
                if count % 500_000 == 0:
                    print(f"  {count:,} artists processed, {len(seen):,} unique names...")
        except Exception as exc:
            print(f"Warning: ijson array parse failed ({exc}), retrying as JSONL...",
                  file=sys.stderr)
            fh.seek(0)
            import json
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for field in ("name", "sort-name", "sort_name"):
                    if v := obj.get(field):
                        folded = v.casefold()
                        if folded not in seen:
                            seen.add(folded)
                            out.write(folded + "\n")
                count += 1

    print(f"Done: {count:,} artists → {len(seen):,} unique names → {dst}")


def main():
    parser = argparse.ArgumentParser(description="Extract MB artist names to a flat text file")
    parser.add_argument("src", help="Path to MusicBrainz artist JSON dump")
    parser.add_argument("--out", default="data/mb_artist_names.txt",
                        help="Output path (default: data/mb_artist_names.txt)")
    args = parser.parse_args()
    extract_names(Path(args.src), Path(args.out))


if __name__ == "__main__":
    main()
