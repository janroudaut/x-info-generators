# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [1.2.1] — 2026-06-29

### Changed
- **`--help` reorganized** — options grouped by topic (generation, catalog,
  caching, display, network) with aligned descriptions and uppercase section
  headings (GNU/eza-style formatter). A bare invocation now prints full help.
- Invocation errors print a short usage line plus a highlighted message on
  stderr and exit with status 2; clean exit codes throughout (130 on Ctrl-C).

## [1.2.0] — 2026-06-29

### Changed
- **Commands renamed** to a shared, tab-completion-friendly `gen-` prefix:
  `game-info-gen` → **`gen-game-info`**, `video-info-gen` → **`gen-video-info`**.
  After upgrading: `uv tool install --force --reinstall .`.

## [1.1.0] — 2026-06-29

### Added
- **Catalog (`--index`)** — build a single, self-contained, browsable `00_INDEX.html`
  from the pages **already generated** on disk: client-side search, type filter and
  sort, downscaled inlined thumbnails. No generation, no network.
  - `--title TEXT` to set the page title; a single-type catalog drops the type filter
    and names itself after that type (e.g. *Games*).
  - `--max-depth N` to cap scan depth; follows symlinked directories.
  - `--wsl` to emit Windows `file://` links for `/mnt/<drive>/` paths.
  - Written atomically (temp file + rename).
- User-Agents now identify the tool via the project repository URL.

### Changed
- **Documentation overhaul** — README redesigned (per-mode sections, full-page
  screenshots, per-category catalog showcases), this CHANGELOG added, `CLAUDE.md`
  refreshed, richer package metadata (project URLs, keywords, readme).
- *Minor:* game output file renamed `game_info.html` → **`00_GAME_INFO.html`** (the
  `00_` prefix sorts it to the top of the game folder; no users affected yet 😉).

## [1.0.0]

- Initial release.
- **`game-info-gen`** — aggregates Steam, Metacritic, Wikipedia, MobyGames and Steam
  user reviews into a self-contained game page.
- **`video-info-gen`** — content-based classification of movies vs. TV series;
  movies via Wikidata → imdbapi.dev, series via TVmaze, plus Rotten Tomatoes,
  Wikipedia, YouTube and FFmpeg screenshots.
- Single self-contained HTML per item (WebP images inlined as base64), on-disk cache
  with offline mode.
