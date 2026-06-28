# x-info-generators

Generate self-contained HTML info pages for local **game directories**, **movie files**, and **TV series**.

Each generated `.html` is a single portable file — zero external dependencies, no CDN, no JS/CSS imports. All images are optimized (WebP via Pillow) and embedded as base64 data URIs. Fetched data is cached on disk, so re-runs are near-instant and can run fully offline.

## Install

```bash
uv tool install .
```

This installs two commands: `game-info-gen` and `video-info-gen`.

## game-info-gen

Generates a `game_info.html` in each game directory by aggregating data from Steam, Metacritic, Wikipedia, MobyGames, and Steam user reviews.

```
game-info-gen [-R] [--force] [-C] [cache options] [--max-concurrent N] [--timeout N] INPUT_DIR [...]
```

### Examples

```bash
game-info-gen "/path/to/Hollow Knight"      # single game
game-info-gen -R /path/to/games/            # scan subdirectories as individual games
game-info-gen -R --force /path/to/games/    # force regeneration
game-info-gen -R -C /path/to/games/         # remove generated game_info.html files
```

### Data sources

| Source | Data |
|--------|------|
| Steam API | Title, description (`about_the_game`), header image, screenshots, genres, release date, developers, publishers |
| Steam Reviews | User reviews with recommendation badge |
| Metacritic | Score (via JSON-LD) |
| Wikipedia | Summary, page link |
| MobyGames | Additional description, page link |

## video-info-gen

Handles **movies and TV series**, deciding what each video *is* from its **content**, never from the folder name (a directory is just an organizational placeholder — `old`, `films 2024`, a "collection"…).

- **Movies** → one `{filename}.html` next to the video.
- **TV series** → episodes (`SxxExx`, whether in `Season N` subfolders or loose at the root) are grouped into **one series page** at the series root, **plus one page per season** that lives in its own folder. Episodes you own are marked (`✓`); the page lists the full season from the metadata source.
- **Collections** (a folder holding several unrelated movies) → one page **per movie**; the folder name is ignored.
- Content not found on its metadata source (e.g. web-only clips) is **skipped** — no page is created.

```
video-info-gen [-R] [--force] [-C] [--ignore PATTERN] [cache options] PATH [...]
```

### Examples

```bash
video-info-gen /path/to/The.Matrix.1999.mkv           # single movie
video-info-gen -R /path/to/videos/                    # whole library (movies + series)
video-info-gen -R --force /path/to/videos/            # force regeneration
video-info-gen -R -C /path/to/videos/                 # remove generated HTML

# Skip directories (repeatable; glob, case-insensitive; wrap in /.../ for a regex)
video-info-gen -R --ignore '*Le dessous des images*' --ignore '/s\d+e\d+ sample/' /path/to/videos/
```

### Data sources

**Movies**

