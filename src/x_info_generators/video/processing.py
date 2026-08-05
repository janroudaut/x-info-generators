import asyncio
import os
import re
import shutil
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

import aiohttp

from ..display import DisplayMode as D
from ..images import cached_image_data_uri, optimize_and_encode
from ..processing import ItemStats
from ..templates import render_template
from .. import __version__
from .fetchers import (
    fetch_tmdb_search, fetch_tmdb_detail, fetch_tmdb_rating, fetch_tmdb_stills,
    fetch_tmdb_fr_details, fetch_tmdb_specific_title, fetch_tmdb_collection,
    resolve_imdb_id_wikidata,
    generate_screenshots_async, probe_media_info,
    fetch_rotten_tomatoes_data, fetch_wikipedia_data,
    fetch_youtube_trailer, fetch_youtube_reviews,
    fetch_tvmaze_series,
)


async def _cached(cache, namespace, key, factory):
    """Return a cached fetch result, or call ``factory()`` (a lambda) on a miss."""
    hit, value = cache.get(namespace, key)
    if hit:
        return value
    if cache.offline:
        return None  # never hit the network in offline mode
    value = await factory()
    cache.set(namespace, key, value)
    return value

VIDEO_EXTENSIONS = (
    ".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".qt", ".wmv", ".asf",
    ".flv", ".f4v", ".ts", ".m2ts", ".mts", ".mpeg", ".mpg", ".mpe", ".m2v",
    ".vob", ".ogv", ".ogm", ".3gp", ".3g2", ".divx", ".rm", ".rmvb", ".mxf",
)
NOISE_REGEX = re.compile(
    r'(\b(5 1|7 1|8bit|10bit|2160p|1080p|720p|480p|aac|ac3|bluray|brrip|directors cut|dts|dual|dvdrip|extended|hdrip|hevc|multi|repack|remastered|uncut|unrated|uhd|4k|hdr|web dl|webrip|x264|x265|yify|yts)\b)',
    re.IGNORECASE,
)


def find_movie_files(path: Path) -> List[Path]:
    """Recursively find all video files in a given path."""
    movie_files = []
    if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
        movie_files.append(path)
    elif path.is_dir():
        for root, _, files in os.walk(path):
            for file in files:
                # Dotfiles here are work in progress, not library content
                # (transcoders park ".convert-XXXX.mkv" next to the source).
                if file.lower().endswith(VIDEO_EXTENSIONS) and not file.startswith("."):
                    movie_files.append(Path(root) / file)
    return movie_files


# French-market release tags: the file was named after the French release
# title, so TMDB lookups should compare against French titles too.
_FR_TAG_REGEX = re.compile(r'\b(truefrench|french|vff|vfq|vfi|vf|vostfr|vost|multi)\b', re.IGNORECASE)
_IMDB_ID_REGEX = re.compile(r'[{\[]?\b(tt\d{7,8})\b[}\]]?', re.IGNORECASE)


def parse_filename_hints(filepath: Path) -> Dict[str, Optional[str]]:
    """Resolution hints read from the raw stem (before title cleaning): an
    explicit IMDb id token (Radarr-style) pins the title; a French release tag
    localizes the TMDB search."""
    stem = filepath.stem
    m = _IMDB_ID_REGEX.search(stem)
    return {"imdb_id": m.group(1).lower() if m else None,
            "lang": "fr" if _FR_TAG_REGEX.search(stem) else None}


def clean_filename_to_title(filepath: Path) -> tuple[str, Optional[str]]:
    """Extract movie title and year from a filename."""
    name = _IMDB_ID_REGEX.sub(' ', filepath.stem)

    # A year in (...) or [...] is explicitly the release year and ends the
    # title — so a bare year *inside* the title survives ("New-york 1997
    # (1981)" → "New york 1997", 1981) instead of truncating the title there.
    year = None
    bracketed = re.search(r'[(\[]\s*(19[0-9]{2}|20[0-2][0-9]|2030)\s*[)\]]', name)
    if bracketed:
        year = bracketed.group(1)
        name = name[:bracketed.start()]

    name = re.sub(r'\[.*?\]', '', name)
    name = re.sub(r'\(.*?\)', '', name)
    name = re.sub(r'[\._-]', ' ', name)

    if not year:
        # A release year always has a title in front of it, so a leading bare
        # year is the title, not the release date.
        for m in re.finditer(r'\b(19[0-9]{2}|20[0-2][0-9]|2030)\b', name):
            if NOISE_REGEX.sub('', name[:m.start()]).strip(' .-_'):
                year = m.group(1)
                name = name[:m.start()].strip()
                break

    name = NOISE_REGEX.sub('', name).strip()
    name = re.sub(r'\s+', ' ', name)
    return name, year


