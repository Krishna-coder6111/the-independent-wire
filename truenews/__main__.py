"""CLI entry point: fetch -> two-host script -> synthesize audio -> publish site.

Usage:
    python -m truenews                  # full run: script + MP3 + docs/ site refresh
    python -m truenews --no-audio      # script only
    python -m truenews --hours 72      # widen the lookback window
    python -m truenews --sample-voices # audition candidate TTS voices, then exit
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .fetch import collect_stories
from .models import Config
from .publish import publish_episode
from .script_writer import render_readable, write_script
from .tts import dialogue_to_mp3, make_voice_samples

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path) -> Config:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Config(**{k: v for k, v in raw.items() if k in Config.__dataclass_fields__})


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="truenews", description="Independent-journalism podcast builder")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "sources.yaml"))
    parser.add_argument("--hours", type=int, help="override window_hours from the config")
    parser.add_argument("--no-audio", action="store_true", help="skip TTS, produce the script only")
    parser.add_argument("--no-publish", action="store_true", help="skip refreshing the docs/ site")
    parser.add_argument("--voice-a", help="override host A's TTS voice")
    parser.add_argument("--voice-b", help="override host B's TTS voice")
    parser.add_argument(
        "--sample-voices", action="store_true",
        help="synthesize a sample line for each candidate voice into output/voice-samples/ and exit",
    )
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    if args.hours:
        cfg.window_hours = args.hours
    if args.voice_a:
        cfg.host_a_voice = args.voice_a
    if args.voice_b:
        cfg.host_b_voice = args.voice_b

    if args.sample_voices:
        sample_dir = PROJECT_ROOT / "output" / "voice-samples"
        sample_dir.mkdir(parents=True, exist_ok=True)
        print(f"Writing voice samples to {sample_dir} ...")
        paths = make_voice_samples(sample_dir)
        for p in paths:
            print(f"  {p}")
        print("Listen to these, then set host_a_voice / host_b_voice in sources.yaml.")
        return 0

    episode_date = datetime.now(timezone.utc)
    out_dir = PROJECT_ROOT / "output" / episode_date.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] Fetching feeds (last {cfg.window_hours}h) ...")
    stories, log = collect_stories(cfg)
    for line in log:
        print(f"      {line}")
    if not stories:
        print("No stories found in the window. Try --hours 72 or check sources.yaml.")
        return 1
    print(f"      -> {len(stories)} stories selected")

    (out_dir / "stories.json").write_text(
        json.dumps([s.to_dict() for s in stories], indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("[2/4] Writing the episode script ...")
    turns, writer = write_script(stories, cfg, episode_date)
    script_path = out_dir / "script.txt"
    script_path.write_text(render_readable(turns, cfg), encoding="utf-8")
    words = sum(len(t.split()) for _, t in turns)
    print(f"      -> {script_path} ({writer} writer, {len(turns)} turns, {words} words)")

    notes = [f"# {cfg.podcast_name} - {episode_date.strftime('%Y-%m-%d')}", ""]
    notes += [f"- {s.author} ({s.outlet}): [{s.title}]({s.link})" for s in stories]
    (out_dir / "episode-notes.md").write_text("\n".join(notes), encoding="utf-8")

    if args.no_audio:
        print("[3/4] Skipping audio (--no-audio).")
        return 0

    print(f"[3/4] Synthesizing dialogue ({cfg.host_a_name}={cfg.host_a_voice}, "
          f"{cfg.host_b_name}={cfg.host_b_voice}) ...")
    mp3_path = out_dir / "episode.mp3"
    dialogue_to_mp3(turns, cfg, str(mp3_path))
    size_mb = mp3_path.stat().st_size / 1_048_576
    print(f"      -> {mp3_path} ({size_mb:.1f} MB)")

    if args.no_publish:
        print("[4/4] Skipping site refresh (--no-publish).")
        return 0

    print("[4/4] Refreshing the docs/ site ...")
    docs = publish_episode(cfg, episode_date, out_dir, stories, PROJECT_ROOT)
    print(f"      -> {docs / 'index.html'}")
    print(f"      -> {docs / 'feed.xml'}")
    if not cfg.site_url:
        print("      NOTE: site_url is empty in sources.yaml - the RSS feed will not be")
        print("      subscribable in podcast apps until you set it to your Pages URL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
