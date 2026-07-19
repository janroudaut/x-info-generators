import asyncio
import re
import shutil
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

import aiohttp
from bs4 import BeautifulSoup

from ..display import DisplayMode as D
from ..images import cached_image_data_uri
from ..processing import ItemStats
from ..templates import render_template
from .. import __version__
from .fetchers import (
    fetch_steam_data, fetch_steam_user_reviews,
    fetch_metacritic_data, fetch_wikipedia_data, fetch_mobygames_data,
)

DEFAULT_HTML_FILENAME = "00_GAME_INFO.html"


async def _cached(cache, namespace, key, factory):
    """Return a cached fetch result, or call ``factory()`` (a lambda) on a miss.

    Using a factory rather than a pre-created coroutine avoids "coroutine was
    never awaited" warnings when the cache hits.
    """
    hit, value = cache.get(namespace, key)
    if hit:
        return value
    if cache.offline:
        return None  # never hit the network in offline mode
    value = await factory()
    cache.set(namespace, key, value)
    return value


# Trademark/copyright symbols and "smart" punctuation break Steam/Wikipedia/
# Metacritic lookups (e.g. Wikipedia suggests "logo party" for "LEGO® Party!").
_SEARCH_NOISE = str.maketrans({
    "™": "", "®": "", "©": "", "℠": "",
    "’": "'", "‘": "'", "“": '"', "”": '"',
})


def sanitize_search_term(name: str) -> str:
    """Strip trademark symbols and normalize smart quotes for cleaner lookups."""
    return re.sub(r"\s{2,}", " ", name.translate(_SEARCH_NOISE)).strip()


