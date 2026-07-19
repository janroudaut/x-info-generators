# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [1.6.0] — 2026-07-19

### Added
- **File details on video pages** (`ffprobe`): resolution + codec, audio
  tracks and subtitles with language flags. Tracks belong to episodes, so
  series/season pages show them per episode line, with a series-level union
  in the details table. Cached by file path+mtime. Untagged/`und` tracks show
  a neutral 🌐, and identical unlabeled tracks are collapsed into one entry
  with a count ("🌐 ×14").
- **Language flags on catalog cards** (🔊 🇫🇷🇬🇧 💬 🇫🇷), read from the pages'
  file details — regenerate pages (`--force`) then the catalog to see them.
- **Audio-language filter in the catalog** (videos) — an "All languages"
  dropdown with flags and counts; ISO 639 variants (`fr`/`fra`/`fre`) are
  merged into one entry.
- **Resolution/audio badge on catalog posters** — top-right corner pill like
  "4K · 5.1" (resolution label + best channel layout when above stereo).
  Subtitles on cards show flags up to 3 distinct languages, a count beyond
  (full list as tooltip); audio flags are spaced.

### Changed
- **More readable per-item CLI output** — the header shows the movie's
  filename (inside a collection folder the cleaned title is identical for
  every file), and the status line leads with the resolved title:
  `📊 Dr. No (1962) | 📄 SUCCESS | ⏱️ 4.48s | 1.87 MB`. Same format for
  `gen-game-info` (resolved Steam name).

## [1.5.1] — 2026-07-18

### Added
- **Catalog search: quoted phrases** — `"daniel craig"` matches the exact
  phrase instead of AND-ing the words (an unclosed quote degrades to a plain
  word search).

### Changed
- **Catalog search: smarter year matching** — a term starting with 19/20 and
  at least 3 digits long is treated as a year and matches by prefix ("197"
  lists the whole 1970s); any other term (e.g. "007") no longer matches the
  year, only titles/folders/people. Years are also stripped from the indexed
  folder paths, so a "(2007)" in a folder name can't leak into text search.

### Fixed
- Catalog cards showed a literal "None" after the year for items without a
  runtime (games, since the runtime line landed in 1.2.2).

## [1.5.0] — 2026-07-18

### Added
- **Genre filter in the catalog** (`--index`) — a "All genres" dropdown in the
  toolbar (each genre with its item count), combinable with the search box and
  the type filter.
- **Catalog search matches more than titles** — the search box also looks at
  the year (typing "2004" lists that year's titles) and, for videos, the
  directory path under the scanned root (a "007" collection folder), the cast
  and the directors. Whitespace-separated terms combine (AND): "keanu 1999"
  lists Keanu Reeves titles from 1999.

## [1.4.1] — 2026-07-18

### Fixed
- **Filename parsing: a year in parentheses/brackets wins** and marks the end
  of the title, so titles containing a bare year are no longer truncated there
  ("New-york 1997 (1981)…" is now searched as "New york 1997" (1981) — i.e.
  *Escape from New York* — instead of "New york" (1997)). The bare-year
  truncation remains as fallback for names like `The.Matrix.1999.1080p.mkv`.

## [1.4.0] — 2026-07-18

### Added
- **`--tmdb-api-key KEY`** (`gen-video-info`, new "network" option group) to
  pass the TMDB key on the command line; overrides the `TMDB_API_KEY`
  environment variable.

### Changed
- **Movie metadata now comes from TMDB** — imdbapi.dev vanished (the domain no
  longer resolves), so `gen-video-info` fetches movie details, cast, posters
  and online stills (backdrops) from [TMDB](https://www.themoviedb.org/)
  instead. The Wikidata → IMDb-id resolution is unchanged; TMDB maps the id
  via `/find/{imdb_id}`. Requires a free API key in **`TMDB_API_KEY`** (v3 key
  or v4 read access token) or via **`--tmdb-api-key KEY`**; without it, movie
  pages degrade to partial pages and a warning is printed.
- Rating badges on pages and catalogs are now labelled **TMDB** (TMDB
  community rating, linking to the TMDB page) instead of IMDb. Catalogs still
  recognise the IMDb badge of previously generated pages.

## [1.3.0] — 2026-06-29

### Added
- **Online screenshots** — movie/series stills are now fetched from
  imdbapi.dev (`/titles/{id}/images`) by default, so pages have real
  screenshots even when generated from a name alone (no local file needed).
  FFmpeg extraction becomes the fallback for titles without online stills.
- **`--screenshot-source {auto,online,ffmpeg,off}`** (`gen-video-info`) to pick
  the stills source — `auto` (default) tries online first then FFmpeg.

## [1.2.2] — 2026-06-29

### Added
- **Runtime / episode length** shown on movie and series pages (and on catalog
  cards): movies via imdbapi.dev `runtimeSeconds`, series via TVmaze
  `averageRuntime`.

### Changed
- **Cast cards** top-aligned, with roles clamped to 3 lines (+ a tooltip) so
  actors with many roles no longer stretch the row.

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
