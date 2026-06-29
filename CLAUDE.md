# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run during development — uv resolves deps from pyproject.toml into .venv automatically
uv run gen-game-info /path/to/game
uv run gen-video-info /path/to/movie.mkv

# Build a browsable catalog from already-generated pages (no network, no generation)
uv run gen-video-info --index /path/to/videos/

# Install globally as user commands (production, after any change)
uv tool install --force --reinstall .
```

`uv run` is the dev entry point: the first call creates `.venv` and installs the
package; later calls are instant. Plain `python -m x_info_generators.game.cli` will
NOT work — the package is not on the system Python's path. Either prefix with
`uv run`, or `source .venv/bin/activate` first.

There are no automated tests. Validation is done by running the commands against real data and inspecting the generated HTML.

## Architecture

The package exposes two independent entry points (`gen-game-info`, `gen-video-info`) sharing a common async infrastructure in `src/x_info_generators/`.

### Data flow (both generators)

1. **CLI** (`game/cli.py` or `video/cli.py`) — parses args, iterates items, calls `process_*`
2. **Processing** (`game/processing.py` or `video/processing.py`) — orchestrates fetchers with `asyncio.gather`, merges data, downloads and encodes images, renders HTML
3. **Fetchers** (`game/fetchers.py` or `video/fetchers.py`) — one `async def fetch_*` per data source, return `Optional[Dict]` or `None` on failure
4. **Templates** (`templates/{game_info,movie_info,series_info,season_info,index}.html.j2`) — Jinja2; all extend `base.html.j2` except `index` (standalone catalog); rendered via `templates.py`
5. **Output** — a single self-contained `.html` per item (all images as base64 WebP). Game pages are named `00_GAME_INFO.html`. Separately, `index.py` builds a catalog (`00_INDEX.html` by default) by scanning already-generated pages (`--index`).

### Shared modules

| Module | Role |
|--------|------|
| `http.py` | `create_session()` → `aiohttp.ClientSession` with connector limits; `download_file_with_progress()` with tqdm |
| `images.py` | `optimize_and_encode()` — resize to 1280px max, convert to WebP via Pillow, return base64 data URI |
| `display.py` | `DisplayMode` — centralized emoji/ANSI color constants; `--no-color` sets them to empty strings |
| `processing.py` | `ItemStats`, `RunStats`, `print_run_summary()`, `cleanup_html_files()` |
| `cli.py` | `add_common_arguments()` shared by both CLIs; `setup_environment()` configures `DisplayMode` |
| `utils.py` | `format_bytes()`, `encode_image_to_base64_data_uri()`, `run_in_executor()` |
| `templates.py` | `render_template()` with `score_color_class` Jinja2 filter |
| `index.py` | `--index` catalog: scans already-generated pages (`parse_page` reads `<title>` suffix, `img.header-image`, `.ratings`, `.details-table`), renders `index.html.j2` via `build_catalog()`. Couples to the content templates' structure. |

### Key implementation details

- All I/O is async (`aiohttp`). The `wikipedia` library is synchronous — always wrap calls with `run_in_executor()` or `asyncio.get_event_loop().run_in_executor(None, ...)`.
- Fetchers are fire-and-forget: they return `None` on any error and log a warning. The processing layer treats missing sources gracefully.
- `game/processing.py::_merge_data()` handles priority: first non-empty value wins, except `name` and `description_html` prefer the longer string.
- Images in Steam's `detailed_description` HTML are rewritten to base64 inline by `_download_and_rewrite_embedded_images()`.
- Screenshots are fetched concurrently via `asyncio.gather` then encoded sequentially.
- Video stills: `video/processing.py::_resolve_screenshots()` picks the source per `--screenshot-source` — online imdbapi.dev stills (`fetch_imdb_stills`) first, then ffmpeg on the local file as fallback (`auto`). `online`/`ffmpeg`/`off` force the behaviour. Online stills work without a local file (name-only generation).

### External APIs

| Source | Endpoint / method |
|--------|-------------------|
| Steam | `store.steampowered.com/api/storesearch` → `/api/appdetails` → `/appreviews/{id}` |
| Metacritic | HTML scraping of `/game/{slug}/` (score via JSON-LD) |
| MobyGames | HTML scraping of search results then game page |
| Wikidata | Resolves a movie's IMDb id (CirrusSearch full-text + label search) |
| imdbapi.dev | `api.imdbapi.dev/titles/{id}` (+ `/credits`, `/images`) for movies (note: `api.imdbapi.dev`, not `imdbapi.dev`). `/images` provides online stills (`still_frame`, landscape) — the default screenshot source |
| TVmaze | Series + all episodes + cast in one `singlesearch` call |
| Rotten Tomatoes | Slug `m/{slug}` (movies) or `/tv/{slug}` (series) resolved by a direct GET — no Google scraping |
| Wikipedia | `wikipedia` Python library (sync, run in executor) |
| YouTube | Scraping `ytInitialData` JSON from search results page |
| FFmpeg | `ffmpeg-python` for screenshot extraction — now the **fallback** when a title has no online stills (`--screenshot-source` controls this; optional, skipped with a warning if not in PATH) |

### Template inheritance

`base.html.j2` provides dark-theme CSS and the outer HTML shell. `game_info.html.j2` and `movie_info.html.j2` extend it via `{% extends "base.html.j2" %}` and fill `{% block content %}`. The `score_color_class` filter maps numeric scores to CSS classes (`score-9x` … `score-0x`, plus `score-unknown`).

## `orig/` directory

Contains the original monolithic scripts (`game_info_generator.py`, `movie_info_generator.py`). They are reference only — do not modify them.
