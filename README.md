# yt-scrobbler

Convert years of YouTube watch history (Google Takeout) into backdated Last.fm scrobbles.

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env
# edit .env and add your YouTube Data API v3 key

uv run python main.py run --takeout watch-history.json
# output files land in out/batch_001.csv, batch_002.csv, ...
```

Import each `batch_NNN.csv` into [Last.fm-Scrubbler-WPF](https://github.com/SHOEGAZEssb/Last.fm-Scrubbler-WPF) one per day (≤ 3,000 scrobbles/day limit).

## Running without a YouTube API key

Pass `--no-api` to skip Stage 2 entirely. The filter uses only the `" - Topic"` channel
signal already present in the Takeout data — high precision, but lower recall (VEVO and
other non-Topic music channels won't be picked up).

```bash
uv run python main.py run --takeout watch-history.json --no-api
```

You can always re-run later with an API key to catch the rest.

## Stage-by-stage usage

```bash
uv run python main.py parse     --takeout watch-history.json   # Stage 1
uv run python main.py fetch                                    # Stage 2: YouTube API (cached)
uv run python main.py fetch     --dry-run                      # Stage 2: cache only
uv run python main.py filter                                   # Stage 3: keep music only
uv run python main.py filter    --no-api                       # Stage 3: Topic-channel only
uv run python main.py normalize                                # Stage 4: extract artist/track
uv run python main.py format    --format csv   --chunk 2800    # Stage 5: write output
uv run python main.py report                                   # dry-run summary
uv run python main.py report    --no-api                       # summary without API
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `YOUTUBE_API_KEY` | — | YouTube Data API v3 key (required for Stage 2) |
| `MIN_DURATION_S` | 30 | Drop clips shorter than this (seconds) |
| `MAX_DURATION_S` | 900 | Drop videos longer than this (seconds, ~15 min) |
| `CHUNK_SIZE` | 2800 | Entries per output file |

Pass `--no-duration-filter` to any subcommand to disable duration filtering.

## Output format

**CSV** (default, `--format csv`): `Artist,Track,Album,Timestamp` (Unix epoch seconds).  
**JSON** (`--format json`): `[{"Artist":…,"Track":…,"Album":…,"Timestamp":…}, …]` for Scrubbler's File Parse mode.  
**Both** (`--format both`): writes both.

Low-confidence entries (no `Artist - Track` split, no Topic channel) are written to `out/review.csv` for manual inspection before importing.

## Pipeline architecture

```
watch-history.json
      │
[1] parse_takeout   → WatchEvent list (videoId, title, channel, timestamp)
      │
[2] fetch_metadata  → YouTube API videos.list, cached in cache.sqlite
      │
[3] filter_music    → keep categoryId == "10" + Topic-channel override
      │
[4] normalize       → artist/track extraction + confidence score
      │
[5] format_output   → CSV/JSON chunks ≤ 2,800 entries each
      │
out/batch_001.csv, batch_002.csv, ...
```

Confidence tiers:
- **topic**: `"Channel - Topic"` channel (auto-generated, very clean)
- **parsed**: `"Artist - Track"` split detected in title
- **fallback**: channel = artist, full title = track (check `out/review.csv`)

## Getting a YouTube Data API key

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → Enable "YouTube Data API v3"
3. Create an API key under Credentials
4. Default quota: 10,000 units/day = up to 500,000 unique video lookups/day

## Notes

- **Replays are kept**: every watch timestamp is its own scrobble entry.
- **Cache everything**: Stage 2 results are cached in `cache.sqlite`; re-runs cost zero API quota.
- **Multi-day import**: a large archive splits into multiple files, one uploaded per day.
- **Locale**: the `watch-history.json` title prefix varies by account language; the pipeline uses the API's `snippet.title` as the canonical name.
