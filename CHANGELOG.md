# Changelog

## 5.4.6 — 2026-10-27

- Parse HubTraffic search results as JSON instead of treating them as HTML.
- Use the correct upload and GIF routes for regular users, with more resilient profile listing extraction.
- Fix photo extension parsing and album downloads to directory paths.
- Accept bare viewkeys, relative video links and embed URLs.
- Expose author helpers consistently across videos, shorts and GIFs, including channel authors.
- Read localized profile statistics and route more supported media types through the CLI.
- Use the published `eaf-base-api>=4.2.5` dependency.

