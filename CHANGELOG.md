# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [1.6.0] — 2026-08-05

> **Upgrading**: entries cached by an earlier version don't carry the fields
> this release reads (a film's original language, its collection). Franchise
> tags backfill themselves on the next run; French titles need the movie detail
> refetched — `--update-cache`, or delete
> `~/.cache/x-info-generators/movie-tmdb-detail`. Pages must be regenerated
> (`--force`) before rebuilding the catalog.

### Fixed
- **Catalog filters were lost on the way back** — opening an item and hitting
  Back landed on an unfiltered, unsorted grid. Search, filters and sort order
  now live in the URL fragment, so the browser restores them like any other
  address (and a narrowed view becomes shareable). The History API is
  unavailable on some `file://` setups, where a fragment-only
  `location.replace()` does the same job without stacking history entries.
  A head script keeps the cards out of the first paint while a filtered view
  is being restored, so the grid no longer flashes unfiltered — only when
  there is state to restore, and with a failsafe should the script die. With
  no state to restore the catalog touches no card at all on load (it ships
  sorted by title), and sorting skips re-inserting cards whose order already
  holds.
- **Accented and numeric titles sorted last in the catalog** — the page was
  built with a key that folded case but not diacritics, so an accented initial
  landed past "z", and numeric segments were ranked behind every letter. Both
  now match the browser's collator: digits first, accents folded. The order
  the page ships with is the one "Sort: Title" produces, so picking it never
  reshuffles the grid.
- **Titles truncated at the year** — files named
  `Collection - YEAR - Title - specs` (a very common pattern for boxed sets)
  lost everything behind the year, so every entry of a set was looked up under
  the collection's name, the year left as the only discriminant — enough for
  some of them to land on another film entirely, silently. When a whole
  dash-separated field is a year, the field behind it now leads the search,
  with the previous title kept as a fallback candidate. Names with the year in
  `(…)`/`[…]` or at the end are unaffected.
- **A year as the whole title** — a name opening on a bare year read it as the
  release year, leaving no title at all and failing the file outright. A
  release year always has a title in front of it, so years with nothing before
  them are skipped.
- **Episodes read as movies** — three naming habits fell through the episode
  patterns and produced a standalone movie page per file: `S2301` (the `E`
  dropped), `101 - Title.avi` (season and episode as three bare digits), and
  seasons held twice. The bare-digit form is only trusted where several files
  in a folder are numbered that way, so a title starting with digits stays a
  film. A season present twice (a tidy `Season N` folder plus a pack of the
  same episodes) is deduplicated instead of being listed twice and losing its
  season page.
- **Hidden files are skipped** — transcoders park `.convert-XXXX.mkv` next to
  the source; those were picked up and given pages of their own.
- **Screenshots lost to a wrong duration** — timestamps were derived from
  `format.duration`, which a broken audio stream can push hours past the last
  video frame: every shot behind the real end failed, and the exception
  discarded the ones already taken. The container and video-stream durations
  are now reconciled (Matroska reports no per-stream duration, so neither can
  be trusted alone), and a failed shot no longer voids the batch — partial
  results are kept and reported as `n/N`.
- **Movie misresolution guards** — a French-market release named after a local
  title could resolve to a completely different film via a translated Wikidata
  label. Now: candidates whose release year is more than ±1 off the filename's
  are rejected (Wikidata pick and post-cache TMDB detail check), and TMDB
  title-search results are scored by title similarity (accepted above a
  threshold, stricter on the year-less retry) instead of taking the first
  result. A French release tag (`FRENCH`, `TRUEFRENCH`, `VF`, `VOSTFR`,
  `MULTI`…) localizes the search so similarity is computed against French
  titles. When nothing confident remains and the filename carries a year, a
  **partial page** is built instead of a wrong film (files without a year are
  still skipped).
- **Miniseries/TV specials stored as movie files** now get full TMDB metadata:
  when `/find` resolves the IMDb id to a TV title, the `/tv/{id}` detail is
  fetched and mapped to the movie shape (creators shown as directors, episode
  runtime), instead of degrading to a partial page.

### Added
- **French films shown under their French title** — TMDB's canonical title is
  the English one, so a French film could be listed under an English name
  nobody uses for it. Titles TMDB reports as French-language
  (`original_language`) now show their original title, their French synopsis
  when there is one, the French poster artwork, a French Wikipedia summary
  (falling back to English) and French-worded YouTube queries. The English
  title stays visible under the heading and remains the lookup key for Rotten
  Tomatoes and the English Wikipedia. Genres deliberately stay in English so
  the catalog filter doesn't split into "Comédie" and "Comedy".
- **Franchise tags from TMDB collections** — movie pages gain a *Collection*
  row and the catalog a franchise filter, both fed by TMDB's curated
  `belongs_to_collection`. It costs no extra request (the field rides along
  with the movie detail) and, unlike grouping by folder, it holds when the
  sequels sit in unrelated folders and never mistakes a release pack for a
  franchise.
- **Fuller title when TMDB's is the short one** — TMDB sometimes holds only
  the short form of a title; when the filename clearly names a longer one, the
  alternative-titles list provides it (scene release names in that list are
  filtered out).
- **IMDb id pinning in the filename** — a Radarr-style `{tt1234567}` token
  (braces optional) forces the resolution, for titles no source knows under
  their local release name.
- **File details on video pages** (`ffprobe`): resolution + codec, audio
  tracks and subtitles with language flags. Tracks belong to episodes, so
  series/season pages show them per episode line, with a series-level union in
  the details table. Cached by file path+mtime. Identical unlabeled tracks are
  collapsed into one entry with a count.
- **Language flags on catalog cards**, read from the pages' file details —
  regenerate pages (`--force`) then the catalog to see them.
- **Audio-language filter in the catalog** (videos) — an "All languages"
  dropdown with counts; ISO 639 variants (`fr`/`fra`/`fre`) are merged into
  one entry.
- **Resolution/audio badge on catalog posters** — top-right corner pill like
  "4K · 5.1" (resolution label + best channel layout when above stereo).
  Subtitles on cards show flags up to 3 distinct languages, a count beyond
  (full list as tooltip). The movie page's Subtitles row follows the same
  rule.

### Changed
- **The catalog degrades cleanly without JavaScript** — the grid, posters,
  flags and links never needed it; the toolbar, which does, now hides itself
  via `<noscript>` instead of offering dead controls.
- **Language flags are inline artwork, not emoji** — Windows ships no flag
  glyphs, so Chrome rendered a flag emoji as the bare letter pair it is built
  from (Firefox only escaped this by bundling its own emoji font). Flags are
  now inline SVG carried by the page, identical in every browser; where markup
  can't go (tooltips, `<option>` labels) the language code is spelled out.
  Untagged tracks show `?`.
- **Screenshots come from the file first** — `--screenshot-source auto` now
  extracts frames locally and falls back to TMDB backdrops only when ffmpeg is
  missing or yields nothing (it was the other way round). Stills then show the
  actual copy, cut and grading included.
- **Natural title sort in the catalog** — the part before the colon is
  compared first, so a first instalment precedes its numbered sequel (`:`
  outranks ` 2` in any collation). Numeric-aware, applied both when building
  the page and in the browser, and used as a tiebreak for the year and rating
  sorts.
- **Catalog: the main score drops its "TMDB" label** — the number alone, with
  the source as a tooltip; the other badges (IMDb, 🍅, 🍿, MC) are unchanged.
- **More readable per-item CLI output** — the header shows the movie's
  filename (inside a collection folder the cleaned title is identical for
  every file), and the status line leads with the resolved title, status,
  duration and page size. Same format for `gen-game-info`.

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