| Source | Data |
|--------|------|
| [Wikidata](https://www.wikidata.org/) | Resolves the IMDb id robustly (full-text + label search), avoiding IMDb's flaky search endpoint |
| [imdbapi.dev](https://imdbapi.dev/) `/titles/{id}` | Title, year, IMDb rating, plot, poster, directors, cast (with characters & photos), genres |
| Rotten Tomatoes | Tomatometer + Audience scores (clickable rating badges) |
| Wikipedia | Summary, page link |
| YouTube | Official trailer, review/trivia videos with thumbnails |
| FFmpeg | Screenshots extracted from the video |

**TV series**

| Source | Data |
|--------|------|
| [TVmaze](https://www.tvmaze.com/api) | Series + all episodes + cast in one call: rating, genres, network, IMDb id, poster, per-season episode list (names, summaries, ratings) |
| imdbapi.dev | IMDb rating badge (via the TVmaze-provided id) |
| Rotten Tomatoes (`/tv/`) | Tomatometer + Audience scores |
| Wikipedia, YouTube, FFmpeg | As for movies (screenshots taken from the first owned episode) |

### Supported video formats

Common ones: `.mp4`, `.mkv`, `.avi`, `.mov`, `.webm`, `.ts`, `.mpg`, `.m4v`, `.wmv`, `.flv` — plus a broad set of other containers (`.m2ts`, `.mpeg`, `.vob`, `.3gp`, `.divx`, `.rmvb`, `.mxf`, …). In practice anything **FFmpeg** can read is fine, since screenshots are extracted with FFmpeg.

### Prerequisites

[FFmpeg](https://ffmpeg.org/) is **optional** — it's only used to extract screenshots. If it isn't in `PATH`, `video-info-gen` prints a warning and generates pages without screenshots (all other data is still fetched).

## Caching

Successful fetches (metadata **and** optimized images) are cached under `~/.cache/x-info-generators/`
(respects `XDG_CACHE_HOME`), one JSON file per entry. Failures are never cached, so missing sources
are retried on the next run. The cache **never expires on its own** — cleanup is explicit.

| Flag | Description |
|------|-------------|
| `--no-cache` | Disable the cache (always hit the network, store nothing) |
| `--update-cache` | Re-fetch everything and overwrite cached entries (refresh stale data) |
| `--offline`, `--cache-only` | Use only cached data; make **no** network requests (and no FFmpeg). Pair with `--force` to re-render the whole library from cache after a template change |
| `--purge-cache` | Delete cache entries older than `--cache-ttl` days, then exit (no path needed) |
| `--cache-ttl N` | Age in days used by `--purge-cache`; `0` purges everything (default: 30) |

```bash
video-info-gen -R --offline --force /path/to/videos/   # re-render from cache, no network
video-info-gen --purge-cache --cache-ttl 0             # wipe the cache
```

## Common options

| Flag | Description |
|------|-------------|
| `-R, --recursive` | Scan subdirectories |
| `--force` | Regenerate even if `.html` already exists |
| `-C, --cleanup` | Remove generated `.html` files (incl. series + season pages) |
| `--no-color` | Disable emoji and color output |
| `--debug` | Print aggregated data for debugging |
| `--max-screenshots N` | Limit number of screenshots (default: 8) |
| `-V, --version` | Show version |

`--ignore` is specific to `video-info-gen`.

## Development

```bash
uv run video-info-gen /path/to/movie.mkv     # dev run (resolves deps into .venv automatically)
uv run game-info-gen /path/to/game

uv tool install --force --reinstall .        # reinstall the global commands after changes
```

> Run `uv run` **from the project directory** — otherwise uv falls back to the globally installed
> (possibly stale) tool.

## Project structure

```
src/x_info_generators/
├── __init__.py          # __version__
├── display.py           # DisplayMode (emoji/color management)
├── utils.py             # format_bytes, base64 encoding, run_in_executor, path_matches_ignore
├── images.py            # Pillow image optimization (→ WebP), cached_image_data_uri
├── http.py              # aiohttp session, download with progress bar
├── cache.py             # FetchCache (on-disk cache), purge_cache
├── processing.py        # ItemStats, RunStats, print_run_summary, cleanup_html_files
├── cli.py               # Common CLI arguments
├── templates.py         # Jinja2 rendering, score_color_class & linebreaks filters
├── templates/
│   ├── base.html.j2     # Common dark theme CSS, HTML structure
│   ├── game_info.html.j2
│   ├── movie_info.html.j2
│   ├── series_info.html.j2
│   ├── season_info.html.j2
│   └── _series_macros.html.j2   # shared ratings_block, cast_list, episode_list macros
├── game/
│   ├── cli.py           # game-info-gen entry point
│   ├── fetchers.py      # Steam, Metacritic, Wikipedia, MobyGames
│   └── processing.py    # clean_game_title, merge_data, process_game_directory
└── video/
    ├── cli.py           # video-info-gen entry point
    ├── discovery.py     # content-based classification (movies vs series, collections)
    ├── fetchers.py      # Wikidata, imdbapi.dev, TVmaze, Rotten Tomatoes, Wikipedia, YouTube, FFmpeg
    └── processing.py    # process_movie_file, process_series
```
