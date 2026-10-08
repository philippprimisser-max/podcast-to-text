# Transcript-tag survey (8 October 2026)

How often does a popular podcast feed already ship a transcript (`<podcast:transcript>`, Podcasting 2.0)?

- `feeds-2026-10-08.json`: the feed list. Apple's public top-podcast charts for US (top 50), UK (25), Germany (25) and Austria (10), fetched on 8 October 2026 from `rss.applemarketingtools.com` and resolved to each show's RSS feed with Apple's public lookup API (110 shows).
- `results-2026-10-08.json`: what `transcript_tag_survey.py` found in those feeds that day. 109 feeds could be read (one had broken XML).
- Result: the newest episode had a transcript tag in **18 of 109** feeds; **28** feeds had it on at least one episode (US 5/49, UK 2/25, DE 8/25, AT 3/10 for the newest episode).

Re-print the numbers from the saved file:

```bash
python research/transcript_tag_survey.py --summary research/results-2026-10-08.json
```

Run it again against the same feeds (results will differ, feeds change every day):

```bash
python research/transcript_tag_survey.py
```

Each feed is fetched once, with a 0.3 s pause between feeds and an honest user agent.
