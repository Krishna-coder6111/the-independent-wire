"""Fetch recent posts from journalists' public feeds (RSS + Bluesky)."""
import html
import re
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests

from .models import Story

BSKY_FEED_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
USER_AGENT = "true-news-podcast/1.0 (personal news-to-podcast tool)"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_html(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw or "")
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _parse_iso(ts: str) -> datetime:
    # Bluesky timestamps end in 'Z', which fromisoformat() rejects on py<3.11
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc)


def fetch_rss(source: dict, limit: int) -> list[Story]:
    parsed = feedparser.parse(
        source["url"], agent=USER_AGENT, request_headers={"Accept": "application/rss+xml, application/xml, */*"}
    )
    stories = []
    for entry in parsed.entries[: limit * 3]:
        ts = entry.get("published_parsed") or entry.get("updated_parsed")
        if not ts:
            continue
        published = datetime.fromtimestamp(time.mktime(ts), tz=timezone.utc)
        summary = _clean_html(entry.get("summary", ""))
        stories.append(
            Story(
                author=source["name"],
                outlet=source["outlet"],
                title=_clean_html(entry.get("title", "Untitled")),
                text=summary[:900],
                link=entry.get("link", source["url"]),
                published=published,
            )
        )
    return stories


def fetch_bluesky(source: dict, limit: int) -> list[Story]:
    resp = requests.get(
        BSKY_FEED_URL,
        params={"actor": source["handle"], "limit": 40, "filter": "posts_no_replies"},
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    resp.raise_for_status()
    stories = []
    for item in resp.json().get("feed", []):
        if "reason" in item:  # skip reposts of other accounts
            continue
        post = item.get("post", {})
        record = post.get("record", {})
        text = (record.get("text") or "").strip()
        if not text:
            continue
        # Pull in the title of a linked article when the post embeds one
        embed = record.get("embed") or {}
        external = embed.get("external") or {}
        title = external.get("title") or (text[:110] + ("..." if len(text) > 110 else ""))
        rkey = post.get("uri", "").rsplit("/", 1)[-1]
        stories.append(
            Story(
                author=source["name"],
                outlet=source["outlet"],
                title=_clean_html(title),
                text=text[:900],
                link=f"https://bsky.app/profile/{source['handle']}/post/{rkey}",
                published=_parse_iso(record.get("createdAt")),
            )
        )
    return stories


def collect_stories(cfg) -> tuple[list[Story], list[str]]:
    """Fetch all sources; returns (stories within the window, per-source status lines)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.window_hours)
    all_stories: list[Story] = []
    log: list[str] = []
    seen_keys: set = set()

    for source in cfg.sources:
        label = f"{source['name']} ({source['outlet']})"
        try:
            if source["type"] == "rss":
                fetched = fetch_rss(source, cfg.max_items_per_source)
            elif source["type"] == "bluesky":
                fetched = fetch_bluesky(source, cfg.max_items_per_source)
            else:
                log.append(f"SKIP  {label}: unknown type {source['type']!r}")
                continue
        except Exception as exc:
            log.append(f"FAIL  {label}: {exc}")
            continue

        recent = [s for s in fetched if s.published >= cutoff]
        recent.sort(key=lambda s: s.published, reverse=True)
        kept = 0
        for story in recent:
            if kept >= cfg.max_items_per_source:
                break
            key = (story.author, story.title.lower()[:80])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_stories.append(story)
            kept += 1
        log.append(f"OK    {label}: {len(fetched)} fetched, {kept} kept in window")

    all_stories.sort(key=lambda s: s.published, reverse=True)
    return all_stories[: cfg.max_stories], log
