# 6.0.0

Release 6.0.0

Update pagination handling, validation, and documentation.

Changes since v5.4.8:

```text
README.md                     |  14 +
 pornhub_api/__init__.py       |  22 +-
 pornhub_api/api.py            | 593 +++++++++++++++++++++++++++++++++---------
 pornhub_api/modules/consts.py |  72 ++---
 pornhub_api/modules/errors.py |   4 +
 pyproject.toml                |   4 +-
 6 files changed, 544 insertions(+), 165 deletions(-)
```

# Changelog

## 5.4.8 - 2026-11-14

- Reject an unmatched explicitly requested Short instead of returning another item from the page.

## 5.4.7 — 2026-11-12

- Retry expired HLS playlists once, then refresh the PornHub session and video page before retrying with a new playlist.

## 5.4.6 — 2026-10-27

- Parse HubTraffic search results as JSON instead of treating them as HTML.
- Use the correct upload and GIF routes for regular users, with more resilient profile listing extraction.
- Fix photo extension parsing and album downloads to directory paths.
- Accept bare viewkeys, relative video links and embed URLs.
- Expose author helpers consistently across videos, shorts and GIFs, including channel authors.
- Read localized profile statistics and route more supported media types through the CLI.
- Use the published `eaf-base-api>=4.2.5` dependency.

