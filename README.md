# The Independent Wire

Turns recent reporting from independent and investigative journalists — pulled from
their social media (Bluesky) and newsletters (RSS) — into a two-host conversational
podcast episode, published to a GitHub Pages site with a subscribable RSS feed.
**100% free: no API keys, no subscriptions, no ffmpeg.**

- **Listen:** https://krishna-coder6111.github.io/the-independent-wire/
- **Subscribe in a podcast app:** https://krishna-coder6111.github.io/the-independent-wire/feed.xml

A new episode publishes itself every morning via GitHub Actions
([daily-episode.yml](.github/workflows/daily-episode.yml), 11:30 UTC).

## Pausing or stopping the daily episode

- **Pause:** on GitHub go to **Actions -> Daily episode -> "..." menu -> Disable
  workflow**. Re-enable the same way whenever you want it back.
- **Stop permanently:** delete `.github/workflows/daily-episode.yml` and push.
- Episodes already published stay on the site either way; delete folders under
  `docs/episodes/` and push to take them down.

## How it works

```
sources.yaml -> fetch (Bluesky + RSS) -> two-host script -> MP3 (edge-tts) -> docs/ site
```

1. **Fetch** — every source in [sources.yaml](sources.yaml) is polled. Bluesky posts come
   from the public AppView API (no account or key needed); newsletters come from their
   RSS feeds. Items outside the lookback window are dropped, reposts and duplicates are
   skipped, and each feed is capped so no single voice dominates.
2. **Script** — the stories become a two-host conversation (NotebookLM-style). The free
   built-in writer curates deterministically: social posts only qualify if they share an
   actual article or are substantive non-personal commentary (captions, quips, promo,
   and life updates are dropped); article excerpts come from the piece's opening
   paragraphs with photo credits and subscribe buttons stripped; and related items are
   grouped into segments, with the co-host adding "X is on the same story" cross-
   references. (Optionally, setting `ANTHROPIC_API_KEY` swaps in an AI-written script —
   never required.)
3. **Audio** — each host's turns are synthesized with their own Microsoft Edge neural
   voice via `edge-tts` (free, no key, no ffmpeg) and stitched into `episode.mp3`.
4. **Publish** — the episode is copied into `docs/`, and the site index + podcast RSS
   feed are regenerated. Push to GitHub and the episode is live on your Pages site.

Every claim in the episode is attributed on air to the journalist who reported it, and
each episode page links back to every original post/article.

## Usage

```powershell
pip install --user -r requirements.txt

python -m truenews                  # full run: script + MP3 + site refresh
python -m truenews --no-audio      # script only
python -m truenews --hours 72      # widen the lookback window
python -m truenews --sample-voices # audition TTS voices into output/voice-samples/
python -m truenews --voice-a en-GB-RyanNeural --voice-b en-GB-SoniaNeural
```

Local artifacts land in `output/YYYY-MM-DD/`; the publishable site lives in `docs/`.

After a run, publish with:

```powershell
git add docs
git commit -m "Episode YYYY-MM-DD"
git push
```

## Choosing voices

Run `python -m truenews --sample-voices`, listen to the MP3s in
`output/voice-samples/`, then set `host_a_voice` / `host_b_voice` in `sources.yaml`.
Any voice from `edge-tts --list-voices` works (hundreds of voices, ~40 English ones).

## Customizing sources

Edit `sources.yaml`. Each entry is either:

```yaml
- name: Journalist Name        # who gets credited on air
  outlet: Their Newsletter
  type: rss
  url: https://example.com/feed

- name: Journalist Name
  outlet: Bluesky
  type: bluesky
  handle: their-handle.bsky.social
```

Dead or unreachable feeds are logged and skipped — they never break a run.

## Optional: AI-written scripts

Everything works for free. If you ever want more natural banter (hosts reacting to each
other, grouping related stories across sources), setting `ANTHROPIC_API_KEY` switches
the script writer to Claude — but this is a paid API and entirely optional. Without it,
the free built-in writer is always used.