def clean_game_title(raw_name: str) -> str:
    """Cleans a directory name to get a better game title for searching."""
    name = re.sub(r'\[.*?\]', '', raw_name)
    common_tags = [
        r'\b(CODEX|CPY|PLAZA|SKIDROW|RELOADED|GOG|FLT|PROPHET|RUNE|TENOKE|GOLDGERG|ELAMIGOS|FITGIRL|DODI)\b',
        r'\b(REPACK|RIP|MULTi\d+|LiMiTED|EDITION|COMPLETE|DEFINITIVE|DELUXE|STEAMWORKSFiX|UNLOCKER)\b',
        r'\((Ultimate|Collector\'s|GOTY|Game of the Year|Anniversary|Day One|Preorder|Bonus|DLC|Update)\s*Edition\)',
        r'Build\s*\d+', r'v\d+\.\d+(\.\d+)?',
        r'\((DE|EN|FR|ES|IT|RU|PL|CZ|HU|JP|KR|CN|TW)\)', r'\(MULTi\d*\)',
    ]
    for tag_pattern in common_tags:
        name = re.sub(tag_pattern, '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
    name = sanitize_search_term(name)
    name = name.replace('.', ' ').replace('_', ' ')
    # Windows dir names can't contain ":", so a subtitle "Game: Subtitle" is stored
    # as "Game - Subtitle". Restore the colon: Steam search returns nothing for the
    # " - " form but matches on ":".
    name = re.sub(r'\s-\s', ': ', name)
    name = re.sub(r'\s{2,}', ' ', name).strip()
    name = name.strip(' -_')
    name = re.sub(r'\b(dlc|goty|hd|vr)\b', lambda m: m.group(1).upper(), name, flags=re.IGNORECASE)
    return name if name else raw_name


def _merge_data(target: Dict[str, Any], source: Optional[Dict[str, Any]]):
    """Merge source data into target, avoiding duplicates in lists."""
    if not source:
        return
    for key, value in source.items():
        if value is None or (isinstance(value, (str, list, dict)) and not value):
            continue
        if key in ("screenshots", "developers", "genres", "publishers", "platforms", "reviews"):
            if key not in target or not isinstance(target[key], list):
                target[key] = []
            current_list = target[key]
            if isinstance(value, list):
                for item in value:
                    if key == "reviews" and isinstance(item, dict):
                        if not any(r.get("source") == item.get("source") and r.get("snippet") == item.get("snippet") for r in current_list):
                            current_list.append(item)
                    elif key != "reviews":
                        str_item = str(item)
                        if not any(str_item.lower() == str(existing).lower() for existing in current_list):
                            current_list.append(item)
        elif key not in target or not target[key]:
            target[key] = value
        elif key == "name" and isinstance(value, str) and len(value) > len(target.get("name", "")):
            target[key] = value
        elif key == "description_html" and isinstance(value, str) and len(value) > len(target.get("description_html", "")):
            target[key] = value
        elif key == "description_text" and isinstance(value, str) and not target.get("description_html") and len(value) > len(target.get("description_text", "")):
            target[key] = value


async def _download_and_process_header(
    session: aiohttp.ClientSession, header_url: Optional[str],
    temp_dir: Path, log: Callable, cache,
) -> Optional[str]:
    """Return base64 data URI for the header image (via disk cache)."""
    if not header_url:
        return None
    log(f"  {D.INFO} Processing header image...")
    return await cached_image_data_uri(session, header_url, cache, temp_dir, log, "Header")


async def _download_and_process_screenshots(
    session: aiohttp.ClientSession, screenshot_urls: List[str],
    temp_dir: Path, max_screenshots: int, log: Callable, cache,
) -> List[str]:
    """Return base64 data URIs for screenshots (via disk cache)."""
    urls = screenshot_urls[:max_screenshots]
    if not urls:
        return []
    log(f"  {D.INFO} Processing {len(urls)} screenshots...")

    results = await asyncio.gather(*[
        cached_image_data_uri(session, url, cache, temp_dir, log, f"Screenshot {i + 1}/{len(urls)}")
        for i, url in enumerate(urls)
    ])
    sources = [uri for uri in results if uri]
    log(f"  {D.SUCCESS_DATA} Finished screenshot processing. Successfully processed {len(sources)}/{len(urls)}.")
    return sources


async def _download_and_rewrite_embedded_images(
    session: aiohttp.ClientSession, html_content: str, base_url: Optional[str],
    temp_dir: Path, log: Callable, cache,
) -> str:
    """Download images in HTML, replace with base64 (via disk cache), return modified HTML."""
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "lxml")
    embedded_dir = temp_dir / "embedded"

    # Clean video tags: remove fixed dimensions and autoplay, embed posters
    videos = soup.find_all("video")
    poster_tags = []
    poster_tasks = []
    for vi, video_tag in enumerate(videos):
        for attr in ("width", "height", "style", "autoplay"):
            if video_tag.has_attr(attr):
                del video_tag[attr]
        poster_url = video_tag.get("poster")
        if poster_url and not poster_url.startswith("data:"):
            poster_tasks.append(cached_image_data_uri(session, poster_url, cache, embedded_dir, log, f"Poster {vi + 1}/{len(videos)}"))
            poster_tags.append(video_tag)

    if poster_tasks:
        log(f"  {D.INFO} Processing {len(poster_tasks)} video posters...")
        for tag, data_uri in zip(poster_tags, await asyncio.gather(*poster_tasks)):
            if data_uri:
                tag["poster"] = data_uri

    images = soup.find_all("img")
    if not images:
        return str(soup)

    log(f"  {D.INFO} Processing {len(images)} embedded images...")
    img_tags = []
    img_tasks = []
    for i, img_tag in enumerate(images):
        src = img_tag.get("src")
        if not src or src.startswith("data:"):
            continue
        img_url = src
        if base_url and not urllib.parse.urlparse(src).scheme:
            img_url = urllib.parse.urljoin(base_url, src)
        img_tasks.append(cached_image_data_uri(session, img_url, cache, embedded_dir, log, f"Embed {i + 1}/{len(images)}"))
        img_tags.append(img_tag)

    count = 0
    for tag, data_uri in zip(img_tags, await asyncio.gather(*img_tasks)):
        if data_uri:
            tag["src"] = data_uri
            for attr in ("width", "height", "style"):
                if tag.has_attr(attr):
                    del tag[attr]
            count += 1

    log(f"  {D.SUCCESS_DATA} Finished embedded image processing. Successfully processed {count}/{len(images)}.")
    return str(soup)


