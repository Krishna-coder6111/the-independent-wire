"""Render dialogue to MP3 using Microsoft Edge neural voices (free, no key).

Each dialogue turn is synthesized with its host's voice; the resulting MP3
segments are concatenated. Raw MPEG frame streams concatenate cleanly, so no
ffmpeg is needed.
"""
import asyncio

import edge_tts

_CONCURRENCY = 6

# A spread of voices worth auditioning; run `python -m truenews --sample-voices`
SAMPLE_VOICES = [
    "en-US-AndrewMultilingualNeural",
    "en-US-BrianMultilingualNeural",
    "en-US-GuyNeural",
    "en-US-ChristopherNeural",
    "en-US-EmmaMultilingualNeural",
    "en-US-AvaMultilingualNeural",
    "en-US-JennyNeural",
    "en-US-MichelleNeural",
    "en-GB-RyanNeural",
    "en-GB-SoniaNeural",
    "en-AU-NatashaNeural",
    "en-IN-NeerjaNeural",
    "en-IN-PrabhatNeural",
]


async def _synth_segment(text: str, voice: str, sem: asyncio.Semaphore) -> bytes:
    async with sem:
        communicate = edge_tts.Communicate(text, voice)
        buf = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)


async def _synth_dialogue(turns, voices: dict, out_path: str) -> None:
    sem = asyncio.Semaphore(_CONCURRENCY)
    # Merge consecutive turns by the same speaker into one synthesis call
    merged: list[tuple[str, str]] = []
    for speaker, text in turns:
        if merged and merged[-1][0] == speaker:
            merged[-1] = (speaker, f"{merged[-1][1]} {text}")
        else:
            merged.append((speaker, text))
    segments = await asyncio.gather(
        *(_synth_segment(text, voices[speaker], sem) for speaker, text in merged)
    )
    with open(out_path, "wb") as f:
        for segment in segments:
            f.write(segment)


def dialogue_to_mp3(turns, cfg, out_path: str) -> None:
    voices = {"A": cfg.host_a_voice, "B": cfg.host_b_voice}
    asyncio.run(_synth_dialogue(turns, voices, out_path))


def make_voice_samples(out_dir, sample_text: str | None = None) -> list[str]:
    """Synthesize a short sample line per candidate voice; returns file paths."""
    text = sample_text or (
        "This is a sample for the Independent Wire. Judd Legum reports in Popular "
        "Information that the committee released its findings late on Tuesday."
    )

    async def _run():
        sem = asyncio.Semaphore(_CONCURRENCY)
        paths = []
        results = await asyncio.gather(
            *(_synth_segment(text, v, sem) for v in SAMPLE_VOICES), return_exceptions=True
        )
        for voice, data in zip(SAMPLE_VOICES, results):
            if isinstance(data, Exception):
                print(f"      FAIL {voice}: {data}")
                continue
            path = out_dir / f"{voice}.mp3"
            path.write_bytes(data)
            paths.append(str(path))
        return paths

    return asyncio.run(_run())
