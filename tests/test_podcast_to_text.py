import functools
import http.server
import json
import os
import threading
from pathlib import Path

import pytest

import podcast_to_text as p2t

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("srv")
    for f in FIX.iterdir():
        (root / f.name).write_bytes(f.read_bytes())
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    (root / "feed.xml").write_text((FIX / "feed.xml").read_text().replace("{BASE}", base))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield base
    httpd.shutdown()


def test_parse_apple_url():
    assert p2t.parse_apple_url("https://podcasts.apple.com/us/podcast/some-show/id1234567890") == ("1234567890", None)
    assert p2t.parse_apple_url("https://podcasts.apple.com/at/podcast/x/id1234567890?i=1000123456789") == (
        "1234567890", "1000123456789")
    assert p2t.parse_apple_url("https://example.com/id1234567890") is None


def test_parse_feed_and_pick():
    show, eps = p2t.parse_feed((FIX / "feed.xml").read_text().replace("{BASE}", "http://x").encode())
    assert show == "Test Show"
    assert len(eps) == 2  # item without enclosure skipped
    assert eps[0].published == "2026-10-06"
    assert p2t.pick_episode(eps, 2).guid == "ep-1"
    assert p2t.pick_episode(eps, search="gettysburg").guid == "ep-1"
    assert p2t.pick_episode(eps, guid="ep-2").title.startswith("Episode Two")
    assert p2t.best_transcript(eps[0])["type"] == "application/srt"  # srt preferred over vtt
    with pytest.raises(p2t.UserError):
        p2t.pick_episode(eps, 5)
    with pytest.raises(p2t.UserError):
        p2t.pick_episode(eps, search="nothing matches")


def test_bad_feed():
    with pytest.raises(p2t.UserError):
        p2t.parse_feed(b"<html><body>not a feed</body></html>")


def test_formats():
    segs = [{"start": 0.0, "end": 1.5, "text": " Hello"}, {"start": 1.6, "end": 3.0, "text": "world."},
            {"start": 6.0, "end": 3725.25, "text": "Later."}]
    srt = p2t.to_srt(segs)
    assert "1\n00:00:00,000 --> 00:00:01,500\nHello\n" in srt
    assert "01:02:05,250" in srt
    assert p2t.to_vtt(segs).startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.500")
    assert p2t.to_txt(segs) == "Hello world.\n\nLater.\n"
    assert p2t.slug("2026-10-05-Episode One: Gettysburg Address!") == "2026-10-05-episode-one-gettysburg-address"


def test_default_threads_capped():
    assert 1 <= p2t.default_threads() <= 4


def test_list(server, capsys):
    assert p2t.main([f"{server}/feed.xml", "--list"]) == 0
    out = capsys.readouterr().out
    assert "Episode Two" in out and "[has transcript]" in out


def test_publisher_transcript_is_used(server, tmp_path):
    assert p2t.main([f"{server}/feed.xml", "-o", str(tmp_path)]) == 0
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and files[0].name.endswith(".publisher-transcript.srt")
    assert "Hello from the publisher." in files[0].read_text()


def test_errors_return_2(tmp_path):
    assert p2t.main([str(tmp_path / "missing.mp3")]) == 2
    assert p2t.main([str(FIX / "gettysburg.mp3"), "-f", "docx"]) == 2


@pytest.mark.skipif(os.environ.get("SKIP_SLOW") == "1", reason="slow: runs whisper")
def test_end_to_end_feed_transcription(server, tmp_path):
    rc = p2t.main([f"{server}/feed.xml", "-e", "2", "-m", "tiny", "--language", "en", "-o", str(tmp_path)])
    assert rc == 0
    names = sorted(f.name for f in tmp_path.iterdir())
    assert names == [f"2026-10-05-episode-one-gettysburg-address.{e}" for e in ("json", "srt", "txt", "vtt")]
    text = (tmp_path / names[2]).read_text().lower()
    assert "seven years ago" in text and "created equal" in text
    meta = json.loads((tmp_path / names[0]).read_text())["meta"]
    assert meta["language"] == "en" and meta["cpu_threads"] <= 4
