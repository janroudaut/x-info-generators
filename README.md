# x-info-generators — demo site

This branch (`gh-pages`) hosts the live demo for
**[x-info-generators](https://github.com/janroudaut/x-info-generators)**:

👉 **https://janroudaut.github.io/x-info-generators/**

## How these sample pages were generated (no files, no ownership needed)

To avoid any misunderstanding: **generating an info page does not require
owning the game (or movie), nor having any media file on disk.**

`gen-game-info` uses the **directory name only** as a search query, then
fetches **publicly available metadata and images** (Steam store, Metacritic,
Wikipedia, MobyGames, …) and inlines them into a self-contained HTML page.
The directory can be completely empty.

```bash
# An empty folder is enough — its name is the only input.
mkdir "Forza Horizon 5"
gen-game-info "Forza Horizon 5"
# → "Forza Horizon 5/00_GAME_INFO.html", built purely from public data.
```

That is exactly how the game pages in this demo were produced: from a
**name**, against public APIs. Their presence here implies nothing about
possessing any game, movie, or file.

The same applies to `gen-video-info` for movies and series — the metadata
comes from public sources (TVmaze, imdbapi.dev, Wikipedia, …). The only
feature that touches a local file is the optional screenshot extraction
(FFmpeg), which is skipped when no video file is present.

## Source & documentation

Everything (install, options, data sources) lives in the main repository:
**https://github.com/janroudaut/x-info-generators**
