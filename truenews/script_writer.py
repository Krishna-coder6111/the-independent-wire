"""Turn the collected stories into a two-host conversational podcast script.

The script format is one dialogue turn per line, tagged `A:` or `B:`.

The default writer is free and fully local. If ANTHROPIC_API_KEY (or
ANTHROPIC_AUTH_TOKEN) is set, an optional Claude-written script is used
instead - but nothing here ever requires a paid API.
"""
import itertools
import json
import os
import re
from datetime import datetime

SYSTEM_PROMPT = """You are the head writer for a two-host daily news podcast that curates
reporting from independent and investigative journalists. You will receive a JSON object
with the podcast name, the episode date, the two hosts' names, and a list of recent items,
each with the journalist's name, their outlet or platform, a title, an excerpt, and a
timestamp.

Write the full episode as a natural conversation between host A and host B, in the style
of two smart colleagues walking each other through the day's reporting.

Output format - strict:
- One dialogue turn per line. Every line must start with exactly "A: " or "B: ".
- Plain spoken prose only. It goes straight to text-to-speech: no markdown, no stage
  directions, no bracketed cues, no emoji, no URLs, nothing except the two speaker tags.

Content rules:
- Attribute every story explicitly to the journalist who reported it, e.g. "Judd Legum
  reports in Popular Information that..." or "Marisa Kabas posted on Bluesky that...".
  Never present a claim as established fact when it is one journalist's reporting.
- Curate: skip subscription drives, self-promotion, and trivial personal posts. Group
  related items into one segment. Cover the weightiest stories first and give them the
  most airtime.
- The hosts trade off who leads each story; the other reacts, asks a brief clarifying
  question, or adds context from another provided item. Reactions must stay grounded in
  the provided material - no speculation, no editorializing, no outside facts.
- Open with both hosts greeting listeners, naming the podcast and the date, and teasing
  what's coming up. Close with a short outro reminding listeners that everything covered
  comes from the cited journalists and that links are in the show notes.
- Target roughly 1,300 to 1,800 words - a natural 9 to 13 minute listen.
"""

_TURN_RE = re.compile(r"^([AB])\s*:\s*(.*)$")
_SENT_RE = re.compile(r"(?<=[.!?])\s+")

# Spoken by the host who presents the NEXT feature, so they must not
# address the other host.
_HANDOFFS = [
    "Interesting. Here's my next one.",
    "Worth keeping an eye on. Moving along.",
    "Noted. Okay, this one's mine.",
    "That one's worth a full read. Next up.",
    "Good to know. Here's what I've got next.",
]


def _claude_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _trim_sentences(text: str, max_chars: int = 380) -> str:
    """Cut an excerpt at a sentence boundary instead of mid-thought."""
    out = ""
    for sentence in _SENT_RE.split(text):
        if not sentence:
            continue
        if out and len(out) + len(sentence) + 1 > max_chars:
            break
        out = f"{out} {sentence}".strip()
    return out or text[:max_chars].rsplit(" ", 1)[0]


def parse_dialogue(script: str) -> list[tuple[str, str]]:
    """Parse the tagged script into ordered (speaker, text) turns."""
    turns: list[tuple[str, str]] = []
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _TURN_RE.match(line)
        if m:
            turns.append((m.group(1), m.group(2).strip()))
        elif turns:
            speaker, text = turns[-1]
            turns[-1] = (speaker, f"{text} {line}")
    return [(s, t) for s, t in turns if t]


def render_readable(turns: list[tuple[str, str]], cfg) -> str:
    names = {"A": cfg.host_a_name, "B": cfg.host_b_name}
    return "\n\n".join(f"{names[s]}: {t}" for s, t in turns)