_DASH_FIELD_SPLIT = re.compile(r'\s+-\s*|\s*-\s+')
_YEAR_FIELD_RE = re.compile(r'^(19[0-9]{2}|20[0-2][0-9]|2030)$')


def _clean_field(text: str) -> str:
    text = NOISE_REGEX.sub('', re.sub(r'[\._]', ' ', text))
    return re.sub(r'\s+', ' ', text).strip(' -')


def filename_title_candidates(filepath: Path) -> tuple[List[str], Optional[str]]:
    """Search titles to try for one file, best first, plus the release year.

    ``Collection - YEAR - Title - specs`` names put the real title *after* the
    year, exactly where :func:`clean_filename_to_title` truncates — which leaves
    every entry of a collection sharing one meaningless title. When a whole
    dash-separated field is a year, the field behind it leads instead.
    """
    base, year = clean_filename_to_title(filepath)
    fields = _DASH_FIELD_SPLIT.split(_IMDB_ID_REGEX.sub(' ', filepath.stem))
    candidates = []
    for i, field in enumerate(fields[:-1]):
        if not _YEAR_FIELD_RE.match(field.strip()):
            continue
        tail = _clean_field(fields[i + 1])
        if len(tail) < 3 or not re.search(r'[^\W\d_]', tail):
            break  # nothing but specs behind the year
        prefix = _clean_field(' '.join(fields[:i]))
        candidates = [tail, f"{prefix} {tail}".strip()]
        break
    candidates.append(base)
    return list(dict.fromkeys(c for c in candidates if c)), year


def _year_gap(a, b) -> int:
    """Years apart, 0 when either side is unknown (benefit of the doubt)."""
    try:
        return abs(int(a) - int(b))
    except (TypeError, ValueError):
        return 0


def _get_html_path(movie_path: Path) -> Path:
    return movie_path.parent / f"{movie_path.stem}.html"


async def _resolve_movie(session, cache, title: str, year: Optional[str],
                         lang: Optional[str], log: Callable):
    """``(tmdb_data, imdb_id)`` for one candidate title, both None if unknown."""
    imdb_id = await _cached(
        cache, "wikidata-imdb", f"{title}|{year or ''}",
        lambda: resolve_imdb_id_wikidata(session, title, year, log),
    )
    tmdb_data = None
    if imdb_id:
        tmdb_data = await _cached(
            cache, "movie-tmdb-detail", imdb_id,
            lambda: fetch_tmdb_detail(session, imdb_id, log),
        )
        # A resolved title whose year is far off the filename's is a
        # misresolution (homonyms, translated labels) — drop it and retry
        # by title. Runs post-cache, so stale wrong ids heal themselves.
        if tmdb_data and year and _year_gap(tmdb_data.get("year"), year) > 1:
            log(f"    {D.WARNING} TMDB: '{tmdb_data.get('title')}' ({tmdb_data.get('year')}) "
                f"doesn't match file year {year} — retrying by title.")
            tmdb_data, imdb_id = None, None
    if not tmdb_data and not imdb_id:
        tmdb_data = await _cached(
            cache, "movie-tmdb-search", f"{title}|{year or ''}|{lang or ''}",
            lambda: fetch_tmdb_search(session, title, year, log, lang=lang),
        )
    return tmdb_data, imdb_id


_SIMPLIFY_RE = re.compile(r'[^a-z0-9]+')


def _simplify(text: Optional[str]) -> str:
    return _SIMPLIFY_RE.sub(' ', (text or '').casefold()).strip()


def _looks_truncated(tmdb_title: str, filename_title: str) -> bool:
    """TMDB's canonical title is the filename's title cut short."""
    short, long = _simplify(tmdb_title), _simplify(filename_title)
    return bool(short) and long.startswith(short) and len(long) > len(short) + 4


