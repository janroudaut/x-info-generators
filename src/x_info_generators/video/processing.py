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
    fetch_imdb_data, fetch_imdb_detail, fetch_imdb_rating, resolve_imdb_id_wikidata,
    generate_screenshots_async,
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
                if file.lower().endswith(VIDEO_EXTENSIONS):
                    movie_files.append(Path(root) / file)
    return movie_files


def clean_filename_to_title(filepath: Path) -> tuple[str, Optional[str]]:
    """Extract movie title and year from a filename."""
    name = filepath.stem
    name = re.sub(r'\[.*?\]', '', name)
    name = re.sub(r'\(.*?\)', '', name)
    name = re.sub(r'[\._-]', ' ', name)

    year_match = re.search(r'\b(19[0-9]{2}|20[0-2][0-9]|2030)\b', name)
    year = None
    if year_match:
        year = year_match.group(1)
        name = name[:year_match.start()].strip()

    name = NOISE_REGEX.sub('', name).strip()
    name = re.sub(r'\s+', ' ', name)
    return name, year


def _get_html_path(movie_path: Path) -> Path:
    return movie_path.parent / f"{movie_path.stem}.html"


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


async def process_movie_file(
    session: aiohttp.ClientSession, movie_path: Path,
    force: bool, max_screenshots: int, debug: bool, log: Callable, cache,
) -> ItemStats:
    """Main processing logic for a single movie file."""
    start_time = time.monotonic()
    stats = ItemStats()
    html_path = _get_html_path(movie_path)
    temp_dir = Path(tempfile.mkdtemp(prefix="movie_info_"))

    try:
        if force and html_path.exists() and not cache.offline:
            html_path.unlink()

        clean_title, year = clean_filename_to_title(movie_path)
        if not clean_title:
            stats.status = "ERROR"
            return stats

        # Resolve the IMDb id via Wikidata (reliable) and read full metadata from
        # the dependable /titles/{id} endpoint; fall back to the flaky IMDb search.
        imdb_id = await _cached(
            cache, "wikidata-imdb", f"{clean_title}|{year or ''}",
            lambda: resolve_imdb_id_wikidata(session, clean_title, year, log),
        )
        if imdb_id:
            imdb_data = await _cached(
                cache, "movie-imdb-detail", imdb_id,
                lambda: fetch_imdb_detail(session, imdb_id, log),
            )
        else:
            imdb_data = await _cached(
                cache, "movie-imdb", f"{clean_title}|{year or ''}",
                lambda: fetch_imdb_data(session, clean_title, year, log),
            )

        if imdb_data:
            aggregated_data = imdb_data
        elif imdb_id:
            # The film is identified (Wikidata gave an IMDb id) but imdbapi's detail
            # endpoint failed (it 500s transiently). Don't drop the page — build a
            # partial one from the filename + id, enriched by the other sources.
            log(f"    {D.WARNING} IMDb detail unavailable; building partial page.")
            stats.failed_sources.append("IMDb")
            aggregated_data = {"title": clean_title, "year": year, "imdb_id": imdb_id}
        else:
            # Not identified anywhere (e.g. web-only clips) → skip.
            stats.status = "INSUFFICIENT_DATA"
            stats.failed_sources.append("IMDb")
            return stats

        title = aggregated_data["title"]
        movie_year = aggregated_data.get("year")
        meta_key = f"{title}|{movie_year or ''}"

        # Build parallel tasks (cached metadata fetches + image work)
        tasks = {
            "wikipedia": _cached(cache, "movie-wikipedia", meta_key,
                                 lambda: fetch_wikipedia_data(title, movie_year, log)),
            "rotten_tomatoes": _cached(cache, "movie-rt", meta_key,
                                       lambda: fetch_rotten_tomatoes_data(session, title, movie_year, log)),
            "youtube_trailer": _cached(cache, "movie-yt-trailer", meta_key,
                                       lambda: fetch_youtube_trailer(session, title, movie_year, log)),
            "youtube_reviews": _cached(cache, "movie-yt-reviews", meta_key,
                                       lambda: fetch_youtube_reviews(session, title, movie_year, log)),
            "screenshots": _cached_screenshots(cache, movie_path, temp_dir, max_screenshots, log),
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
                stats.failed_sources.append(source_name)
                continue

            if source_name in ("wikipedia", "rotten_tomatoes", "youtube_trailer", "youtube_reviews"):
                aggregated_data.update(data)
            elif source_name == "poster":
                aggregated_data["poster_src"] = data
            elif source_name == "screenshots":
                aggregated_data["screenshot_sources"] = data

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


def _build_seasons_view(item, imdb_episodes: Dict[int, list], owned: set) -> list:
    """Build the per-season episode listing for the templates.

    Uses the IMDb episode list when available (marking which episodes the user
    owns); falls back to the locally-present episodes otherwise.
    """
    seasons_view = []
    for sg in item.seasons:
        imdb_eps = imdb_episodes.get(sg.number)
        if imdb_eps:
            episodes = [{**e, "owned": (sg.number, e["number"]) in owned} for e in imdb_eps]
        else:
            episodes = [
                {"number": ep.number, "title": f"Episode {ep.number}",
                 "plot": None, "rating": None, "owned": True}
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
            tasks["imdb_rating"] = _cached(cache, "imdb-rating", meta["imdb_id"],
                                           lambda: fetch_imdb_rating(session, meta["imdb_id"], log))
        if first_ep:
            tasks["screenshots"] = _cached_screenshots(cache, first_ep, temp_dir, max_screenshots, log)
        if meta.get("poster_url"):
            tasks["poster"] = cached_image_data_uri(session, meta["poster_url"], cache, temp_dir, log, "Poster")

        results = dict(zip(tasks.keys(), await asyncio.gather(*tasks.values(), return_exceptions=True)))

        data = {k: v for k, v in meta.items() if k != "episodes_by_season"}
        # The IMDb badge must show the IMDb rating, not TVmaze's — clear it and let
        # the imdb_rating task fill it (no mislabeled badge if that lookup fails).
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
            if name in ("wikipedia", "rotten_tomatoes", "youtube_trailer", "youtube_reviews", "imdb_rating"):
                data.update(val)
            elif name == "poster":
                data["poster_src"] = val
            elif name == "screenshots":
                data["screenshot_sources"] = val

        await _embed_youtube_thumbnails(data, session, cache, temp_dir, log)
        await _embed_cast_images(data, session, cache, temp_dir, log)

        seasons_view = _build_seasons_view(item, episodes_by_season, owned)
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
