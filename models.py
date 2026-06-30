from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


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
    confidence: str  # "topic" | "parsed" | "fallback"
    raw_title: str
    channel: str
