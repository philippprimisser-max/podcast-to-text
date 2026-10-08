#!/usr/bin/env python3
"""podcast-to-text: turn a podcast episode into a transcript on your own computer.

Input:  an RSS feed URL, an Apple Podcasts show/episode link, a direct audio URL, or a local audio file.
Output: .txt, .srt, .vtt and .json next to each other in the output folder.

If the feed already publishes a transcript (Podcasting 2.0 <podcast:transcript> tag), that transcript is
downloaded instead of transcribing, unless you pass --force.

MIT License. https://github.com/philippprimisser-max/podcast-to-text
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path

__version__ = "0.1.0"
USER_AGENT = f"podcast-to-text/{__version__} (+https://github.com/philippprimisser-max/podcast-to-text)"
APPLE_LOOKUP = "https://itunes.apple.com/lookup"
PODCAST_NS = "https://podcastindex.org/namespace/1.0"
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
AUDIO_EXT = (".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".opus", ".wav", ".flac", ".webm")


class UserError(Exception):
    """Something the user can fix (bad link, no feed, ...)."""


# --------------------------------------------------------------------------- HTTP

def http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise UserError(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise UserError(f"Could not reach {url}: {exc.reason}") from exc


def download(url: str, dest: Path, timeout: int = 120) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done, last = 0, 0.0
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if sys.stderr.isatty() and time.time() - last > 0.5:
                    pct = f"{done * 100 // total}%" if total else f"{done >> 20} MB"
                    print(f"\r  downloading ... {pct}", end="", file=sys.stderr)
                    last = time.time()
    except urllib.error.HTTPError as exc:
        raise UserError(f"Audio download failed: HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise UserError(f"Audio download failed: {exc.reason}") from exc
    if sys.stderr.isatty():
        print("\r" + " " * 40 + "\r", end="", file=sys.stderr)
    return dest


# --------------------------------------------------------------------------- Apple Podcasts

def parse_apple_url(url: str) -> tuple[str, str | None] | None:
    """(show_id, episode_id or None) for a podcasts.apple.com link, else None."""
    u = urllib.parse.urlparse(url.strip())
    if (u.hostname or "").lower() not in ("podcasts.apple.com", "itunes.apple.com"):
        return None
    m = re.search(r"/id(\d{5,})", u.path)
    if not m:
        return None
    ep = (urllib.parse.parse_qs(u.query).get("i") or [None])[0]
    return m.group(1), (ep if ep and ep.isdigit() else None)


def apple_to_feed(url: str) -> tuple[str, str | None]:
    """Use Apple's public lookup API to find the show's own RSS feed (audio never comes from Apple)."""
    show_id, ep_id = parse_apple_url(url)  # type: ignore[misc]
    data = json.loads(http_get(f"{APPLE_LOOKUP}?id={show_id}&entity=podcast"))
    feed = next((r.get("feedUrl") for r in data.get("results", []) if r.get("feedUrl")), None)
    if not feed:
        raise UserError("Apple lists no public RSS feed for this show (Apple-exclusive or subscriber-only).")
    guid = None
    if ep_id:
        eps = json.loads(http_get(f"{APPLE_LOOKUP}?id={show_id}&entity=podcastEpisode&limit=300"))
        hit = next((r for r in eps.get("results", []) if str(r.get("trackId")) == ep_id), None)
        if not hit:
            raise UserError("Episode not among Apple's latest 300 for this show. Use the RSS feed and --search.")
        guid = hit.get("episodeGuid")
    return feed, guid


# --------------------------------------------------------------------------- RSS

@dataclass
class Episode:
    title: str
    audio_url: str
    guid: str = ""
    published: str = ""
    duration: str = ""
    transcripts: list[dict] = field(default_factory=list)  # [{url, type, language}]


def parse_feed(xml_bytes: bytes) -> tuple[str, list[Episode]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise UserError(f"That URL did not return a valid RSS feed ({exc}).") from exc
    channel = root.find("channel")
    if channel is None:
        raise UserError("That URL did not return an RSS feed (no <channel>).")
    show = (channel.findtext("title") or "podcast").strip()
    episodes = []
    for item in channel.findall("item"):
        enc = item.find("enclosure")
        if enc is None or not enc.get("url"):
            continue
        pub = (item.findtext("pubDate") or "").strip()
        try:
            pub = parsedate_to_datetime(pub).date().isoformat() if pub else ""
        except (TypeError, ValueError):
            pass
        transcripts = [
            {"url": t.get("url"), "type": (t.get("type") or "").lower(), "language": t.get("language") or ""}
            for t in item.findall(f"{{{PODCAST_NS}}}transcript") if t.get("url")
        ]
        episodes.append(Episode(
            title=(item.findtext("title") or "untitled").strip(),
            audio_url=enc.get("url").strip(),
            guid=(item.findtext("guid") or "").strip(),
            published=pub,
            duration=(item.findtext(f"{{{ITUNES_NS}}}duration") or "").strip(),
            transcripts=transcripts,
        ))
    if not episodes:
        raise UserError("The feed has no episodes with an audio enclosure.")
    return show, episodes


def pick_episode(episodes: list[Episode], index: int = 1, search: str | None = None,
                 guid: str | None = None) -> Episode:
    if guid:
        for ep in episodes:
            if ep.guid == guid:
                return ep
        raise UserError("Episode from the Apple link was not found in the RSS feed.")
    if search:
        words = search.lower().split()
        hits = [ep for ep in episodes if all(w in ep.title.lower() for w in words)]
        if not hits:
            raise UserError(f'No episode title contains all of: {search!r}. Try --list.')
        return hits[0]
    if not 1 <= index <= len(episodes):
        raise UserError(f"--episode must be between 1 and {len(episodes)}.")
    return episodes[index - 1]


TRANSCRIPT_PREFERENCE = ["application/json", "application/srt", "application/x-subrip", "text/vtt", "text/plain",
                         "text/html"]


def best_transcript(ep: Episode) -> dict | None:
    def rank(t):
        return TRANSCRIPT_PREFERENCE.index(t["type"]) if t["type"] in TRANSCRIPT_PREFERENCE else 99
    usable = sorted(ep.transcripts, key=rank)
    return usable[0] if usable else None


# --------------------------------------------------------------------------- output formats

def ts(seconds: float, sep: str = ",") -> str:
    ms = int(round(max(seconds, 0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(segments: list[dict]) -> str:
    return "\n".join(f"{i}\n{ts(s['start'])} --> {ts(s['end'])}\n{s['text'].strip()}\n"
                     for i, s in enumerate(segments, 1))


def to_vtt(segments: list[dict]) -> str:
    body = "\n".join(f"{ts(s['start'], '.')} --> {ts(s['end'], '.')}\n{s['text'].strip()}\n" for s in segments)
    return "WEBVTT\n\n" + body


def to_txt(segments: list[dict], paragraph_gap: float = 2.0) -> str:
    """Plain text; a pause longer than paragraph_gap seconds starts a new paragraph."""
    paras, cur, last_end = [], [], None
    for s in segments:
        if last_end is not None and s["start"] - last_end > paragraph_gap and cur:
            paras.append(" ".join(cur))
            cur = []
        cur.append(s["text"].strip())
        last_end = s["end"]
    if cur:
        paras.append(" ".join(cur))
    return "\n\n".join(paras) + "\n"


def slug(text: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return (s[:maxlen].rstrip("-")) or "episode"


# --------------------------------------------------------------------------- transcription

def default_threads() -> int:
    # int8 with 8 CTranslate2 threads silently dropped passages in 13/15 test runs, 4 threads in 1/15 (float32: 0/15).
    # 4 reduces the risk but is no guarantee; use --compute-type float32 or check with faster-whisper-gap-check.
    return max(1, min(4, os.cpu_count() or 1))


def transcribe(audio: Path, model_name: str, language: str | None, threads: int,
               compute_type: str = "int8", beam_size: int = 1) -> tuple[list[dict], dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise UserError("faster-whisper is not installed: pip install -r requirements.txt") from exc
    model = WhisperModel(model_name, device="cpu", compute_type=compute_type, cpu_threads=threads)
    seg_iter, info = model.transcribe(str(audio), language=language, beam_size=beam_size, vad_filter=True,
                                      condition_on_previous_text=False)
    segments = []
    t0 = time.time()
    for s in seg_iter:
        segments.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
        if sys.stderr.isatty() and info.duration:
            print(f"\r  transcribing ... {min(100, int(s.end * 100 / info.duration))}%", end="", file=sys.stderr)
    if sys.stderr.isatty():
        print("\r" + " " * 40 + "\r", end="", file=sys.stderr)
    meta = {"model": model_name, "language": info.language, "language_probability": round(info.language_probability, 3),
            "audio_seconds": round(info.duration, 1), "processing_seconds": round(time.time() - t0, 1),
            "cpu_threads": threads, "compute_type": compute_type}
    return segments, meta


def write_outputs(out_dir: Path, base: str, segments: list[dict], meta: dict, formats: list[str]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for fmt in formats:
        p = out_dir / f"{base}.{fmt}"
        if fmt == "txt":
            p.write_text(to_txt(segments), encoding="utf-8")
        elif fmt == "srt":
            p.write_text(to_srt(segments), encoding="utf-8")
        elif fmt == "vtt":
            p.write_text(to_vtt(segments), encoding="utf-8")
        elif fmt == "json":
            p.write_text(json.dumps({"meta": meta, "segments": segments}, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        written.append(p)
    return written


# --------------------------------------------------------------------------- main

def is_url(s: str) -> bool:
    return s.lower().startswith(("http://", "https://"))


def looks_like_audio_url(url: str) -> bool:
    return urllib.parse.urlparse(url).path.lower().endswith(AUDIO_EXT)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="podcast_to_text.py",
        description="Transcribe a podcast episode locally with faster-whisper (RSS feed, Apple Podcasts link, "
                    "audio URL or local file).")
    p.add_argument("source", help="RSS feed URL, Apple Podcasts link, direct audio URL, or local audio file")
    p.add_argument("-e", "--episode", type=int, default=1, help="1 = newest episode in the feed (default), 2 = the one before, ...")
    p.add_argument("-s", "--search", help="pick the newest episode whose title contains these words")
    p.add_argument("-l", "--list", action="store_true", help="list the latest episodes and exit")
    p.add_argument("-m", "--model", default="base", help="tiny, base, small, medium, large-v3, ... (default: base)")
    p.add_argument("--language", default=None, help="language code like en or de (default: auto-detect)")
    p.add_argument("-o", "--out", default="transcripts", help="output folder (default: ./transcripts)")
    p.add_argument("-f", "--formats", default="txt,srt,vtt,json", help="comma list of txt,srt,vtt,json")
    p.add_argument("--threads", type=int, default=default_threads(), help="CPU threads (default: min(4, cores))")
    p.add_argument("--compute-type", default="int8", help="int8 (default) or float32")
    p.add_argument("--beam-size", type=int, default=1, help="1 = fast greedy decoding (default), 5 = slower")
    p.add_argument("--force", action="store_true", help="transcribe even if the feed publishes a transcript")
    p.add_argument("--keep-audio", action="store_true", help="keep the downloaded audio file in the output folder")
    p.add_argument("--version", action="version", version=__version__)
    return p


def run(args: argparse.Namespace) -> int:
    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    bad = set(formats) - {"txt", "srt", "vtt", "json"}
    if bad:
        raise UserError(f"Unknown format(s): {', '.join(sorted(bad))}")
    out_dir = Path(args.out)
    src = args.source.strip()
    episode: Episode | None = None
    show = ""

    if not is_url(src):
        audio_path = Path(src)
        if not audio_path.is_file():
            raise UserError(f"File not found: {src}")
        base = slug(audio_path.stem)
    elif looks_like_audio_url(src):
        audio_path, base = None, slug(Path(urllib.parse.urlparse(src).path).stem)
        episode = Episode(title=base, audio_url=src)
    else:
        guid = None
        if parse_apple_url(src):
            src, guid = apple_to_feed(src)
            print(f"Apple Podcasts link -> RSS feed: {src}", file=sys.stderr)
        show, episodes = parse_feed(http_get(src))
        if args.list:
            print(f"{show}\n")
            for i, ep in enumerate(episodes[:30], 1):
                flag = "  [has transcript]" if ep.transcripts else ""
                print(f"{i:>3}  {ep.published or '----------'}  {ep.title}{flag}")
            return 0
        episode = pick_episode(episodes, args.episode, args.search, guid)
        base = slug(f"{episode.published}-{episode.title}" if episode.published else episode.title)
        audio_path = None
        print(f"Episode: {episode.title} ({episode.published or 'no date'})", file=sys.stderr)

        tr = best_transcript(episode)
        if tr and not args.force:
            ext = {"application/json": "json", "application/srt": "srt", "application/x-subrip": "srt",
                   "text/vtt": "vtt", "text/plain": "txt", "text/html": "html"}.get(tr["type"], "txt")
            out_dir.mkdir(parents=True, exist_ok=True)
            dest = out_dir / f"{base}.publisher-transcript.{ext}"
            dest.write_bytes(http_get(tr["url"]))
            print(f"The publisher already provides a transcript, saved: {dest}\n"
                  f"(Use --force to transcribe the audio yourself.)", file=sys.stderr)
            return 0

    tmpdir = Path(tempfile.mkdtemp(prefix="podcast-to-text-"))
    try:
        if audio_path is None:
            assert episode is not None
            suffix = Path(urllib.parse.urlparse(episode.audio_url).path).suffix or ".mp3"
            audio_path = download(episode.audio_url, tmpdir / f"audio{suffix}")
        print(f"Transcribing with faster-whisper '{args.model}' ({args.threads} threads) ...", file=sys.stderr)
        segments, meta = transcribe(audio_path, args.model, args.language, args.threads, args.compute_type,
                                    args.beam_size)
        if episode:
            meta.update({"show": show, "episode": episode.title, "published": episode.published,
                         "audio_url": episode.audio_url})
        written = write_outputs(out_dir, base, segments, meta, formats)
        if args.keep_audio and is_url(args.source):
            shutil.copy(audio_path, out_dir / f"{base}{audio_path.suffix}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    rtf = meta["processing_seconds"] / meta["audio_seconds"] if meta["audio_seconds"] else 0
    print(f"Done: {meta['audio_seconds'] / 60:.1f} min audio in {meta['processing_seconds']:.0f} s "
          f"({rtf:.2f} s per audio second), language {meta['language']}.", file=sys.stderr)
    for p in written:
        print(p)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except UserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
