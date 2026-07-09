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


def write_script_builtin(stories, cfg, episode_date: datetime) -> str:
    """The free writer: features from newsletters first, quick hits from social after."""
    date_str = episode_date.strftime("%A, %B %d, %Y")
    a, b = cfg.host_a_name, cfg.host_b_name
    features = [s for s in stories if s.outlet.lower() != "bluesky"]
    quick_hits = [s for s in stories if s.outlet.lower() == "bluesky"]

    teaser_bits = []
    if features:
        outlets = sorted({s.outlet for s in features})
        teaser_bits.append(
            f"{len(features)} new pieces from outlets like {', '.join(outlets[:3])}"
        )
    if quick_hits:
        teaser_bits.append(f"{len(quick_hits)} quick hits from journalists on social media")

    lines = [
        f"A: Welcome to {cfg.podcast_name} for {date_str}. I'm {a}.",
        f"B: And I'm {b}. On today's episode: {' and '.join(teaser_bits)}. "
        f"As always, every story is credited to the journalist who reported it. "
        f"Let's get into it.",
    ]

    handoffs = itertools.cycle(_HANDOFFS)
    speakers = itertools.cycle(["A", "B"])
    for i, story in enumerate(features):
        speaker = next(speakers)
        when = story.published.strftime("%B %d")
        lead = "" if i == 0 else f"{next(handoffs)} "
        body = _trim_sentences(story.text) if story.text else ""
        headline = (
            f"{lead}From {story.author} at {story.outlet}, published {when}: "
            f"{story.title}."
        )
        lines.append(f"{speaker}: {headline} {body}".rstrip())

    if quick_hits:
        speaker = next(speakers)
        lines.append(
            f"{speaker}: Now for some quick hits. These are recent posts from "
            f"independent journalists on Bluesky, in their own words."
        )
        for story in quick_hits:
            speaker = next(speakers)
            body = _trim_sentences(story.text, max_chars=320)
            lines.append(f"{speaker}: {story.author} wrote: {body}")

    lines.append(
        f"A: And that's everything for this edition of {cfg.podcast_name}. Everything "
        f"you heard comes directly from the cited journalists' own feeds and newsletters."
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
