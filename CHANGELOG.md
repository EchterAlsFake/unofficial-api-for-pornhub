# Changelog

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

