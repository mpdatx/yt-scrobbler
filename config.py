from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

YOUTUBE_API_KEY: str = os.getenv("YOUTUBE_API_KEY", "")

BASE_DIR = Path(__file__).parent
CACHE_DB = BASE_DIR / "cache.sqlite"
OUT_DIR = BASE_DIR / "out"

# YouTube Music category ID
MUSIC_CATEGORY_ID = "10"

# Duration filters (seconds). Set to None to disable.
MIN_DURATION_S = 30
MAX_DURATION_S = 60 * 15  # 15 min — can be overridden per run

# Output chunking
CHUNK_SIZE = 2800

# Confidence tiers
CONF_TOPIC = "topic"
CONF_PARSED = "parsed"
CONF_FALLBACK = "fallback"