def write_script_with_claude(stories, cfg, episode_date: datetime) -> str:
    import anthropic

    client = anthropic.Anthropic()
    payload = {
        "podcast_name": cfg.podcast_name,
        "episode_date": episode_date.strftime("%A, %B %d, %Y"),
        "host_a_name": cfg.host_a_name,
        "host_b_name": cfg.host_b_name,
        "items": [s.to_dict() for s in stories],
    }
    with client.messages.stream(
        model="claude-opus-4-8",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    ) as stream:
        message = stream.get_final_message()
    return next(b.text for b in message.content if b.type == "text").strip()


_CAP_STOP = {
    # function words / time words that start sentences
    "There", "These", "Those", "Their", "Where", "Which", "While", "About", "After",
    "Before", "Because", "Every", "First", "Today", "Tonight", "Yesterday", "Monday",
    "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "January",
    "February", "March", "April", "August", "September", "October", "November",
    "December", "Should", "Would", "Could", "Inside", "Against", "Between", "Under",
    # news-generic words that appear in unrelated stories and cause false links
    "President", "Donald", "Trump", "White", "House", "America", "American",
    "Americans", "United", "States", "Washington", "Congress", "Senate", "Senator",
    "Democratic", "Democrats", "Democrat", "Republican", "Republicans", "Justice",
    "Government", "Federal", "National", "State", "Country", "People", "Getty",
    "Images", "Photo", "Video", "Editor", "Media", "Press", "Politico", "Times",
    "Post", "Bluesky", "Substack", "Twitter", "Podcast", "Newsletter", "Breaking",
}


def _story_keywords(story) -> set:
    """Distinctive proper nouns used to link related stories together.

    Only the title and the very opening of the excerpt count - deep-in-the-text
    mentions are too weak a signal and cause false "same story" links.
    """
    caps = re.findall(r"\b[A-Z][a-z]{4,}\b", f"{story.title} {story.text[:100]}")
    return {w for w in caps if w not in _CAP_STOP}


def _group_stories(stories) -> tuple[list, list, list]:
    """Cluster stories on shared rare proper nouns.

    Returns (segments, leftover_shared, leftover_posts) where each segment is
    (lead_story, [related stories]). A linking word must appear in 2-3 stories -
    common enough to connect, rare enough not to lump the whole episode together.
    """
    from collections import Counter

    keywords = {id(s): _story_keywords(s) for s in stories}
    title_keywords = {
        id(s): {w for w in re.findall(r"\b[A-Z][a-z]{4,}\b", s.title) if w not in _CAP_STOP}
        for s in stories
    }
    df = Counter(w for ks in keywords.values() for w in ks)
    link_words = {w for w, c in df.items() if 2 <= c <= 3}
    for sid in keywords:
        keywords[sid] &= link_words
        title_keywords[sid] &= link_words

    articles = [s for s in stories if s.kind == "article"]
    shared = [s for s in stories if s.kind == "shared"]
    posts = [s for s in stories if s.kind == "post"]
    leads = articles if articles else shared

    used, segments = set(), []
    for lead in leads:
        if id(lead) in used:
            continue
        used.add(id(lead))
        # A link needs the shared topic word in at least one of the two TITLES -
        # a passing mention deep in a digest post is not "the same story".
        # Each related entry is (story, headline_match): headline_match is False
        # when the topic only appears in the piece's body (e.g. a daily digest).
        related = []
        for s in stories:
            if id(s) in used or len(related) >= 3:
                continue
            if keywords[id(lead)] & title_keywords[id(s)]:
                related.append((s, True))
            elif title_keywords[id(lead)] & keywords[id(s)]:
                related.append((s, False))
        for r, _ in related:
            used.add(id(r))
        segments.append((lead, related))

    leftover_shared = [s for s in shared if id(s) not in used]
    leftover_posts = [s for s in posts if id(s) not in used][:3]
    return segments, leftover_shared, leftover_posts


