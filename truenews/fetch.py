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
_HTTP_URL_RE = re.compile(r"https?://\S+")
_BARE_URL_RE = re.compile(r"\b[\w.-]+\.(?:com|co|org|net|news|info|app|social|io|us|uk)(?:/\S*)?\b")
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]+")
# Bluesky posts that are fundraising/self-promo rather than reporting
_PROMO_RE = re.compile(
    r"\b(subscri\w+|upgrad\w+|paywall\w*|pledge\w*|donat\w+|tip jar|"
    r"merch|discount\w*|free trial|founding member)\b",
    re.IGNORECASE,
)


def _clean_html(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw or "")
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


_MENTION_RE = re.compile(r"@[\w.-]*")
_CAPTION_RE = re.compile(
    r"(photo by|getty images|via getty|via anadolu|via reuters|via ap\b|"
    r"screenshot|illustration by|photograph by)",
    re.IGNORECASE,
)
_EDITOR_NOTE_RE = re.compile(r"editor['’]?s note:\s*", re.IGNORECASE)
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_CTA_PHRASE_RE = re.compile(
    r"\b(subscribe now|share this post|upgrade to paid|leave a comment|"
    r"read in app|give a gift subscription)\b[.!]?\s*",
    re.IGNORECASE,
)
_PROMO_SENT_RE = re.compile(
    r"(reader-funded|paid subscriber|tax-deductible|donation|pledge your support|"
    r"without your support|support (our|this|independent) (work|journalism|newsletter)|"
    r"consider (subscribing|becoming|supporting)|free and paid subscription)",
    re.IGNORECASE,
)


def clean_for_speech(text: str) -> str:
    """Strip URLs, @-handles, and emoji so TTS doesn't read them out."""
    text = _HTTP_URL_RE.sub("", text)
    text = _BARE_URL_RE.sub("", text)
    text = _MENTION_RE.sub("", text)
    text = _EMOJI_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip()
    return text.rstrip("—-– ").strip()


def strip_boilerplate(text: str) -> str:
    """Remove photo captions, subscribe buttons, fundraising asks, and
    editor's-note prefixes from article excerpts."""
    text = _EDITOR_NOTE_RE.sub("", text)
    text = _CTA_PHRASE_RE.sub("", text)  # button labels glued into the text flow
    sentences = _SENT_SPLIT_RE.split(text)
    drop = set()
    for i, sentence in enumerate(sentences):
        if _PROMO_SENT_RE.search(sentence):
            drop.add(i)
        if _CAPTION_RE.search(sentence):
            drop.add(i)
            if i > 0:
                drop.add(i - 1)  # the caption text usually precedes its credit line
    return " ".join(s for i, s in enumerate(sentences) if i not in drop).strip()


def _is_noise(story: Story) -> bool:
    """Filter social posts with no reporting value (promo, link-only one-liners)."""
    if story.outlet.lower() != "bluesky":
        return False  # newsletter items are actual articles - keep them
    if _PROMO_RE.search(story.text):
        return True
    if len(clean_for_speech(story.text)) < 60:  # link drops and one-word reactions
        return True
    return False


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
        # Prefer the full article body when the feed provides it; a couple of
        # opening sentences beats a one-line subtitle for spoken summaries.
        summary = _clean_html(entry.get("summary", ""))
        content_list = entry.get("content") or []
        full = _clean_html(content_list[0].get("value", "")) if content_list else ""
        body = strip_boilerplate(full if len(full) > len(summary) else summary)
        stories.append(
            Story(
                author=source["name"],
                outlet=source["outlet"],
                title=_clean_html(entry.get("title", "Untitled")),
                text=body[:1200],
                link=entry.get("link", source["url"]),
                published=published,
                kind="article",
            )
        )
    return stories


_FIRST_PERSON_RE = re.compile(r"\b(i|i'm|i've|i'll|my|me|mine|we're)\b", re.IGNORECASE)
_MIN_POST_CHARS = 180  # standalone posts must be substantive, not one-line snark


def fetch_bluesky(source: dict, limit: int) -> list[Story]:
    """Keep only posts with reporting value.

    - Posts sharing an external article ("shared"): the linked piece's title and
      description become the story - this is journalists distributing actual work.
    - Standalone posts ("post") survive only if they are long, non-personal
      commentary. Photo captions, life updates, and quips are dropped here.
    """
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
        text = clean_for_speech((record.get("text") or "").strip())
        embed = record.get("embed") or {}
        external = embed.get("external") or {}
        ext_title = clean_for_speech(_clean_html(external.get("title") or ""))
        ext_desc = clean_for_speech(_clean_html(external.get("description") or ""))

        if ext_title:
            kind = "shared"
            title = ext_title
            body = strip_boilerplate(ext_desc if len(ext_desc) >= 40 else text)
        elif len(text) >= _MIN_POST_CHARS and not _FIRST_PERSON_RE.search(text):
            kind = "post"
            title = text[:110] + ("..." if len(text) > 110 else "")
            body = text
        else:
            continue  # caption, quip, or personal post - no reporting value

        rkey = post.get("uri", "").rsplit("/", 1)[-1]
        stories.append(
            Story(
                author=source["name"],
                outlet=source["outlet"],
                title=title,
                text=body[:900],
                link=f"https://bsky.app/profile/{source['handle']}/post/{rkey}",
                published=_parse_iso(record.get("createdAt")),
                kind=kind,
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
        filtered = [s for s in recent if not _is_noise(s)]
        filtered.sort(key=lambda s: s.published, reverse=True)
        kept = 0
        for story in filtered:
            if kept >= cfg.max_items_per_source:
                break
            key = (story.author, story.title.lower()[:80])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_stories.append(story)
            kept += 1
        noise = len(recent) - len(filtered)
        log.append(
            f"OK    {label}: {len(fetched)} fetched, {kept} kept"
            + (f", {noise} promo/noise dropped" if noise else "")
        )

    all_stories.sort(key=lambda s: s.published, reverse=True)
    return all_stories[: cfg.max_stories], log
