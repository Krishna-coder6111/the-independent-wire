"""Build the static GitHub Pages site under docs/.

Layout:
    docs/index.html                     episode list with audio players
    docs/feed.xml                       podcast RSS feed (subscribable in podcast apps)
    docs/episodes/YYYY-MM-DD/episode.mp3
    docs/episodes/YYYY-MM-DD/script.txt
    docs/episodes/YYYY-MM-DD/meta.json
"""
import json
import shutil
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{podcast_name}</title>
<link rel="alternate" type="application/rss+xml" title="{podcast_name}" href="feed.xml">
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: Georgia, 'Times New Roman', serif; max-width: 720px;
         margin: 0 auto; padding: 2rem 1rem; line-height: 1.6;
         background: #faf7f2; color: #2b2620; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #1c1a17; color: #e8e2d8; }}
    .card {{ background: #262320 !important; border-color: #3a3630 !important; }}
  }}
  header {{ border-bottom: 3px double currentColor; margin-bottom: 2rem; padding-bottom: 1rem; }}
  h1 {{ margin: 0 0 .25rem; font-size: 2rem; }}
  .tagline {{ font-style: italic; opacity: .75; margin: 0; }}
  .subscribe {{ font-size: .9rem; margin-top: .75rem; }}
  .card {{ background: #fffdf9; border: 1px solid #e3dccf; border-radius: 10px;
           padding: 1.25rem 1.5rem; margin-bottom: 1.5rem; }}
  .card h2 {{ margin: 0 0 .5rem; font-size: 1.25rem; }}
  audio {{ width: 100%; margin: .5rem 0 .75rem; }}
  details summary {{ cursor: pointer; font-size: .95rem; }}
  details ul {{ font-size: .9rem; padding-left: 1.25rem; }}
  a {{ color: #a35a2a; }}
  footer {{ font-size: .85rem; opacity: .7; margin-top: 2.5rem;
            border-top: 1px solid currentColor; padding-top: 1rem; }}
</style>
</head>
<body>
<header>
  <h1>{podcast_name}</h1>
  <p class="tagline">Daily reporting from independent &amp; investigative journalists, in podcast form.</p>
  <p class="subscribe">Subscribe in a podcast app: <a href="feed.xml">RSS feed</a></p>
</header>
<main>
{episode_cards}
</main>
<footer>
  Every story is attributed on air to the journalist who reported it. This feed is an
  automated round-up; all reporting belongs to the cited journalists &mdash; support them
  directly via the links in each episode's story list.
</footer>
</body>
</html>
"""

CARD_TEMPLATE = """<article class="card">
  <h2>{title}</h2>
  <audio controls preload="none" src="episodes/{date}/episode.mp3"></audio>
  <details>
    <summary>{n_stories} stories covered &mdash; sources &amp; links</summary>
    <ul>
{story_items}
    </ul>
  </details>
  <p style="font-size:.85rem"><a href="episodes/{date}/script.txt">Read the transcript</a></p>
</article>
"""


def publish_episode(cfg, episode_date: datetime, episode_dir: Path, stories, project_root: Path) -> Path:
    """Copy today's episode into docs/ and regenerate the site + feed."""
    docs = project_root / "docs"
    date_str = episode_date.strftime("%Y-%m-%d")
    dest = docs / "episodes" / date_str
    dest.mkdir(parents=True, exist_ok=True)

    shutil.copy2(episode_dir / "episode.mp3", dest / "episode.mp3")
    shutil.copy2(episode_dir / "script.txt", dest / "script.txt")
    meta = {
        "date": date_str,
        "title": f"{cfg.podcast_name} — {episode_date.strftime('%B %d, %Y')}",
        "published": episode_date.isoformat(),
        "stories": [s.to_dict() for s in stories],
    }
    (dest / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    (docs / ".nojekyll").write_text("", encoding="utf-8")
    _rebuild_index(cfg, docs)
    _rebuild_feed(cfg, docs)
    return docs


def _load_episodes(docs: Path) -> list[dict]:
    episodes = []
    ep_root = docs / "episodes"
    if not ep_root.exists():
        return episodes
    for meta_path in sorted(ep_root.glob("*/meta.json"), reverse=True):
        try:
            episodes.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return episodes


def _rebuild_index(cfg, docs: Path) -> None:
    cards = []
    for ep in _load_episodes(docs):
        items = "\n".join(
            f'      <li>{escape(s["author"])} ({escape(s["outlet"])}): '
            f'<a href="{escape(s["link"])}">{escape(s["title"])}</a></li>'
            for s in ep["stories"]
        )
        cards.append(
            CARD_TEMPLATE.format(
                title=escape(ep["title"]),
                date=ep["date"],
                n_stories=len(ep["stories"]),
                story_items=items,
            )
        )
    html = PAGE_TEMPLATE.format(
        podcast_name=escape(cfg.podcast_name),
        episode_cards="\n".join(cards) if cards else "<p>No episodes yet.</p>",
    )
    (docs / "index.html").write_text(html, encoding="utf-8")


def _rebuild_feed(cfg, docs: Path) -> None:
    base = (cfg.site_url or "").rstrip("/")
    items = []
    for ep in _load_episodes(docs):
        mp3 = docs / "episodes" / ep["date"] / "episode.mp3"
        size = mp3.stat().st_size if mp3.exists() else 0
        url = f"{base}/episodes/{ep['date']}/episode.mp3"
        pub = format_datetime(datetime.fromisoformat(ep["published"]))
        n = len(ep["stories"])
        authors = sorted({s["author"] for s in ep["stories"]})
        description = (
            f"{n} stories from independent journalists including "
            f"{', '.join(authors[:6])}. Full source links on the episode page."
        )
        items.append(
            "  <item>\n"
            f"    <title>{escape(ep['title'])}</title>\n"
            f"    <description>{escape(description)}</description>\n"
            f"    <enclosure url=\"{escape(url)}\" length=\"{size}\" type=\"audio/mpeg\"/>\n"
            f"    <guid isPermaLink=\"false\">{escape(ep['date'])}</guid>\n"
            f"    <pubDate>{pub}</pubDate>\n"
            "  </item>"
        )
    now = format_datetime(datetime.now(timezone.utc))
    feed = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">\n'
        "<channel>\n"
        f"  <title>{escape(cfg.podcast_name)}</title>\n"
        f"  <link>{escape(base or '.')}</link>\n"
        "  <language>en-us</language>\n"
        "  <description>Automated daily round-up of reporting from independent and "
        "investigative journalists, with on-air attribution and source links.</description>\n"
        "  <itunes:author>Automated round-up</itunes:author>\n"
        f"  <lastBuildDate>{now}</lastBuildDate>\n"
        + "\n".join(items)
        + "\n</channel>\n</rss>\n"
    )
    (docs / "feed.xml").write_text(feed, encoding="utf-8")
