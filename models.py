from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Disposition constants — set during filter/normalize/validate stages
# ---------------------------------------------------------------------------

class Disposition:
    READY        = "ready"         # confirmed music, clean scrobble
    NEEDS_REVIEW = "needs_review"  # confirmed music, but needs manual work
    NOT_MUSIC    = "not_music"     # confirmed not music
    UNDECIDED    = "undecided"     # insufficient signal (no API metadata, etc.)


class ReviewReason:
    POOR_TITLE        = "poor_title"         # couldn't parse artist/track (fallback confidence)
    FULL_ALBUM        = "full_album"         # long duration + album title signals
    OST               = "ost"               # soundtrack/score signals
    COMPILATION       = "compilation"       # various artists / multi-artist
    UNVERIFIED_ARTIST = "unverified_artist" # artist not found in MB or Last.fm


@dataclass
class WatchEvent:
    video_id: str
    raw_title: str
    channel: str
    watched_at: datetime  # timezone-aware UTC


@dataclass
class VideoMeta:
    video_id: str
    category_id: Optional[str]  # None = unavailable/private
    api_title: Optional[str]
    channel_title: Optional[str]
    duration_s: Optional[int]
    tags: list[str] = field(default_factory=list)
    unavailable: bool = False


@dataclass
class MusicEvent:
    video_id: str
    artist: str
    track: str
    album: Optional[str]
    watched_at: datetime
    confidence: str       # "topic" | "parsed" | "fallback" | "override" | "title_pattern"
    raw_title: str
    channel: str
    disposition: str = Disposition.READY          # set by normalize, updated by validate
    review_reason: Optional[str] = None           # set when disposition=needs_review
    artist_validation: str = "unvalidated"        # mb_exact|mb_fuzzy|lastfm|unverified|unvalidated
    artist_correction: Optional[str] = None       # suggested canonical name (not auto-applied)