async def _apply_title_policy(session, cache, data: Dict[str, Any],
                              filename_title: str, log: Callable) -> None:
    """Choose the displayed title; ``lookup_title`` keeps the English form the
    anglophone sources (Rotten Tomatoes, Wikipedia) are indexed under."""
    tmdb_id = data.get("tmdb_id")
    original = data.get("original_title")
    if data.get("original_language") == "fr":
        if original and original != data["title"]:
            data["lookup_title"] = data["title"]
            data["title"] = original
        if tmdb_id:
            fr = await _cached(
                cache, "movie-tmdb-fr", str(tmdb_id),
                lambda: fetch_tmdb_fr_details(session, tmdb_id, log),
            )
            if fr:
                data.update(fr)  # French synopsis and poster artwork
    elif tmdb_id and _looks_truncated(data["title"], filename_title):
        fuller = await _cached(
            cache, "movie-tmdb-alt-title", str(tmdb_id),
            lambda: fetch_tmdb_specific_title(session, tmdb_id, data["title"], filename_title, log),
        )
        if fuller:
            data["lookup_title"] = data["title"]
            data["title"] = fuller

    lookup = data.get("lookup_title")
    if lookup and _simplify(lookup) not in _simplify(data["title"]):
        data["alt_title"] = lookup


async def _ensure_collection(session, cache, data: Dict[str, Any], log: Callable) -> None:
    """Fill in the franchise for entries cached before it was read."""
    tmdb_id = data.get("tmdb_id")
    if "collection" in data or not tmdb_id:
        return
    extra = await _cached(cache, "movie-tmdb-collection", str(tmdb_id),
                          lambda: fetch_tmdb_collection(session, tmdb_id, log))
    data["collection"] = (extra or {}).get("collection")


async def _cached_screenshots(cache, movie_path: Path, temp_dir: Path, max_screenshots: int, log: Callable) -> List[str]:
    """Generate (or reuse cached) screenshot data URIs for a movie file.

    Keyed by the video path + mtime + count, so ffmpeg only runs once per file.
    """
    try:
        mtime = movie_path.stat().st_mtime
    except OSError:
        mtime = 0
    key = f"{movie_path}|{mtime}|{max_screenshots}"
    hit, value = cache.get("movie-screenshots", key)
    if hit:
        return value
    if cache.offline:
        return []  # no ffmpeg in offline mode
    paths = await generate_screenshots_async(movie_path, temp_dir, max_screenshots, log)
    sources = [uri for uri in (optimize_and_encode(p) for p in paths) if uri]
    cache.set("movie-screenshots", key, sources)
    return sources