async def process_game_directory(
    session: aiohttp.ClientSession, game_dir_path: Path,
    game_title_cleaned: str, force: bool, max_screenshots: int, log: Callable, cache,
) -> ItemStats:
    """Main processing logic for a single game directory."""
    start_time = time.monotonic()
    stats = ItemStats()
    temp_dir = Path(tempfile.mkdtemp(prefix="game_info_"))

    try:
        output_html_file = game_dir_path / DEFAULT_HTML_FILENAME
        if force and output_html_file.exists() and not cache.offline:
            output_html_file.unlink()

        aggregated_data: Dict[str, Any] = {"name": game_title_cleaned}

        # --- Steam data (first, to get app_id for later) ---
        steam_data = await _cached(
            cache, "game-steam", game_title_cleaned,
            lambda: fetch_steam_data(session, game_title_cleaned, log),
        )
        steam_app_id = None
        if steam_data:
            _merge_data(aggregated_data, steam_data)
            stats.sources_summary["Steam"] = f"AppID: {steam_data.get('app_id', 'N/A')}, {len(steam_data.get('screenshots', []))} screenshots"
            steam_app_id = steam_data.get("app_id")
            if steam_data.get("header_image_url"):
                header_src = await _download_and_process_header(session, steam_data["header_image_url"], temp_dir, log, cache)
                if header_src:
                    aggregated_data["header_image_src"] = header_src
        else:
            stats.failed_sources.append("Steam")

        # Steam's name re-introduces ™/® symbols, which break Wikipedia/Metacritic.
        current_best_name = sanitize_search_term(aggregated_data.get("name", game_title_cleaned))
        stats.title = current_best_name

        # --- Parallel fetches ---
        fetch_tasks = {
            "Metacritic": _cached(cache, "game-metacritic", current_best_name,
                                  lambda: fetch_metacritic_data(session, current_best_name, log)),
            "Wikipedia": _cached(cache, "game-wikipedia", current_best_name,
                                 lambda: fetch_wikipedia_data(session, current_best_name, log)),
            "MobyGames": _cached(cache, "game-mobygames", current_best_name,
                                 lambda: fetch_mobygames_data(session, current_best_name, log)),
        }
        if steam_app_id:
            fetch_tasks["Steam Reviews"] = _cached(cache, "game-steam-reviews", str(steam_app_id),
                                                   lambda: fetch_steam_user_reviews(session, steam_app_id, log))

        results = await asyncio.gather(*fetch_tasks.values(), return_exceptions=True)
        source_data_map = dict(zip(fetch_tasks.keys(), results))

        # --- Merge non-review data ---
        for source_name, result_data in source_data_map.items():
            if source_name == "Steam Reviews":
                continue
            if isinstance(result_data, Exception) or not result_data:
                if source_name not in stats.failed_sources:
                    stats.failed_sources.append(source_name)
                continue

            if source_name == "Metacritic":
                score = result_data.get("metacritic_score", "N/A")
                reviews_count = len(result_data.get("reviews", []))
                stats.sources_summary["Metacritic"] = f"Score: {score}, {reviews_count} reviews"
                data_without_reviews = {k: v for k, v in result_data.items() if k != "reviews"}
                _merge_data(aggregated_data, data_without_reviews)
            else:
                stats.sources_summary[source_name] = "Data found"
                _merge_data(aggregated_data, result_data)

        # --- Combine reviews (Metacritic priority, then Steam) ---
        final_reviews: List[Dict] = []
        metacritic_data = source_data_map.get("Metacritic")
        steam_reviews_data = source_data_map.get("Steam Reviews")

        metacritic_reviews = metacritic_data.get("reviews", []) if isinstance(metacritic_data, dict) else []
        steam_user_reviews = steam_reviews_data if isinstance(steam_reviews_data, list) else []

        if metacritic_reviews:
            final_reviews.extend(metacritic_reviews[:3])
        reviews_needed = 3 - len(final_reviews)
        if reviews_needed > 0 and steam_user_reviews:
            final_reviews.extend(steam_user_reviews[:reviews_needed])
        if final_reviews:
            aggregated_data["reviews"] = final_reviews
            if "Steam Reviews" in fetch_tasks and steam_user_reviews:
                stats.sources_summary["Steam Reviews"] = f"{len(steam_user_reviews)} reviews found"
        elif "Steam Reviews" in fetch_tasks:
            stats.failed_sources.append("Steam Reviews")

        # --- Image processing ---
        if aggregated_data.get("description_html"):
            aggregated_data["description_html"] = await _download_and_rewrite_embedded_images(
                session, aggregated_data["description_html"],
                aggregated_data.get("base_url_for_description_images"),
                temp_dir, log, cache,
            )

        screenshot_urls = aggregated_data.get("screenshots", [])
        if screenshot_urls:
            aggregated_data["screenshot_sources"] = await _download_and_process_screenshots(
                session, screenshot_urls, temp_dir, max_screenshots, log, cache,
            )

        # --- Check sufficient data ---
        if (not aggregated_data.get("description_html")
                and not aggregated_data.get("description_text")
                and not aggregated_data.get("screenshot_sources")
                and not aggregated_data.get("header_image_src")):
            stats.status = "INSUFFICIENT_DATA"
            return stats

        # --- Render and write ---
        html_output = render_template(
            "game_info.html.j2",
            data=aggregated_data,
            generator_name="GameInfoGenerator",
            version=__version__,
            generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        output_html_file.write_text(html_output, encoding="utf-8")
        stats.status = "SUCCESS"
        stats.size_bytes = output_html_file.stat().st_size

    except Exception as e:
        log(f"  {D.ERROR} Unexpected error processing '{game_dir_path.name}': {e}")
        stats.status = "ERROR"
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        stats.duration_s = time.monotonic() - start_time

    return stats
