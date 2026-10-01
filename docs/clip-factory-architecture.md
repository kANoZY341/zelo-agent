# Zelo Clip Factory

## Purpose

Build a zero-touch short-form media operation under Zelo's company hierarchy.
After the Managing Director authorizes a content source and publishing account,
the system may discover, select, transform, render, publish, and measure clips
without per-post human intervention.

The system must not ingest arbitrary third-party channels. A source must be
owned, licensed, Creative Commons where the specific license permits the use,
or covered by explicit written permission. Rights evidence is stored as part
of the trusted source configuration.

## Organizational placement

```text
Managing Director
  -> Zelo — Group CEO
    -> VP Media
      -> Director Short-Form
        -> Clip Factory Manager
          -> Source Scout / Research Agent
          -> Highlight Selection Agent
          -> Video Editing Agent
          -> Packaging Agent
          -> Publishing Agent
          -> Analytics Agent
```

Workers remain replaceable execution resources beneath agents. Titles never
grant direct filesystem, browser, credential, or platform authority.

## Zero-touch pipeline

```text
Source watcher
  -> rights gate
  -> new-video discovery
  -> authorized media acquisition
  -> transcription
  -> highlight scoring
  -> segment selection
  -> transformative edit/render
  -> caption/title/package
  -> quality + duplicate gate
  -> publish queue
  -> official platform uploader
  -> status verification
  -> analytics collection
  -> learning feedback
```


## Autonomy boundary

The operator performs one-time authorization for:

- approved source identity;
- rights basis and evidence reference;
- destination account;
- daily posting ceiling;
- maximum clips per source video;
- duration bounds;
- transformation policy;
- platform credentials and scopes.

Normal jobs do not require human approval when they remain inside those grants.
Core must fail closed if the source is disabled, rights evidence is absent,
credentials are unavailable, a budget/rate limit is exhausted, the clip is a
duplicate, or the requested action exceeds the approved policy.

A global kill switch and per-source disable switch remain available.

## First publishing target

The first target is YouTube Shorts through the official YouTube Data API.

TikTok and Instagram/Reels are intentionally not part of the first unattended
publisher slice. They should be added only after their current app-review,
consent, and automated-publishing requirements are represented explicitly in
Core policy.

## Clip quality strategy

The factory should not simply crop and repost a source segment. The editing
stage should be capable of adding material transformation such as:

- reframing and scene-aware 9:16 crops;
- burned-in subtitles;
- removal of dead air;
- contextual intro or hook;
- original narration/commentary where appropriate;
- graphic callouts and chapter/context labels;
- branded but non-obstructive layout;
- source attribution where required by the license or agreement.

The exact transformation needed depends on the source license and destination
platform policy. Rights permission and monetization eligibility are separate
questions.


## Durable job identity

Every candidate clip is bound to:

- approved source ID;
- source video ID;
- exact start/end timestamps;
- target platform;
- deterministic SHA-256 fingerprint.

The fingerprint is the first duplicate-prevention boundary. Later slices should
add perceptual video/audio hashes so near-duplicate renders are also rejected.

## Implementation slices

1. **Contracts and rights gate**
   - approved source contract;
   - autonomy policy;
   - clip segment contract;
   - deterministic job fingerprint;
   - fail-closed tests.

2. **Discovery and transcript**
   - YouTube channel/upload watcher through supported public/API mechanisms;
   - durable source-video records;
   - authorized media acquisition path;
   - local transcription worker.

3. **Highlight selection**
   - transcript chunking;
   - hook/context/payoff scoring;
   - overlap suppression;
   - configurable clips-per-video ceiling.

4. **Media render**
   - FFmpeg-based cuts;
   - 9:16 smart crop;
   - subtitles;
   - templates/branding;
   - loudness and black-frame checks.

5. **Publisher**
   - OAuth credential boundary;
   - YouTube Data API upload;
   - idempotent publish queue;
   - processing/status polling;
   - retry/dead-letter handling.

6. **Analytics loop**
   - views, retention and engagement;
   - per-hook and per-format performance;
   - bounded strategy updates;
   - no self-widening of rights or publishing authority.

## Current code slice

`app/clip_factory.py` implements the first trusted contracts and policy gate.
`tests/test_clip_factory.py` proves disabled sources, invalid rights evidence,
duration drift, and duplicate-identity drift fail closed.
