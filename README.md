# True News Podcast

Turns recent reporting from independent and investigative journalists — pulled from
their social media (Bluesky) and newsletters (RSS) — into a two-host conversational
podcast episode, published to a GitHub Pages site with a subscribable RSS feed.

## How it works

```
sources.yaml -> fetch (Bluesky + RSS) -> two-host script -> MP3 (edge-tts) -> docs/ site
```

1. **Fetch** — every source in [sources.yaml](sources.yaml) is polled. Bluesky posts come
   from the public AppView API (no account or key needed); newsletters come from their
   RSS feeds. Items outside the lookback window are dropped, reposts and duplicates are
   skipped, and each feed is capped so no single voice dominates.
2. **Script** — the stories become a two-host conversation (NotebookLM-style). If Claude
   API credentials are available (`ANTHROPIC_API_KEY` or an `ant auth login` profile),
   Claude writes a curated, natural back-and-forth. Without credentials a template
   writer produces an alternating readout instead.
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

## One-time GitHub Pages setup

1. Create a **public** repository on GitHub (e.g. `true-news-podcast`).
2. From this folder:
   ```powershell
   git remote add origin https://github.com/YOURUSER/true-news-podcast.git
   git push -u origin main
   ```
3. On GitHub: **Settings -> Pages -> Source: Deploy from a branch -> Branch: `main`,
   folder: `/docs`** -> Save.
4. Your site appears at `https://YOURUSER.github.io/true-news-podcast/` within a minute
   or two.
5. Put that URL into `site_url` in `sources.yaml`, run `python -m truenews` once more,
   and push — this makes the RSS feed (`feed.xml`) subscribable in podcast apps.

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

## Better episodes with Claude

The template writer reads items verbatim, so promo posts and off-topic items get
through. With Claude API credentials set, the script writer instead curates: it groups
related stories, drops noise, orders by weight, and writes a natural conversation —
while keeping strict per-journalist attribution and a no-editorializing rule
(see `SYSTEM_PROMPT` in [script_writer.py](truenews/script_writer.py)).

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # per session, or set it in Windows env vars
```