def _related_line(story, lead=None, headline_match=True) -> str:
    if lead is not None and story.author == lead.author:
        return f"There's also a companion piece from {story.author}, titled {story.title}."
    if story.kind == "article":
        if headline_match:
            return (
                f"{story.author} at {story.outlet} is on the same story, with a piece "
                f"titled {story.title}."
            )
        return f"{story.author} at {story.outlet} also touches on this in their latest edition."
    if story.kind == "shared":
        return (
            f"{story.author} pointed to this on Bluesky too, sharing a piece "
            f"titled {story.title}."
        )
    return f"And {story.author} weighed in on Bluesky: {_trim_sentences(story.text, 260)}"


def write_script_builtin(stories, cfg, episode_date: datetime) -> str:
    """The free writer: topic-grouped feature segments, then journalists' shared
    pieces, then a few substantive quick takes."""
    date_str = episode_date.strftime("%A, %B %d, %Y")
    a, b = cfg.host_a_name, cfg.host_b_name
    segments, leftover_shared, leftover_posts = _group_stories(stories)

    outlets = sorted({lead.outlet for lead, _ in segments})
    teaser = f"{len(segments)} stories from outlets like {', '.join(outlets[:3])}"
    if leftover_shared or leftover_posts:
        teaser += ", plus what independent journalists are highlighting on social media"

    lines = [
        f"A: Welcome to {cfg.podcast_name} for {date_str}. I'm {a}.",
        f"B: And I'm {b}. On today's episode: {teaser}. As always, every story is "
        f"credited to the journalist who reported it. Let's get into it.",
    ]

    handoffs = itertools.cycle(_HANDOFFS)
    speakers = itertools.cycle(["A", "B"])
    for i, (lead, related) in enumerate(segments):
        speaker = next(speakers)
        other = "B" if speaker == "A" else "A"
        when = lead.published.strftime("%B %d")
        opener = "" if i == 0 else f"{next(handoffs)} "
        body = _trim_sentences(lead.text) if lead.text else ""
        src = f"From {lead.author} at {lead.outlet}, published {when}" \
            if lead.kind == "article" else f"Shared by {lead.author} on Bluesky, {when}"
        lines.append(f"{speaker}: {opener}{src}: {lead.title}. {body}".rstrip())
        for r, headline_match in related:
            lines.append(f"{other}: {_related_line(r, lead, headline_match)}")

    if leftover_shared:
        speaker = next(speakers)
        lines.append(
            f"{speaker}: Journalists were also sharing new work on social media."
        )
        for story in leftover_shared:
            speaker = next(speakers)
            body = _trim_sentences(story.text, max_chars=280)
            lines.append(f"{speaker}: {story.author} shared: {story.title}. {body}".rstrip())

    if leftover_posts:
        speaker = next(speakers)
        lines.append(f"{speaker}: A few quick takes before we go.")
        for story in leftover_posts:
            speaker = next(speakers)
            lines.append(
                f"{speaker}: In a post on Bluesky, {story.author} wrote: "
                f"{_trim_sentences(story.text, 300)}"
            )

    lines.append(
        f"A: And that's everything for this edition of {cfg.podcast_name}. Everything "
        f"you heard comes directly from the cited journalists' own reporting."
    )
    lines.append(
        f"B: Links to every story are in the show notes. Support the journalists whose "
        f"work you heard today. Thanks for listening."
    )
    return "\n".join(lines)


def write_script(stories, cfg, episode_date: datetime) -> tuple[list[tuple[str, str]], str]:
    """Returns (dialogue turns, writer_used)."""
    raw, writer = None, "builtin (free)"
    if _claude_available():
        try:
            raw = write_script_with_claude(stories, cfg, episode_date)
            writer = "claude"
        except Exception as exc:
            print(f"  Claude writer failed ({type(exc).__name__}: {exc}); using the free writer.")
    if raw is None:
        raw = write_script_builtin(stories, cfg, episode_date)
    turns = parse_dialogue(raw)
    if not turns:  # a writer produced untagged text; read it all as host A
        turns = [("A", raw)]
    return turns, writer
