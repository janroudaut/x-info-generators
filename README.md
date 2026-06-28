# x-info-generators

[![License: WTFPL](https://img.shields.io/badge/license-WTFPL-brightgreen.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

Turn your local **games**, **movies** and **TV series** into self-contained HTML info pages — then browse them all from one searchable catalog.

<p align="center">
  <img src="assets/screenshots/catalog-videos.png" width="900" alt="A searchable catalog of movies and TV series">
</p>

Every generated page is a **single portable `.html` file** — zero external dependencies, no CDN, no JS/CSS imports. Images are optimized (WebP via Pillow) and embedded as base64 data URIs. Fetched data is cached on disk, so re-runs are near-instant and can run fully **offline**.

## Install

```bash
uv tool install .
```

This installs two commands: [🎬 **`video-info-gen`**](#-video-info-gen) and [🎮 **`game-info-gen`**](#-game-info-gen).

---

## 🎬 video-info-gen

Handles **movies and TV series**, deciding what each video *is* from its **content**, never from the folder name (a directory is just an organizational placeholder — `old`, `films 2024`, a "collection"…).

- **Movies** → one `{filename}.html` next to the video.
- **TV series** → episodes (`SxxExx`, in `Season N` subfolders or loose at the root) are grouped into **one series page**, plus **one page per season** that lives in its own folder. Owned episodes are marked (`✓`); the page lists the full season from the metadata source.
- **Collections** (a folder of several unrelated movies) → one page **per movie**; the folder name is ignored.
- Content not found on its metadata source (e.g. web-only clips) is **skipped** — no page is created.

```bash
video-info-gen /path/to/The.Matrix.1999.mkv           # single movie
video-info-gen -R /path/to/videos/                    # whole library (movies + series)
video-info-gen -R --force /path/to/videos/            # force regeneration
video-info-gen -R -C /path/to/videos/                 # remove generated HTML

# Skip directories (repeatable; glob, case-insensitive; wrap in /.../ for a regex)
video-info-gen -R --ignore '*Le dessous des images*' --ignore '/s\d+e\d+ sample/' /path/to/videos/
```

A movie page (poster, ratings, cast, plot, Wikipedia, screenshots, related videos) and a series page (ratings, network, cast, then every season with its episodes):

<p align="center">
  <img src="assets/screenshots/movie-page.jpg" width="48%" alt="Full movie info page">
  <img src="assets/screenshots/series-page.jpg" width="48%" alt="TV series info page">
</p>

<details>
<summary>Per-season page</summary>

Each season that lives in its own folder also gets a page listing every episode (owned ones marked `✓`) with summaries and ratings.

<p align="center">
  <img src="assets/screenshots/season-page.jpg" width="700" alt="Per-season page with episode summaries">
</p>
</details>

## 🎮 game-info-gen

Generates a `00_GAME_INFO.html` in each game directory, aggregating Steam, Metacritic, Wikipedia, MobyGames and Steam user reviews.

```bash
game-info-gen "/path/to/Hollow Knight"      # single game
game-info-gen -R /path/to/games/            # scan subdirectories as individual games
game-info-gen -R --force /path/to/games/    # force regeneration
game-info-gen -R -C /path/to/games/         # remove generated 00_GAME_INFO.html files
```

A full game page — description, details, Metacritic + Steam reviews, links and a screenshot gallery:

<p align="center">
  <img src="assets/screenshots/game-page.jpg" width="560" alt="Full game info page">
</p>

## 🗂️ Catalog (`--index`)

`--index` builds a single, browsable **catalog** from the pages **already generated** on disk — no generation, no network. It scans the given paths for generated `.html`, reads each page (title, type, year, ratings, poster) and writes a self-contained catalog file (`00_INDEX.html` by default) with client-side **search, filter and sort**. The video catalog is the page at the top of this README; here's a games one:

<p align="center">
  <img src="assets/screenshots/catalog-games.png" width="760" alt="A games catalog">
</p>

```bash
# a videos catalog (scan the dir, write ./00_INDEX.html)
video-info-gen --index /path/to/videos/

# a games catalog
game-info-gen --index /path/to/games/

# choose the output file
video-info-gen --index my-catalog.html /path/to/videos/
```

A single-type catalog drops the type filter and names itself after that type (e.g. **Games**). The type is read from each page — so you *can* point one run at several roots for a combined catalog, but per-category is the usual case. Posters/headers are downscaled and inlined (one portable file); season pages are left out.

| Flag | Description |
|------|-------------|
| `--index [OUTPUT]` | Build the catalog under the given paths, then exit. An optional value is the output file; a directory there is treated as a path to scan (default output: `00_INDEX.html`) |
| `--title TEXT` | Catalog page title (default: derived — the single type if there's only one, else "Catalog") |
| `--max-depth N` | Max directory depth scanned by `--index` (default: 5) |
| `--wsl` | Emit Windows `file://` links (e.g. `D:/…`) for `/mnt/<drive>/` paths, so a catalog built under WSL opens correctly in a Windows browser |

> **Tip:** run any command with `-h` / `--help` for the full, authoritative option list — it's always in sync with the installed version.

---

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
| `-h, --help` | Full, authoritative CLI reference |

`--ignore` is specific to `video-info-gen`.

## Supported video formats

Common ones: `.mp4`, `.mkv`, `.avi`, `.mov`, `.webm`, `.ts`, `.mpg`, `.m4v`, `.wmv`, `.flv` — plus a broad set of other containers (`.m2ts`, `.mpeg`, `.vob`, `.3gp`, `.divx`, `.rmvb`, `.mxf`, …). In practice anything **FFmpeg** can read is fine.

[FFmpeg](https://ffmpeg.org/) is **optional** — it's only used to extract screenshots. Without it in `PATH`, `video-info-gen` prints a warning and generates pages without screenshots (all other data is still fetched).

## Data sources

<details>
<summary>🎮 Games</summary>

| Source | Data |
|--------|------|
| Steam API | Title, description (`about_the_game`), header image, screenshots, genres, release date, developers, publishers |
| Steam Reviews | User reviews with recommendation badge |
| Metacritic | Score (via JSON-LD) |
| Wikipedia | Summary, page link |
| MobyGames | Additional description, page link |
</details>

<details>
<summary>🎬 Movies &amp; 📺 TV series</summary>

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
</details>

## Development

```bash
uv run video-info-gen /path/to/movie.mkv     # dev run (resolves deps into .venv automatically)
uv run game-info-gen /path/to/game

uv tool install --force --reinstall .        # reinstall the global commands after changes
```

> Run `uv run` **from the project directory** — otherwise uv falls back to the globally installed (possibly stale) tool.

<details>
<summary>Project structure</summary>

```
src/x_info_generators/
├── __init__.py          # __version__
├── display.py           # DisplayMode (emoji/color management)
├── utils.py             # format_bytes, base64 encoding, run_in_executor, path_matches_ignore
├── images.py            # Pillow image optimization (→ WebP), cached_image_data_uri, downscale_data_uri
├── http.py              # aiohttp session, download with progress bar
├── cache.py             # FetchCache (on-disk cache), purge_cache
├── processing.py        # ItemStats, RunStats, print_run_summary, cleanup_html_files
├── cli.py               # Common CLI arguments
├── index.py             # --index catalog: scan generated pages → 00_INDEX.html
├── templates.py         # Jinja2 rendering, score_color_class & linebreaks filters
├── templates/
│   ├── base.html.j2     # Common dark theme CSS, HTML structure
│   ├── game_info.html.j2
│   ├── movie_info.html.j2
│   ├── series_info.html.j2
│   ├── season_info.html.j2
│   ├── index.html.j2    # catalog page
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
</details>

## License

[WTFPL](LICENSE) — Do What The Fuck You Want To Public License.
