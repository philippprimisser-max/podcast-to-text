# podcast-to-text

Transcribe a podcast episode on your own computer. Paste an RSS feed, an Apple Podcasts link, a direct audio URL or a local file, and get `.txt`, `.srt`, `.vtt` and `.json` back. Free, no API key, no account. Uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on the CPU.

```bash
python podcast_to_text.py "https://podcasts.apple.com/us/podcast/hacker-public-radio/id281699640" --search "wl-copy"
```
```
Apple Podcasts link -> RSS feed: https://hackerpublicradio.org/hpr_rss.php
Episode: HPR4743: wl-copy (2026-10-07)
Transcribing with faster-whisper 'base' (4 threads) ...
Done: 15.7 min audio in 18 s (0.02 s per audio second), language en.
transcripts/2026-10-07-hpr4743-wl-copy.txt
transcripts/2026-10-07-hpr4743-wl-copy.srt
transcripts/2026-10-07-hpr4743-wl-copy.vtt
transcripts/2026-10-07-hpr4743-wl-copy.json
```

## What it does

1. **Finds the episode.** RSS feeds are read directly. For Apple Podcasts links it asks Apple's public lookup API for the show's own RSS feed; the audio always comes from the publisher, never from Apple.
2. **Uses the publisher's transcript if there is one.** Some feeds already ship a transcript via the Podcasting 2.0 `<podcast:transcript>` tag. Downloading it is instant and usually better than a machine transcript. Pass `--force` to transcribe anyway. (How common is that? In Apple's top charts for US, UK, DE and AT on 8 October 2026, 18 of 109 feeds had a transcript on the newest episode; script and data in [`research/`](research/).)
3. **Otherwise transcribes locally** with faster-whisper (int8, voice activity detection on) and writes four formats:
   - `.txt` plain text, new paragraph after pauses longer than 2 seconds
   - `.srt` / `.vtt` subtitles with timestamps
   - `.json` segments plus metadata (model, detected language, durations)

## Install

Python 3.10–3.13. ffmpeg is **not** needed (faster-whisper decodes audio with PyAV).

```bash
git clone https://github.com/philippprimisser-max/podcast-to-text
cd podcast-to-text
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

The first run downloads the speech model once (base ≈ 150 MB, small ≈ 490 MB).

> **Note on PyAV:** `requirements.txt` pins `av<19`. With a fresh install in October 2026, PyAV 19 broke faster-whisper 1.2.1 (`open() got an unexpected keyword argument 'metadata_errors'`).

## Usage

```bash
# newest episode of a feed
python podcast_to_text.py https://hackerpublicradio.org/hpr_mp3_rss.php

# see what's in the feed (and which episodes already have a transcript)
python podcast_to_text.py https://hackerpublicradio.org/hpr_mp3_rss.php --list

# second-newest episode, German, more accurate model
python podcast_to_text.py https://example.com/feed.xml -e 2 --language de -m small

# episode link from Apple Podcasts (the ?i= part selects the episode)
python podcast_to_text.py "https://podcasts.apple.com/at/podcast/.../id123456789?i=1000123456789"

# local file or direct audio URL
python podcast_to_text.py interview.mp3 -f txt,srt
```

| Option | Default | |
|---|---|---|
| `-e, --episode N` | 1 | 1 = newest, 2 = the one before, … |
| `-s, --search WORDS` | | newest episode whose title contains all words |
| `-l, --list` | | list the latest 30 episodes and exit |
| `-m, --model` | base | `tiny`, `base`, `small`, `medium`, `large-v3`, … |
| `--language` | auto | `en`, `de`, … Setting it avoids wrong auto-detection on intros/music |
| `-o, --out` | `transcripts/` | output folder |
| `-f, --formats` | `txt,srt,vtt,json` | any subset |
| `--threads` | min(4, cores) | see the note below |
| `--compute-type` | int8 | `float32` is slower but uses more precise math |
| `--beam-size` | 1 | 5 is slower and sometimes a bit more accurate |
| `--force` | | transcribe even if the feed has a transcript |
| `--keep-audio` | | keep the downloaded audio next to the transcript |

## Speed (measured, not promised)

Measured on an 8-core x86-64 Linux server (`os.cpu_count()` = 8), default 4 threads, int8, model already downloaded. The time is what the script prints itself (transcription only, without model loading and download):

| Audio | Command | Time |
|---|---|---|
| 15.7 min English podcast (HPR4743) | the example at the top (`base`) | 18 s |
| 1 min Gettysburg Address (test fixture) | `python podcast_to_text.py tests/fixtures/gettysburg.mp3 -m base` | 1 s |
| 1 min Gettysburg Address (test fixture) | `python podcast_to_text.py tests/fixtures/gettysburg.mp3 -m small --language en` | 9–10 s |

Your laptop will be slower or faster; try `tiny` first if you just want to see it work. Set `--language` when you know it: in one run without it, `small` detected the English test clip as Russian and wrote the transcript in Russian.

## Why the thread count is capped at 4

On one 8-core machine, faster-whisper with `compute_type="int8"` and 8 CPU threads silently skipped whole passages of speech (up to 60 s in a one-minute clip) in 13 of 15 test runs. With 4 threads it happened in 1 of 15 runs, at about 4 s instead of 2.4 s per one-minute clip. So the default is `min(4, cores)`. If you want to be safer, use `--compute-type float32` (0 of 15 runs with gaps in the same test) or check your transcript with [faster-whisper-gap-check](https://github.com/philippprimisser-max/faster-whisper-gap-check), which also has the data and the script to test your own machine.

## Limits

- No speaker labels (diarization).
- Only public feeds. Private/subscriber feeds work only if you have a personal feed URL that you're allowed to use.
- Some hosts block unknown user agents. The script identifies itself honestly as `podcast-to-text`; it doesn't pretend to be a browser.
- Machine transcripts contain mistakes, especially for names and jargon. Read before you publish.

## Tests

```bash
pip install pytest
python -m pytest -q            # includes one real transcription with the tiny model (a few seconds once the model is downloaded)
SKIP_SLOW=1 python -m pytest -q
```

The test audio is the [LibriVox](https://librivox.org) recording of the Gettysburg Address (public domain).

## Related

- [Podcast Transcriber](https://apify.com/prime619/podcast-transcriber) on Apify: a hosted, paid version (pay per audio minute) with scheduling and "only new episodes". Disclosure: I built it. This repository is free and complete on its own.

## License

Code written with AI assistance and tested with the commands above.

MIT, see [LICENSE](LICENSE). Not affiliated with Apple, OpenAI or SYSTRAN. "Apple Podcasts" is a trademark of Apple Inc.; it's mentioned only to describe which links the script understands.