def _prepare_media_info(info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Display prep, applied outside the cache (so older cached probes get it
    too): identical tracks are collapsed into one entry with a count."""
    if not info:
        return info
    for kind in ("audio", "subtitles"):
        grouped: Dict[tuple, Dict[str, Any]] = {}
        for t in info.get(kind) or []:
            key = (t.get("lang"), t.get("title"), t.get("codec"), t.get("channels"))
            if key in grouped:
                grouped[key]["count"] += 1
            else:
                grouped[key] = {**t, "count": 1}
        info[kind] = list(grouped.values())
    return info


async def _cached_media_info(cache, path: Path, log: Callable) -> Optional[Dict[str, Any]]:
    """ffprobe result (resolution + audio/subtitle tracks), cached by path+mtime."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0
    key = f"{path}|{mtime}"
    hit, value = cache.get("media-info", key)
    if hit:
        return _prepare_media_info(value)
    if cache.offline:
        return None
    value = await probe_media_info(path, log)
    cache.set("media-info", key, value)
    return _prepare_media_info(value)


def _uniq(seq):
    seen = set()
    return [x for x in seq if not (x in seen or seen.add(x))]


_RES_ORDER = ["SD", "720p", "1080p", "1440p", "2160p"]


def _aggregate_media_info(infos) -> Optional[Dict[str, Any]]:
    """Union of per-episode media info for the series-level summary."""
    infos = [i for i in infos if i]
    if not infos:
        return None
    audio = _uniq(a["lang"] for i in infos for a in i.get("audio") or [])
    subs = _uniq(s["lang"] for i in infos for s in i.get("subtitles") or [])
    labels = _uniq((i.get("video") or {}).get("label") for i in infos)
    labels = sorted((l for l in labels if l),
                    key=lambda l: _RES_ORDER.index(l) if l in _RES_ORDER else -1)
    resolution = None
    if labels:
        resolution = labels[0] if len(labels) == 1 else f"{labels[0]}–{labels[-1]}"
    return {"resolution": resolution, "audio_langs": audio, "sub_langs": subs}


async def _resolve_screenshots(
    strategy: str, cache, session: aiohttp.ClientSession, imdb_id: Optional[str],
    video_path: Optional[Path], temp_dir: Path, n: int, log: Callable,
) -> List[str]:
    """Screenshot data URIs, honouring ``--screenshot-source``.

    ``auto``: frames from the local file (they show the actual copy), falling
    back to TMDB backdrops when ffmpeg is unavailable or the file yields
    nothing. ``online``/``ffmpeg`` force one source; ``off`` yields none.
    """
    if strategy == "off":
        return []
    if strategy in ("auto", "ffmpeg") and video_path is not None:
        shots = await _cached_screenshots(cache, video_path, temp_dir, n, log)
        if shots or strategy == "ffmpeg":
            return shots
    if strategy in ("auto", "online") and imdb_id:
        urls = await _cached(cache, "tmdb-stills", f"{imdb_id}|{n}",
                             lambda: fetch_tmdb_stills(session, imdb_id, n, log))
        if urls:
            encoded = [await cached_image_data_uri(session, u, cache, temp_dir, log, f"Still {i + 1}")
                       for i, u in enumerate(urls)]
            return [e for e in encoded if e]
    return []


async def process_movie_file(
    session: aiohttp.ClientSession, movie_path: Path,
    force: bool, max_screenshots: int, debug: bool, log: Callable, cache,
    screenshot_source: str = "auto",
) -> ItemStats:
    """Main processing logic for a single movie file."""
    start_time = time.monotonic()
    stats = ItemStats()
    html_path = _get_html_path(movie_path)
    temp_dir = Path(tempfile.mkdtemp(prefix="movie_info_"))

    try:
        if force and html_path.exists() and not cache.offline:
            html_path.unlink()

        candidates, year = filename_title_candidates(movie_path)
        if not candidates:
            stats.status = "ERROR"
            return stats
        clean_title = candidates[0]

        # Resolve the IMDb id via Wikidata (reliable) and read full metadata from
        # TMDB (mapped via /find); fall back to a TMDB title search. An explicit
        # tt-id in the filename pins the title and skips all guessing.
        hints = parse_filename_hints(movie_path)
        pinned = hints["imdb_id"]
        imdb_id = tmdb_data = None
        if pinned:
            log(f"    {D.SUCCESS_DATA} IMDb id pinned by filename: {pinned}")
            imdb_id = pinned
            tmdb_data = await _cached(
                cache, "movie-tmdb-detail", imdb_id,
                lambda: fetch_tmdb_detail(session, imdb_id, log),
            )
        else:
            for candidate in candidates:
                tmdb_data, imdb_id = await _resolve_movie(
                    session, cache, candidate, year, hints["lang"], log)
                if tmdb_data or imdb_id:
                    clean_title = candidate
                    break

        if tmdb_data:
            await _apply_title_policy(session, cache, tmdb_data, clean_title, log)
            await _ensure_collection(session, cache, tmdb_data, log)
            aggregated_data = tmdb_data
        elif imdb_id:
            # The film is identified (Wikidata gave an IMDb id) but the TMDB lookup
            # failed (no key, or transient error). Don't drop the page — build a
            # partial one from the filename + id, enriched by the other sources.
            log(f"    {D.WARNING} TMDB detail unavailable; building partial page.")
            stats.failed_sources.append("TMDB")
            aggregated_data = {"title": clean_title, "year": year, "imdb_id": imdb_id}
        elif year:
            # Unidentified but carries a year — a real release no source knows
            # under this name. A partial page beats both a wrong film and none.
            log(f"    {D.WARNING} No confident match; building partial page from filename.")
            stats.failed_sources.append("TMDB")
            aggregated_data = {"title": clean_title, "year": year}
        else:
            # Not identified anywhere and no year (e.g. web-only clips) → skip.
            stats.status = "INSUFFICIENT_DATA"
            stats.failed_sources.append("TMDB")
            return stats

        title = aggregated_data["title"]
        # Rotten Tomatoes and the English Wikipedia only know the anglophone
        # title; it also keeps the cache keys stable when the display title
        # switches to French.
        lookup_title = aggregated_data.get("lookup_title") or title
        lang = "fr" if aggregated_data.get("original_language") == "fr" else None
        movie_year = aggregated_data.get("year")
        stats.title = f"{title} ({movie_year})" if movie_year else title
        meta_key = f"{lookup_title}|{movie_year or ''}"
        lang_key = f"{meta_key}|{lang}" if lang else meta_key  # English entries keep their key

        # Build parallel tasks (cached metadata fetches + image work)
        tasks = {
            "wikipedia": _cached(cache, "movie-wikipedia", lang_key,
                                 lambda: fetch_wikipedia_data(title, movie_year, log, lang=lang,
                                                              fallback_title=lookup_title)),
            "rotten_tomatoes": _cached(cache, "movie-rt", meta_key,
                                       lambda: fetch_rotten_tomatoes_data(session, lookup_title, movie_year, log)),
            "youtube_trailer": _cached(cache, "movie-yt-trailer", lang_key,
                                       lambda: fetch_youtube_trailer(session, title, movie_year, log, lang=lang)),
            "youtube_reviews": _cached(cache, "movie-yt-reviews", lang_key,
                                       lambda: fetch_youtube_reviews(session, title, movie_year, log, lang=lang)),
            "screenshots": _resolve_screenshots(screenshot_source, cache, session,
                                                aggregated_data.get("imdb_id"), movie_path,
                                                temp_dir, max_screenshots, log),
            "media_info": _cached_media_info(cache, movie_path, log),
        }
        if aggregated_data.get("poster_url"):
            tasks["poster"] = cached_image_data_uri(session, aggregated_data["poster_url"], cache, temp_dir, log, "Poster")

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        task_results = dict(zip(tasks.keys(), results))

        # Process results
        for source_name, data in task_results.items():
            if isinstance(data, Exception):
                stats.failed_sources.append(source_name)
                if debug:
                    log(f"    {D.ERROR} Task '{source_name}' failed: {data}")
                continue
            if not data:
                # Empty stills/poster/media info aren't a "failed source"
                # (e.g. --screenshot-source off, name-only generation).
                if source_name not in ("screenshots", "poster", "media_info"):
                    stats.failed_sources.append(source_name)
                continue

            if source_name in ("wikipedia", "rotten_tomatoes", "youtube_trailer", "youtube_reviews"):
                aggregated_data.update(data)
            elif source_name == "poster":
                aggregated_data["poster_src"] = data
            elif source_name == "screenshots":
                aggregated_data["screenshot_sources"] = data
            elif source_name == "media_info":
                aggregated_data["media_info"] = data

        # Embed YouTube thumbnails and actor photos as base64 (via disk cache)
        await _embed_youtube_thumbnails(aggregated_data, session, cache, temp_dir, log)
        await _embed_cast_images(aggregated_data, session, cache, temp_dir, log)

        if debug:
            import json
            log(f"    {D.INFO} Final aggregated data for '{aggregated_data['title']}':")
            printable = {k: (v[:80] + "..." if isinstance(v, str) and len(v) > 80 else v) for k, v in aggregated_data.items()}
            log(json.dumps(printable, indent=2, ensure_ascii=False))

        # Render and write
        html_output = render_template(
            "movie_info.html.j2",
            data=aggregated_data,
            generator_name="VideoInfoGenerator",
            version=__version__,
            generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        html_path.write_text(html_output, encoding="utf-8")
        stats.status = "SUCCESS"
        stats.size_bytes = html_path.stat().st_size

    except Exception as e:
        log(f"    {D.ERROR} Unexpected error processing '{movie_path.name}': {e}")
        stats.status = "ERROR"
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        stats.duration_s = time.monotonic() - start_time

    return stats


# --- Series ---

async def _embed_cast_images(data, session, cache, temp_dir, log):
    """Download actor photos to base64 data URIs (via cache) → cast[i]['image_src']."""
    cast = data.get("cast")
    if not cast:
        return
    indices, tasks = [], []
    for i, actor in enumerate(cast):
        url = actor.get("image_url")
        if url and not url.startswith("data:"):
            tasks.append(cached_image_data_uri(session, url, cache, temp_dir, log, f"Cast {i + 1}"))
            indices.append(i)
    if tasks:
        for i, data_uri in zip(indices, await asyncio.gather(*tasks)):
            if data_uri:
                cast[i]["image_src"] = data_uri


async def _embed_youtube_thumbnails(data, session, cache, temp_dir, log):
    """Rewrite YouTube review thumbnail URLs to base64 data URIs (via cache)."""
    reviews = data.get("youtube_reviews")
    if not reviews:
        return
    indices, tasks = [], []
    for i, vid in enumerate(reviews):
        url = vid.get("thumbnail_url", "")
        if url and not url.startswith("data:"):
            tasks.append(cached_image_data_uri(session, url, cache, temp_dir, log, f"Thumb {i + 1}"))
            indices.append(i)
    if tasks:
        for i, data_uri in zip(indices, await asyncio.gather(*tasks)):
            if data_uri:
                reviews[i]["thumbnail_url"] = data_uri


def _build_seasons_view(item, imdb_episodes: Dict[int, list], owned: set,
                        ep_media: Optional[Dict] = None) -> list:
    """Build the per-season episode listing for the templates.

    Uses the metadata episode list when available (marking which episodes the
    user owns); falls back to the locally-present episodes otherwise. Owned
    episodes get their probed audio/subtitle languages attached.
    """

    def tracks(season, number):
        info = (ep_media or {}).get((season, number))
        if not info:
            return {}
        return {"audio_langs": _uniq(a["lang"] for a in info.get("audio") or []),
                "sub_langs": _uniq(s["lang"] for s in info.get("subtitles") or [])}

    seasons_view = []
    for sg in item.seasons:
        imdb_eps = imdb_episodes.get(sg.number)
        if imdb_eps:
            episodes = [{**e, "owned": (sg.number, e["number"]) in owned,
                         **tracks(sg.number, e["number"])} for e in imdb_eps]
        else:
            episodes = [
                {"number": ep.number, "title": f"Episode {ep.number}",
                 "plot": None, "rating": None, "owned": True,
                 **tracks(sg.number, ep.number)}
                for ep in sg.episodes
            ]
        page_link = None
        if sg.html_path and item.root:
            page_link = urllib.parse.quote(os.path.relpath(sg.html_path, item.root))
        seasons_view.append({
            "number": sg.number,
            "episodes": episodes,
            "owned_count": sum(1 for e in episodes if e["owned"]),
            "total_count": len(episodes),
            "page_link": page_link,
        })
    return seasons_view


async def process_series(
    session: aiohttp.ClientSession, item,
    force: bool, max_screenshots: int, debug: bool, log: Callable, cache,
    screenshot_source: str = "auto",
) -> ItemStats:
    """Process a TV series: one series page plus a page per dedicated season folder."""
    start_time = time.monotonic()
    stats = ItemStats()
    temp_dir = Path(tempfile.mkdtemp(prefix="series_info_"))

    try:
        if force and not cache.offline:
            for p in item.all_html_paths():
                if p.exists():
                    p.unlink()

        # One TVmaze call yields show metadata + all episodes + cast (no flaky
        # IMDb search, no per-season requests).
        meta = await _cached(
            cache, "tvmaze-series", item.title,
            lambda: fetch_tvmaze_series(session, item.title, log),
        )
        if not meta:
            stats.status = "INSUFFICIENT_DATA"
            stats.failed_sources.append("TVmaze")
            return stats

        # JSON (the disk cache) turns dict keys into strings, so a cached run has
        # str season keys while a fresh fetch has int — normalize to int either way.
        stats.title = f"{meta['title']} ({meta['year']})" if meta.get("year") else meta["title"]
        episodes_by_season = {int(k): v for k, v in (meta.get("episodes_by_season") or {}).items()}
        owned = item.owned_episodes()
        meta_key = f"{meta['title']}|{meta.get('year') or ''}"
        first_ep = item.seasons[0].episodes[0].path if item.seasons and item.seasons[0].episodes else None

        tasks = {
            "wikipedia": _cached(cache, "movie-wikipedia-series", meta_key,
                                 lambda: fetch_wikipedia_data(meta["title"], meta.get("year"), log, kind="series")),
            "rotten_tomatoes": _cached(cache, "series-rt", meta_key,
                                       lambda: fetch_rotten_tomatoes_data(session, meta["title"], meta.get("year"), log, kind="series")),
            "youtube_trailer": _cached(cache, "movie-yt-trailer", meta_key,
                                       lambda: fetch_youtube_trailer(session, meta["title"], meta.get("year"), log)),
            "youtube_reviews": _cached(cache, "movie-yt-reviews", meta_key,
                                       lambda: fetch_youtube_reviews(session, meta["title"], meta.get("year"), log)),
        }
        if meta.get("imdb_id"):
            tasks["tmdb_rating"] = _cached(cache, "tmdb-rating", meta["imdb_id"],
                                           lambda: fetch_tmdb_rating(session, meta["imdb_id"], log))
        if screenshot_source != "off":
            tasks["screenshots"] = _resolve_screenshots(screenshot_source, cache, session,
                                                        meta.get("imdb_id"), first_ep,
                                                        temp_dir, max_screenshots, log)
        if meta.get("poster_url"):
            tasks["poster"] = cached_image_data_uri(session, meta["poster_url"], cache, temp_dir, log, "Poster")

        results = dict(zip(tasks.keys(), await asyncio.gather(*tasks.values(), return_exceptions=True)))

        data = {k: v for k, v in meta.items() if k != "episodes_by_season"}
        # The TMDB badge must show the TMDB rating, not TVmaze's — clear it and let
        # the tmdb_rating task fill it (no mislabeled badge if that lookup fails).
        data["rating"] = None
        for name, val in results.items():
            if isinstance(val, Exception):
                if debug:
                    log(f"    {D.ERROR} Task '{name}' failed: {val}")
                stats.failed_sources.append(name)
                continue
            if not val:
                if name not in ("screenshots", "poster"):
                    stats.failed_sources.append(name)
                continue
            if name in ("wikipedia", "rotten_tomatoes", "youtube_trailer", "youtube_reviews", "tmdb_rating"):
                data.update(val)
            elif name == "poster":
                data["poster_src"] = val
            elif name == "screenshots":
                data["screenshot_sources"] = val

        await _embed_youtube_thumbnails(data, session, cache, temp_dir, log)
        await _embed_cast_images(data, session, cache, temp_dir, log)

        # Tracks are per episode, so probe every owned file (quietly — cached
        # by path+mtime); the series page shows the union.
        ep_paths = {(sg.number, ep.number): ep.path
                    for sg in item.seasons for ep in sg.episodes}
        quiet = lambda *_: None
        probes = await asyncio.gather(
            *(_cached_media_info(cache, p, quiet) for p in ep_paths.values())
        ) if ep_paths else []
        ep_media = {k: v for k, v in zip(ep_paths, probes) if v}
        if ep_media:
            log(f"    {D.SUCCESS_DATA} ffprobe: media info for {len(ep_media)}/{len(ep_paths)} episode(s)")
            data["media_summary"] = _aggregate_media_info(ep_media.values())

        seasons_view = _build_seasons_view(item, episodes_by_season, owned, ep_media)
        data["seasons"] = seasons_view
        data["owned_episode_count"] = len(owned)
        data["total_seasons"] = len(episodes_by_season) or len(seasons_view)

        common = dict(
            generator_name="VideoInfoGenerator",
            version=__version__,
            generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )

        # Series page
        item.html_path.write_text(
            render_template("series_info.html.j2", data=data, **common),
            encoding="utf-8",
        )
        total_bytes = item.html_path.stat().st_size

        # Per-season pages (only seasons that live in a dedicated folder)
        seasons_by_num = {s["number"]: s for s in seasons_view}
        for sg in item.seasons:
            if not sg.html_path:
                continue
            sview = seasons_by_num[sg.number]
            season_data = {
                "series_title": data["title"],
                "season_number": sg.number,
                "poster_src": data.get("poster_src"),
                "episodes": sview["episodes"],
                "owned_count": sview["owned_count"],
                "total_count": sview["total_count"],
                "series_page_link": urllib.parse.quote(os.path.relpath(item.html_path, sg.folder)),
                "imdb_id": data.get("imdb_id"),
            }
            sg.html_path.write_text(
                render_template("season_info.html.j2", data=season_data, **common),
                encoding="utf-8",
            )
            total_bytes += sg.html_path.stat().st_size

        stats.status = "SUCCESS"
        stats.size_bytes = total_bytes

    except Exception as e:
        log(f"    {D.ERROR} Unexpected error processing series '{item.title}': {e}")
        stats.status = "ERROR"
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        stats.duration_s = time.monotonic() - start_time

    return stats
