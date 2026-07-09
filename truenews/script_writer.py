"""Turn the collected stories into a two-host conversational podcast script.

The script format is one dialogue turn per line, tagged `A:` or `B:`.
Uses the Claude API when credentials are available (ANTHROPIC_API_KEY or an
`ant auth login` profile); otherwise falls back to a deterministic template
so the pipeline always produces an episode.
"""
import itertools
import json
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

# Canned co-host reactions for the no-API template writer. These are spoken by
# the host who presents the NEXT story, so they must not address the other host.
_HANDOFFS = [
    "Interesting. Here's my next one.",
    "Worth keeping an eye on. Moving along.",
    "Noted. Okay, this one's mine.",
    "That one's worth a full read. Next up.",
    "Good to know. Here's what I've got next.",
]


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
            # continuation of the previous turn
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


def write_script_template(stories, cfg, episode_date: datetime) -> str:
    """No-API fallback: alternating hosts read the items out with attribution."""
    date_str = episode_date.strftime("%A, %B %d, %Y")
    a, b = cfg.host_a_name, cfg.host_b_name
    lines = [
        f"A: Welcome to {cfg.podcast_name} for {date_str}. I'm {a}.",
        f"B: And I'm {b}. Today we're rounding up {len(stories)} recent items from "
        f"independent and investigative journalists. Every story is attributed to the "
        f"journalist who reported it. Let's get into it.",
    ]
    handoffs = itertools.cycle(_HANDOFFS)
    speakers = itertools.cycle(["A", "B"])
    for story in stories:
        speaker = next(speakers)
        other = "B" if speaker == "A" else "A"
        when = story.published.strftime("%B %d")
        if story.outlet.lower() == "bluesky":
            intro = f"Posting on Bluesky on {when}, {story.author} wrote:"
        else:
            intro = f"From {story.author} at {story.outlet}, published {when}: {story.title}."
        body = story.text if story.text else story.title
        lines.append(f"{speaker}: {intro} {body}")
        lines.append(f"{other}: {next(handoffs)}")
    lines.pop()  # drop the trailing handoff after the last story
    lines.append(
        f"A: And that's everything for this edition of {cfg.podcast_name}. Everything "
        f"you heard comes directly from the cited journalists' own feeds and newsletters."
    )
    lines.append(f"B: Links to every story are in the show notes. Thanks for listening.")
    return "\n".join(lines)


def write_script(stories, cfg, episode_date: datetime) -> tuple[list[tuple[str, str]], str]:
    """Returns (dialogue turns, writer_used)."""
    try:
        raw = write_script_with_claude(stories, cfg, episode_date)
        writer = "claude"
    except Exception as exc:
        print(f"  Claude script writer unavailable ({type(exc).__name__}: {exc})")
        print("  Falling back to the template writer.")
        raw = write_script_template(stories, cfg, episode_date)
        writer = "template"
    turns = parse_dialogue(raw)
    if not turns:  # a writer produced untagged text; read it all as host A
        turns = [("A", raw)]
    return turns, writer
