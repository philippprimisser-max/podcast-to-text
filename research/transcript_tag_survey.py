#!/usr/bin/env python3
"""How many podcast feeds publish a Podcasting 2.0 <podcast:transcript> tag?

    python research/transcript_tag_survey.py                       # feeds-2026-10-08.json -> results-<today>.json
    python research/transcript_tag_survey.py my_feeds.json out.json

Input: JSON list of {"cc": country, "name": show, "feed": RSS URL}. Standard library only.
Prints per country: feeds whose newest episode has a transcript tag, and feeds where any episode has one.
"""
import datetime
import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

NS = "{https://podcastindex.org/namespace/1.0}transcript"
UA = "podcast-to-text-survey/0.1 (+https://github.com/philippprimisser-max/podcast-to-text)"
HERE = Path(__file__).resolve().parent


def check(feed: dict) -> dict:
    rec = {"cc": feed["cc"], "name": feed["name"], "feed": feed.get("feed")}
    if not rec["feed"]:
        rec["error"] = "no feed"
        return rec
    try:
        req = urllib.request.Request(rec["feed"], headers={"User-Agent": UA})
        items = ET.fromstring(urllib.request.urlopen(req, timeout=30).read()).find("channel").findall("item")
        rec["items"] = len(items)
        rec["newest_has"] = bool(items and items[0].findall(NS))
        rec["any_has"] = any(i.findall(NS) for i in items)
        rec["types"] = sorted({t.get("type", "") for i in items[:5] for t in i.findall(NS)})
    except Exception as e:  # network errors, broken XML: count as "not checked"
        rec["error"] = type(e).__name__
    return rec


def summarize(out: list) -> None:
    ok = [r for r in out if "items" in r]
    print("feeds checked", len(ok), "of", len(out))
    for cc in sorted({r["cc"] for r in ok}):
        sub = [r for r in ok if r["cc"] == cc]
        print(cc, "newest episode has transcript:", sum(r["newest_has"] for r in sub), "/", len(sub),
              "| any episode:", sum(r["any_has"] for r in sub))
    print("total newest:", sum(r["newest_has"] for r in ok), "/", len(ok), " any:", sum(r["any_has"] for r in ok))
    print(Counter(t for r in ok for t in r["types"]))


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "feeds-2026-10-08.json"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / f"results-{datetime.date.today()}.json"
    out = []
    for feed in json.loads(src.read_text(encoding="utf-8")):
        out.append(check(feed))
        time.sleep(0.3)  # be polite to the hosts
    dst.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    summarize(out)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--summary":  # re-print the numbers of a saved result file
        summarize(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
    else:
        main()
