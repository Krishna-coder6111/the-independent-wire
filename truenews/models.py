from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class Story:
    author: str
    outlet: str
    title: str
    text: str
    link: str
    published: datetime

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published"] = self.published.isoformat()
        return d


@dataclass
class Config:
    podcast_name: str = "The Independent Wire"
    window_hours: int = 36
    max_items_per_source: int = 4
    max_stories: int = 14
    host_a_name: str = "Alex"
    host_a_voice: str = "en-US-AndrewMultilingualNeural"
    host_b_name: str = "Sam"
    host_b_voice: str = "en-US-EmmaMultilingualNeural"
    site_url: str = ""
    sources: list = field(default_factory=list)
