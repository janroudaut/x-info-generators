#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
import urllib.parse
import base64
import mimetypes

import aiohttp
import wikipedia
from bs4 import BeautifulSoup
from tqdm.asyncio import tqdm

# --- Configuration & Constants ---
SCRIPT_VERSION = "0.8.7"
DEFAULT_HTML_FILENAME = "game_info.html"
# Persistent storage if not inlining
SCREENSHOTS_DIR_NAME = "screenshots"
EMBEDDED_CONTENT_DIR_NAME = Path(SCREENSHOTS_DIR_NAME) / "embedded_content"
HEADER_IMAGE_FILENAME = "header_image.jpg"
# Temporary storage during generation for a game
GAME_TEMP_IMAGES_SUBDIR = "_temp_processing_images"
HEADER_IMAGE_TEMP_FILENAME = "_temp_header_image"
USER_AGENT = f"GameInfoGenerator/{SCRIPT_VERSION} (I'm a kind scraper, called manually and used for personal use <3)"

# --- Emojis and Colors (can be disabled) ---
EMOJI_ROCKET = "🚀"
EMOJI_CLEAN = "🧹"
EMOJI_PROCESS = "✨"
EMOJI_SKIP = "⏩"
EMOJI_SUCCESS_HTML = "📄"
EMOJI_SUCCESS_DATA = "✔️"
EMOJI_DOWNLOAD = "🖼️"
EMOJI_ERROR = "❌"
EMOJI_WARNING = "⚠️"
EMOJI_INFO = "ℹ️"
EMOJI_QUERY = "📡"
EMOJI_SHRUG = "🤷"
EMOJI_PARTY = "🎉"
EMOJI_SUBDIR = "📁"
EMOJI_STATS = "📊"
EMOJI_CLOCK = "⏱️"

C_YELLOW = '\033[93m'
C_RED = '\033[91m'
C_RESET = '\033[0m'

def setup_display_mode(no_color_flag: bool):
    """Disables emojis and colors globally based on the flag."""
    global EMOJI_ROCKET, EMOJI_CLEAN, EMOJI_PROCESS, EMOJI_SKIP, EMOJI_SUCCESS_HTML, \
           EMOJI_SUCCESS_DATA, EMOJI_DOWNLOAD, EMOJI_ERROR, EMOJI_WARNING, EMOJI_INFO, \
           EMOJI_SHRUG, EMOJI_PARTY, EMOJI_SUBDIR, EMOJI_STATS, EMOJI_CLOCK, EMOJI_QUERY, \
           C_YELLOW, C_RED, C_RESET

    if no_color_flag:
        EMOJI_ROCKET, EMOJI_CLEAN, EMOJI_PROCESS, EMOJI_SKIP, EMOJI_SUCCESS_HTML, \
        EMOJI_SUCCESS_DATA, EMOJI_DOWNLOAD, EMOJI_ERROR, EMOJI_WARNING, EMOJI_INFO, \
        EMOJI_SHRUG, EMOJI_PARTY, EMOJI_SUBDIR, EMOJI_STATS, EMOJI_CLOCK, EMOJI_QUERY = \
        ("",) * 16

    is_tty = sys.stdout.isatty()
    if no_color_flag or not is_tty:
        C_YELLOW, C_RED, C_RESET = ("",) * 3

# --- Utility Functions ---

def clean_game_title(raw_name: str) -> str:
    """Cleans a directory name to get a better game title for searching."""
    name = re.sub(r'\[.*?\]', '', raw_name)
    common_tags = [
        r'\b(CODEX|CPY|PLAZA|SKIDROW|RELOADED|GOG|FLT|PROPHET|RUNE|TENOKE|GOLDGERG|ELAMIGOS|FITGIRL|DODI)\b',
        r'\b(REPACK|RIP|MULTi\d+|LiMiTED|EDITION|COMPLETE|DEFINITIVE|DELUXE|STEAMWORKSFiX|UNLOCKER)\b',
        r'\((Ultimate|Collector\'s|GOTY|Game of the Year|Anniversary|Day One|Preorder|Bonus|DLC|Update)\s*Edition\)',
        r'Build\s*\d+', r'v\d+\.\d+(\.\d+)?', r'\((DE|EN|FR|ES|IT|RU|PL|CZ|HU|JP|KR|CN|TW)\)', r'\(MULTi\d*\)',
    ]
    for tag_pattern in common_tags: name = re.sub(tag_pattern, '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*\(.*?\)\s*', ' ', name); name = name.replace('.', ' ').replace('_', ' ')
    name = re.sub(r'\s-\s', ' - ', name); name = re.sub(r'\s{2,}', ' ', name).strip()
    name = name.strip(' -_'); name = re.sub(r'\b(dlc|goty|hd|vr)\b', lambda m: m.group(1).upper(), name, flags=re.IGNORECASE)
    return name if name else raw_name

def format_bytes(size_bytes: int) -> str:
    """Formats bytes into a human-readable string (KB, MB, etc.)."""
    if size_bytes < 1024:
        return f"{size_bytes} bytes"
    elif size_bytes < 1024**2:
        return f"{size_bytes/1024:.2f} KB"
    elif size_bytes < 1024**3:
        return f"{size_bytes/1024**2:.2f} MB"
    else:
        return f"{size_bytes/1024**3:.2f} GB"

def encode_image_to_base64_data_uri(image_path: Path) -> Optional[str]:
    """Encodes an image file to a Base64 data URI."""
    if not image_path.exists() or not image_path.is_file(): return None
    try:
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type or not mime_type.startswith("image/"):
            ext_to_mime = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'}
            mime_type = ext_to_mime.get(image_path.suffix.lower(), 'image/octet-stream')
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
        return f"data:{mime_type};base64,{encoded_string}"
    except Exception: return None

async def download_file_with_progress(session: aiohttp.ClientSession, url: str, temp_file_path: Path, log_method, file_type="file") -> bool:
    """Downloads a file from a URL to a temporary path with a TQDM progress bar."""
    try:
        temp_file_path.parent.mkdir(parents=True, exist_ok=True)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as response:
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            
            with tqdm(
                total=total_size,
                unit='B',
                unit_scale=True,
                unit_divisor=1024,
                desc=f"  {EMOJI_DOWNLOAD} {file_type}",
                leave=False,
                bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]'
            ) as pbar:
                with open(temp_file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        f.write(chunk)
                        pbar.update(len(chunk))
        return True
    except aiohttp.ClientError as e: 
        if e.status not in [403, 404]: # Filter out common "Not Found" or "Forbidden" errors from logs
            log_method(f"      {EMOJI_ERROR} Download error for {url}: {e}")
    except asyncio.TimeoutError: log_method(f"      {EMOJI_ERROR} Timeout downloading {url}")
    except Exception as e: log_method(f"      {EMOJI_ERROR} Unexpected download error for {url}: {e}")
    return False

async def download_header_image_async(session: aiohttp.ClientSession, header_image_url: Optional[str], game_dir_path: Path, game_temp_images_dir: Path, inline_images: bool, log_method) -> tuple[Optional[str], int]:
    """Downloads and processes the header image. Returns (path_or_uri, download_count)."""
    if not header_image_url: return None, 0
    log_method(f"  {EMOJI_INFO} Processing header image...")

    file_extension = Path(urllib.parse.urlparse(header_image_url).path).suffix.lower() or ".jpg"
    if file_extension not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']: file_extension = '.jpg'

    temp_header_file_path = game_temp_images_dir / f"{HEADER_IMAGE_TEMP_FILENAME}{file_extension}"

    if await download_file_with_progress(session, header_image_url, temp_header_file_path, log_method, "Header"):
        if inline_images:
            data_uri = encode_image_to_base64_data_uri(temp_header_file_path)
            if data_uri: return data_uri, 1
            else: log_method(f"      {EMOJI_ERROR} Failed to encode header image: {header_image_url}"); return None, 0
        else:
            final_header_path = game_dir_path / HEADER_IMAGE_FILENAME
            try:
                shutil.move(str(temp_header_file_path), str(final_header_path))
                return final_header_path.name, 1
            except Exception as e:
                log_method(f"      {EMOJI_ERROR} Failed to move header image {temp_header_file_path} to {final_header_path}: {e}")
                return None, 0
    return None, 0

async def download_screenshots(session: aiohttp.ClientSession, screenshot_urls: List[str], game_dir_path: Path, game_temp_images_dir: Path, inline_images: bool, game_display_name: str, log_method) -> tuple[List[str], int]:
    """Downloads and processes screenshots concurrently. Returns (list_of_paths, download_count)."""
    if not screenshot_urls: return [], 0
    log_method(f"  {EMOJI_INFO} Processing {len(screenshot_urls)} screenshots for {game_display_name}...")

    tasks = []
    temp_paths = []
    for i, url in enumerate(screenshot_urls):
        filename_hint = Path(urllib.parse.urlparse(url).path).stem if urllib.parse.urlparse(url).path else f"sc_{i}"
        file_extension = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".jpg"
        if file_extension not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']: file_extension = '.jpg'
        
        temp_path = game_temp_images_dir / f"screenshot_{i+1}_{filename_hint[:20]}{file_extension}"
        tasks.append(download_file_with_progress(session, url, temp_path, log_method, f"Screenshot {i+1}/{len(screenshot_urls)}"))
        temp_paths.append(temp_path)

    download_results = await asyncio.gather(*tasks)
    
    processed_image_paths_or_uris: List[str] = []
    downloaded_count = 0
    for i, success in enumerate(download_results):
        if success:
            temp_path = temp_paths[i]
            if inline_images:
                data_uri = encode_image_to_base64_data_uri(temp_path)
                if data_uri:
                    processed_image_paths_or_uris.append(data_uri)
                    downloaded_count += 1
                else: log_method(f"      {EMOJI_ERROR} Failed to encode screenshot from: {temp_path}")
            else:
                persistent_screenshots_dir = game_dir_path / SCREENSHOTS_DIR_NAME
                persistent_screenshots_dir.mkdir(parents=True, exist_ok=True)
                final_screenshot_path = persistent_screenshots_dir / temp_path.name
                try:
                    shutil.move(str(temp_path), str(final_screenshot_path))
                    processed_image_paths_or_uris.append(str(Path(SCREENSHOTS_DIR_NAME) / final_screenshot_path.name).replace(os.sep, '/'))
                    downloaded_count += 1
                except Exception as e: log_method(f"      {EMOJI_ERROR} Failed to move screenshot {temp_path} to {final_screenshot_path}: {e}")

    log_method(f"  {EMOJI_SUCCESS_DATA} Finished screenshot processing. Successfully processed {downloaded_count}/{len(screenshot_urls)}.")
    return processed_image_paths_or_uris, downloaded_count

async def download_and_rewrite_embedded_images(session: aiohttp.ClientSession, html_content: str, base_url: Optional[str], game_dir_path: Path, game_temp_images_dir: Path, inline_images: bool, game_display_name: str, log_method) -> tuple[str, int]:
    """Finds images in HTML, downloads them concurrently, and rewrites src attributes. Returns (html_string, download_count)."""
    if not html_content: return "", 0
    soup = BeautifulSoup(html_content, 'lxml'); images = soup.find_all('img')
    if not images: return html_content, 0
    log_method(f"  {EMOJI_INFO} Processing {len(images)} embedded images for {game_display_name}...")

    temp_embedded_dir = game_temp_images_dir / "embedded"
    tasks = []
    tag_map = []

    for i, img_tag in enumerate(images):
        src = img_tag.get('src')
        if not src or src.startswith('data:'):
            continue
        try:
            img_url = src
            if base_url and not urllib.parse.urlparse(src).scheme: img_url = urllib.parse.urljoin(base_url, src)

            filename_hint = Path(urllib.parse.urlparse(img_url).path).stem if urllib.parse.urlparse(img_url).path else f"emb_{i}"
            file_extension = Path(urllib.parse.urlparse(img_url).path).suffix.lower() or ".jpg"
            if file_extension not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']: file_extension = '.jpg'

            temp_path = temp_embedded_dir / f"embedded_{i}_{filename_hint[:20]}{file_extension}"
            tasks.append(download_file_with_progress(session, img_url, temp_path, log_method, f"Embed {i+1}/{len(images)}"))
            tag_map.append({'tag': img_tag, 'temp_path': temp_path})
        except Exception as e:
            log_method(f"      {EMOJI_ERROR} Error preparing embedded image {src} for download: {e}")

    download_results = await asyncio.gather(*tasks)

    embedded_count = 0
    for i, success in enumerate(download_results):
        if success:
            info = tag_map[i]
            img_tag = info['tag']
            temp_path = info['temp_path']
            if inline_images:
                data_uri = encode_image_to_base64_data_uri(temp_path)
                if data_uri:
                    img_tag['src'] = data_uri
                    embedded_count += 1
                else: log_method(f"      {EMOJI_ERROR} Failed to encode embedded image from {temp_path}")
            else:
                persistent_embedded_dir = game_dir_path / EMBEDDED_CONTENT_DIR_NAME
                persistent_embedded_dir.mkdir(parents=True, exist_ok=True)
                final_embedded_path = persistent_embedded_dir / temp_path.name
                try:
                    shutil.move(str(temp_path), str(final_embedded_path))
                    img_tag['src'] = str(EMBEDDED_CONTENT_DIR_NAME / final_embedded_path.name).replace(os.sep, '/')
                    embedded_count += 1
                except Exception as e: log_method(f"      {EMOJI_ERROR} Failed to move embedded image {temp_path} to {final_embedded_path}: {e}")

    log_method(f"  {EMOJI_SUCCESS_DATA} Finished embedded image processing. Successfully processed {embedded_count}/{len(images)}.")
    return soup.prettify(), embedded_count

# --- Real Data Fetchers ---
async def fetch_steam_data(session: aiohttp.ClientSession, game_title: str, log_method) -> Optional[Dict[str, Any]]:
    log_method(f"    {EMOJI_QUERY} Steam: Querying for '{game_title}'...")
    search_url = f"https://store.steampowered.com/api/storesearch/?term={urllib.parse.quote(game_title)}&l=english&cc=US"
    app_id = None; game_data = {}
    try:
        async with session.get(search_url, timeout=10) as response:
            if response.status == 200:
                search_results = await response.json()
                if search_results.get("total", 0) > 0 and search_results.get("items"):
                    best_match = None
                    for item in search_results["items"]:
                        if game_title.lower() == item.get("name", "").lower(): best_match = item; break
                    if not best_match: best_match = search_results["items"][0]
                    app_id = best_match.get("id")
                    if not game_data.get("name"): game_data["name"] = best_match.get("name")
                    game_data["app_id"] = app_id
            else: log_method(f"    {EMOJI_WARNING} Steam: Search request failed {response.status} for '{game_title}'."); return None
    except Exception as e: log_method(f"    {EMOJI_ERROR} Steam: Error during search for '{game_title}': {e}"); return None
    if app_id:
        details_url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&l=english&cc=US"
        try:
            async with session.get(details_url, timeout=15) as response:
                if response.status == 200:
                    app_details_json = await response.json()
                    data = app_details_json.get(str(app_id), {}).get("data", {})
                    if data:
                        game_data["name"] = data.get("name", game_data.get("name"))
                        game_data["description_html"] = data.get("detailed_description")
                        game_data["steam_url"] = f"https://store.steampowered.com/app/{app_id}/"
                        game_data["base_url_for_description_images"] = f"https://store.steampowered.com/app/{app_id}/"
                        game_data["release_date"] = data.get("release_date", {}).get("date")
                        game_data["developers"] = data.get("developers", [])
                        game_data["publishers"] = data.get("publishers", [])
                        game_data["genres"] = [genre["description"] for genre in data.get("genres", []) if isinstance(genre, dict)]
                        game_data["header_image_url"] = data.get("header_image")
                        raw_steam_screenshots_data = data.get("screenshots", [])
                        if isinstance(raw_steam_screenshots_data, list):
                            extracted_urls = [sc_item["path_full"] for sc_item in raw_steam_screenshots_data if isinstance(sc_item, dict) and "path_full" in sc_item]
                            game_data["screenshots"] = extracted_urls
                        else: game_data["screenshots"] = []
                        if data.get("website"): game_data["website"] = data.get("website")
                        if data.get("metacritic"):
                            game_data["metacritic_score_from_steam"] = data.get("metacritic", {}).get("score")
                            game_data["metacritic_url_from_steam"] = data.get("metacritic", {}).get("url")
                        return game_data
                    else: log_method(f"    {EMOJI_WARNING} Steam: No data in appdetails for app ID {app_id}.")
                else: log_method(f"    {EMOJI_WARNING} Steam: Appdetails request failed {response.status} for app ID {app_id}.")
        except Exception as e: log_method(f"    {EMOJI_ERROR} Steam: Error fetching appdetails for '{game_title}': {e}")
    return game_data if game_data.get("name") else None

async def fetch_steam_user_reviews(session: aiohttp.ClientSession, app_id: Optional[str], log_method) -> Optional[List[Dict[str, str]]]:
    if not app_id: return None
    log_method(f"    {EMOJI_QUERY} Steam Reviews: Querying for app ID '{app_id}'...")
    reviews_url = f"https://store.steampowered.com/appreviews/{app_id}?json=1&language=all&num_per_page=3&filter=summary"
    steam_reviews_data = []
    try:
        async with session.get(reviews_url, timeout=10) as response:
            if response.status == 200:
                review_json = await response.json()
                if review_json.get("success") == 1 and review_json.get("reviews"):
                    for review in review_json["reviews"][:3]:
                        review_text = review.get("review", "No review text.")
                        author_id = review.get("author", {}).get("steamid", "Unknown Author")
                        votes_up = review.get("votes_up", 0)
                        recommendation = "Recommended" if review.get("voted_up") else "Not Recommended"
                        steam_reviews_data.append({"source": f"Steam User ({author_id}) - {recommendation}", "score": f"{votes_up} helpful votes", "snippet": review_text[:300] + "..." if len(review_text) > 300 else review_text, "url": f"https://steamcommunity.com/profiles/{author_id}/recommended/{app_id}/" })
                    return steam_reviews_data
    except Exception: pass
    return steam_reviews_data if steam_reviews_data else None

async def fetch_metacritic_data(session: aiohttp.ClientSession, game_title: str, log_method, game_platform: str = "pc") -> Optional[Dict[str, Any]]:
    log_method(f"    {EMOJI_QUERY} Metacritic: Querying for '{game_title}' on {game_platform}...")
    slug_title = re.sub(r'[^\w\s-]', '', game_title.lower()); slug_title = re.sub(r'\s+', '-', slug_title).strip('-')
    possible_slugs = list(dict.fromkeys([ slug_title, slug_title.replace(":", ""), re.sub(r'-edition$', '', slug_title, flags=re.IGNORECASE), re.sub(r'-goty$', '', slug_title, flags=re.IGNORECASE) ]))
    for current_slug in possible_slugs:
        search_url = f"https://www.metacritic.com/game/{game_platform}/{current_slug}/"
        data = {"metacritic_url": search_url}
        try:
            async with session.get(search_url, timeout=15, headers={'Accept-Language': 'en-US,en;q=0.5', 'User-Agent': USER_AGENT}) as response:
                if response.status == 200:
                    html_content = await response.text(); soup = BeautifulSoup(html_content, 'lxml')
                    score_selectors = [ "div.c-siteReviewScore span", "div.metascore_w.game." + game_platform + " > span", "div.metascore_w > span", "a.metascore_anchor span" ]
                    for selector in score_selectors:
                        score_element = soup.select_one(selector)
                        if score_element and score_element.text.strip().isdigit(): data["metacritic_score"] = int(score_element.text.strip()); break
                    reviews = []
                    review_item_selectors = [ "div.c-pageProductReviews_row--critic", "li.c-pageProductReviews_row.critic_review", "div.review.critic_review" ]
                    review_elements = []
                    for sel in review_item_selectors:
                        review_elements = soup.select(sel)
                        if review_elements: break
                    for rev_element in review_elements[:3]:
                        source_el = rev_element.select_one('.c-siteReviewHeader_publicationName, .review_source .source a'); score_el = rev_element.select_one('.c-siteReviewScore_scoreNumber, .review_grade span')
                        snippet_el = rev_element.select_one('.c-siteReview_description span, .review_body, div[class*="Review__body"] p'); review_data = {}
                        if source_el: review_data["source"] = source_el.text.strip()
                        if score_el: review_data["score"] = score_el.text.strip()
                        if snippet_el: snippet_text = ' '.join(snippet_el.find_all(string=True, recursive=True)).strip(); review_data["snippet"] = snippet_text[:250] + "..." if len(snippet_text) > 250 else snippet_text
                        link_el = rev_element.select_one('.c-siteReviewHeader_publicationName a, .review_source a, .c-siteReviewHeader_url a, a.external')
                        if link_el and link_el.get('href'): review_data["url"] = urllib.parse.urljoin(search_url, link_el.get('href'))
                        if review_data.get("source") and review_data.get("score"): reviews.append(review_data)
                    if reviews: data["reviews"] = reviews
                    if "metacritic_score" in data or reviews: return data
                elif response.status in [403, 404]: pass
                else: log_method(f"    {EMOJI_WARNING} Metacritic: Request failed {response.status} for slug '{current_slug}'. URL: {search_url}")
        except Exception as e: log_method(f"    {EMOJI_ERROR} Metacritic: Error scraping '{current_slug}': {e}")
    log_method(f"    {EMOJI_SHRUG} Metacritic: No data found for '{game_title}' after trying all slugs.")
    return None

async def fetch_wikipedia_data(session: aiohttp.ClientSession, game_title: str, log_method) -> Optional[Dict[str, Any]]:
    log_method(f"    {EMOJI_QUERY} Wikipedia: Querying for '{game_title}'...")
    data = {}
    try:
        def sync_wikipedia_search():
            try:
                suggestion = wikipedia.suggest(game_title); page_title_to_search = suggestion if suggestion else game_title
                page = wikipedia.page(page_title_to_search, auto_suggest=False, redirect=True)
                data["wikipedia_url"] = page.url; data["description_text"] = page.summary
                if page.categories and any("video game" in cat.lower() for cat in page.categories): data["is_video_game_page"] = True
                return data
            except wikipedia.exceptions.PageError: log_method(f"    {EMOJI_SHRUG} Wikipedia: Page not found for '{game_title}'."); return None
            except wikipedia.exceptions.DisambiguationError as e:
                log_method(f"    {EMOJI_WARNING} Wikipedia: Disambiguation for '{game_title}'. Trying first: {e.options[0] if e.options else 'N/A'}")
                if e.options:
                    try:
                        page = wikipedia.page(e.options[0], auto_suggest=False, redirect=True)
                        data["wikipedia_url"] = page.url; data["description_text"] = page.summary; return data
                    except Exception: log_method(f"    {EMOJI_ERROR} Wikipedia: Failed disambiguated page."); return None
                return None
            except Exception as e_sync: log_method(f"    {EMOJI_ERROR} Wikipedia: Sync lib error for '{game_title}': {e_sync}"); return None
        loop = asyncio.get_event_loop(); data = await loop.run_in_executor(None, sync_wikipedia_search); return data
    except Exception as e: log_method(f"    {EMOJI_ERROR} Wikipedia: Async error for '{game_title}': {e}"); return None

async def fetch_mobygames_data(session: aiohttp.ClientSession, game_title: str, log_method) -> Optional[Dict[str, Any]]:
    log_method(f"    {EMOJI_QUERY} MobyGames: Querying for '{game_title}'...")
    search_query = urllib.parse.quote(game_title); search_url = f"https://www.mobygames.com/search/?q={search_query}"
    game_data = {}
    try:
        async with session.get(search_url, timeout=15, headers={'User-Agent': USER_AGENT}) as response:
            if response.status != 200: 
                if response.status not in [403, 404]: log_method(f"    {EMOJI_WARNING} MobyGames: Search fail {response.status} for '{game_title}'.")
                return None
            html_content = await response.text(); soup = BeautifulSoup(html_content, 'lxml')
            first_result_link_el = soup.select_one('.search-results-item-primary-link a, .table.search-results td:nth-of-type(1) a')
            if not first_result_link_el or not first_result_link_el.get('href'):
                log_method(f"    {EMOJI_SHRUG} MobyGames: No game link in search for '{game_title}'."); return None
            game_page_url = urllib.parse.urljoin("https://www.mobygames.com", first_result_link_el.get('href'))
            game_data["mobygames_url"] = game_page_url
        async with session.get(game_page_url, timeout=15, headers={'User-Agent': USER_AGENT}) as game_page_response:
            if game_page_response.status != 200: 
                if game_page_response.status not in [403, 404]: log_method(f"    {EMOJI_WARNING} MobyGames: Game page fail {game_page_response.status}.")
                return game_data
            game_html = await game_page_response.text(); game_soup = BeautifulSoup(game_html, 'lxml')
            title_el = game_soup.select_one('h1[itemprop="name"], div.game-title-row h1')
            if title_el: game_data["name"] = title_el.text.strip()
            desc_el = game_soup.select_one('section#description div.object-description__body, div#gameDescription div.col-md-9')
            if desc_el: game_data["description_text"] = desc_el.get_text(separator="\n", strip=True)
            return game_data
    except Exception as e: log_method(f"    {EMOJI_ERROR} MobyGames: Error scraping '{game_title}': {e}")
    return game_data if game_data else None

# --- HTML Generation ---
def score_to_stars_html(score_value: Optional[int], max_stars: int = 5) -> str:
    if not isinstance(score_value, (int, float)) or score_value < 0 or score_value > 100:
        return ""
    num_filled_stars = round((score_value / 100) * max_stars)
    stars_html = '<span class="star-rating">'
    for i in range(max_stars):
        stars_html += '<span class="star filled">★</span>' if i < num_filled_stars else '<span class="star empty">☆</span>'
    stars_html += '</span>'
    return stars_html

def generate_html_content(game_data: Dict[str, Any]) -> str:
    title = game_data.get("name", "Game Information")
    header_image_src = game_data.get("header_image_src")
    header_image_html = f'<img src="{header_image_src}" alt="Header for {title}" class="header-image">' if header_image_src else ""

    description_html_content = game_data.get("description_html", "")
    if not description_html_content and game_data.get("description_text"):
        paras = game_data["description_text"].split('\n')
        description_html_content = "".join(f"<p>{p.strip()}</p>" for p in paras if p.strip())
    if not description_html_content: description_html_content = "<p>No description available.</p>"

    metacritic_score_val = game_data.get("metacritic_score", game_data.get("metacritic_score_from_steam", "N/A"))
    metacritic_url = game_data.get("metacritic_url", game_data.get("metacritic_url_from_steam"))
    metacritic_score_display = "N/A"
    star_rating_html = score_to_stars_html(metacritic_score_val if isinstance(metacritic_score_val, (int,float)) else None)

    if isinstance(metacritic_score_val, (int, float)):
        color_class = "score-unknown"
        if metacritic_score_val >= 90: color_class = "score-9x"
        elif metacritic_score_val >= 80: color_class = "score-8x"
        elif metacritic_score_val >= 70: color_class = "score-7x"
        elif metacritic_score_val >= 60: color_class = "score-6x"
        elif metacritic_score_val >= 50: color_class = "score-5x"
        elif metacritic_score_val > 0 : color_class = "score-0x"
        score_span = f'<span class="score {color_class}">{metacritic_score_val}</span>'
        if metacritic_url:
            metacritic_score_display = f'<a href="{metacritic_url}" target="_blank" rel="noopener noreferrer" title="View on Metacritic">{score_span}</a>'
        else: metacritic_score_display = score_span
    elif metacritic_score_val != "N/A":
        metacritic_score_display = f'<span class="score score-unknown">{metacritic_score_val}</span>'

    reviews_html = ""
    if game_data.get("reviews"):
        for review in game_data["reviews"]:
            reviews_html += f"""<div class="review"><p><strong>{review.get('source', 'Unknown Source')}:</strong> <span class="review-score">{review.get('score', 'N/A')}</span></p><blockquote>"{review.get('snippet', 'No snippet.')}"{f'<p><a href="{review["url"]}" target="_blank" rel="noopener noreferrer">Read full review »</a></p>' if review.get("url") and review.get("url") != "#" else ""}</blockquote></div>"""
    else: reviews_html = "<p>No reviews available for this game.</p>"

    screenshots_html = ""
    screenshot_sources = game_data.get("screenshot_sources", [])
    if screenshot_sources:
        for src_val in screenshot_sources:
            if src_val: screenshots_html += f'<a href="{src_val}" target="_blank"><img src="{src_val}" alt="Screenshot of {title}" class="screenshot"></a>\n'
    if not screenshots_html: screenshots_html = "<p>No screenshots available.</p>"

    links_html_content = "<ul>"
    def add_link(url_key, text, data=game_data):
        nonlocal links_html_content
        url_val = data.get(url_key)
        if not url_val and url_key == "metacritic_url": url_val = data.get("metacritic_url_from_steam")
        if url_val: links_html_content += f'<li><a href="{url_val}" target="_blank" rel="noopener noreferrer">{text}</a></li>'
    add_link("website", "Official Website"); add_link("steam_url", "Steam Page")
    add_link("metacritic_url", "Metacritic Page"); add_link("wikipedia_url", "Wikipedia Page")
    add_link("mobygames_url", "MobyGames Page"); add_link("hltb_url", "HowLongToBeat Page")
    youtube_search_query = f"{title} gameplay"; youtube_search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(youtube_search_query)}"
    links_html_content += f'<li><a href="{youtube_search_url}" target="_blank" rel="noopener noreferrer">Search Gameplay on YouTube</a></li>'
    if links_html_content == "<ul>": links_html_content = "<p>No relevant links found.</p>"
    else: links_html_content += "</ul>"

    details_html = ""
    details_to_display = [
        ("Released", game_data.get("release_date")),
        ("Developers", ", ".join(game_data.get("developers", []))),
        ("Publishers", ", ".join(game_data.get("publishers", []))),
        ("Genres", ", ".join(game_data.get("genres", []))),
        ("Platforms", ", ".join(game_data.get("platforms", []))),
        ("Main Story (HLTB)", game_data.get("main_story_time")),
        ("Main + Extras (HLTB)", game_data.get("main_plus_extras_time")),
        ("Completionist (HLTB)", game_data.get("completionist_time")),
    ]

    for label, value in details_to_display:
        if isinstance(value, str) and value.strip():
            details_html += f"<tr><th>{label}:</th><td>{value}</td></tr>"

    if details_html:
        details_html = f"<table class='details-table'>{details_html}</table>"
    else:
        details_html = "<p>No specific game details available.</p>"

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{title} - Game Info</title><style>body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; background-color: #1e1e1e; color: #d4d4d4; line-height: 1.6; }} .container {{ max-width: 1400px; margin: 20px auto; background-color: #2a2a2a; padding: 25px; border-radius: 8px; box-shadow: 0 0 15px rgba(0,0,0,0.5); }} header {{ text-align: center; margin-bottom: 20px;}} .header-image {{ max-width: 100%; max-height: 300px; width:auto; border-radius: 6px; margin-bottom: 15px; object-fit: cover; display: block; margin-left: auto; margin-right: auto; }} header h1 {{ color: #569cd6; font-size: 2.8em; margin: 0; border-bottom: 2px solid #444; padding-bottom: 10px; }} h2 {{ color: #9cf; margin-top: 25px; border-bottom: 1px solid #444; padding-bottom: 8px; font-size: 1.8em; }} p {{ margin-bottom: 1em; }} .main-content-grid {{ display: grid; grid-template-columns: 1fr; gap: 25px; }} @media (min-width: 992px) {{ .main-content-grid {{ grid-template-columns: 2fr 1fr; }} .description-section {{ grid-column: 1 / 2; }} .sidebar-section {{ grid-column: 2 / 3; display: flex; flex-direction: column; }} }} .description-content {{ background-color: #333; padding: 15px; border-radius: 5px; margin-top:10px; }} .description-content p {{ margin-bottom: 0.5em; }} .description-content p:last-child {{ margin-bottom: 0; }} .description-content img {{ display: block; max-width: 100%; height: auto; border-radius: 4px; margin: 10px 0; }} .screenshots-container {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 15px; margin-top: 15px; }} .screenshot {{ width: 100%; height: auto; border-radius: 4px; border: 2px solid #444; object-fit: cover; transition: transform 0.2s ease-in-out, box-shadow 0.2s ease-in-out; }} .screenshot:hover {{ transform: scale(1.03); box-shadow: 0 0 10px rgba(86,156,214,0.7); }} .section-box {{ background-color: #333; padding: 20px; border-radius: 6px; margin-bottom:20px; }} .review {{ border-left: 4px solid #569cd6; padding-left: 15px; margin-bottom: 20px; background-color: #383838; padding: 15px; border-radius: 0 5px 5px 0; }} .review strong {{ color: #9cdcfe; }} .review-score {{ font-weight: bold; background-color: #4f4f4f; padding: 2px 6px; border-radius: 3px; }} .review blockquote {{ margin: 8px 0 8px 20px; font-style: italic; color: #b0b0b0; border-left: 2px solid #555; padding-left: 10px; }} .score-section p {{ display: flex; align-items: center; gap: 10px; }} .score {{ font-weight: bold; padding: 5px 10px; border-radius: 5px; color: white; display: inline-block; min-width: 30px; text-align: center;}} .score a {{ color: white; text-decoration: none; }} .star-rating {{ font-size: 1.2em; color: #ffc107; margin-left: 5px; }} .star.empty {{ color: #555; }} .score-9x {{ background-color: #4CAF50; }} .score-8x {{ background-color: #8BC34A; }} .score-7x {{ background-color: #CDDC39; color: #333; }} .score-6x {{ background-color: #FFEB3B; color: #333; }} .score-5x {{ background-color: #FFC107; color: #333; }} .score-0x {{ background-color: #F44336; }} .score-unknown {{ background-color: #777; }} ul {{ list-style-type: none; padding-left: 0; }} li {{ margin-bottom: 8px; }} a {{ color: #569cd6; text-decoration: none; }} a:hover {{ text-decoration: underline; color: #9cdcfe; }} .details-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; background-color: #383838; border-radius: 5px; overflow: hidden;}} .details-table th, .details-table td {{ padding: 10px 15px; text-align: left; border-bottom: 1px solid #4a4a4a; }} .details-table th {{ font-weight: bold; color: #9cdcfe; width: 35%; }} .details-table tr:last-child th, .details-table tr:last-child td {{ border-bottom: none; }} footer {{ text-align: center; margin-top: 30px; padding-top: 15px; border-top: 1px solid #444; font-size: 0.9em; color: #888; }}</style></head><body><div class="container"><header>{header_image_html}<h1>{title}</h1></header><div class="main-content-grid"><section class="description-section section-box"><h2>Description</h2><div class="description-content">{description_html_content}</div></section><aside class="sidebar-section"><section class="section-box" id="details"><h2>Game Details</h2>{details_html}</section><section class="section-box" id="scores"><h2>Scores & Reviews</h2><div class="score-section"><p><strong>Metacritic Score:</strong> {metacritic_score_display} {star_rating_html}</p></div>{reviews_html}</section><section class="section-box" id="links"><h2>Links</h2>{links_html_content}</section></aside></div><section class="section-box" id="screenshots-main"><h2>Screenshots</h2><div class="screenshots-container">{screenshots_html}</div></section><footer>Generated by GameInfoGenerator v{SCRIPT_VERSION} on {time.strftime('%Y-%m-%d %H:%M:%S')}</footer></div></body></html>"""

def cleanup_generated_files_for_game(game_dir_path: Path, log_method) -> bool:
    """Cleans up all files generated by the script in a specific game directory."""
    html_file = game_dir_path / DEFAULT_HTML_FILENAME
    header_image_file = game_dir_path / HEADER_IMAGE_FILENAME
    screenshots_dir = game_dir_path / SCREENSHOTS_DIR_NAME
    temp_image_dir = game_dir_path / GAME_TEMP_IMAGES_SUBDIR

    cleaned_something = False
    if html_file.exists():
        try: html_file.unlink(); log_method(f"    {EMOJI_CLEAN} Removed: {html_file.name}"); cleaned_something = True
        except OSError as e: log_method(f"    {EMOJI_ERROR} Error removing {html_file.name}: {e}")

    if header_image_file.exists():
        try: header_image_file.unlink(); log_method(f"    {EMOJI_CLEAN} Removed: {header_image_file.name}"); cleaned_something = True
        except OSError as e: log_method(f"    {EMOJI_ERROR} Error removing {header_image_file.name}: {e}")

    if screenshots_dir.exists() and screenshots_dir.is_dir():
        try: shutil.rmtree(screenshots_dir); log_method(f"    {EMOJI_CLEAN} Removed directory: {screenshots_dir.name}"); cleaned_something = True
        except OSError as e: log_method(f"    {EMOJI_ERROR} Error removing directory {screenshots_dir.name}: {e}")

    if temp_image_dir.exists() and temp_image_dir.is_dir():
        try: shutil.rmtree(temp_image_dir); log_method(f"    {EMOJI_CLEAN} Removed temporary image directory: {temp_image_dir.name}"); cleaned_something = True
        except OSError as e: log_method(f"    {EMOJI_ERROR} Error removing temporary image directory {temp_image_dir.name}: {e}")
    return cleaned_something

def cleanup_generated_files_for_game_cli(game_dir_path: Path) -> tuple[List[str], int]:
    """CLI version of the cleanup function, returns logs and bytes deleted."""
    deleted_files_log = []
    total_bytes_deleted = 0
    html_file = game_dir_path / DEFAULT_HTML_FILENAME
    header_image_file = game_dir_path / HEADER_IMAGE_FILENAME
    screenshots_dir = game_dir_path / SCREENSHOTS_DIR_NAME
    temp_image_dir = game_dir_path / GAME_TEMP_IMAGES_SUBDIR

    for file_to_delete in [html_file, header_image_file]:
        if file_to_delete.exists() and file_to_delete.is_file():
            try:
                file_size = file_to_delete.stat().st_size; file_to_delete.unlink()
                deleted_files_log.append(f"    {EMOJI_CLEAN} Removed: {file_to_delete.name} ({file_size} bytes)")
                total_bytes_deleted += file_size
            except OSError as e: deleted_files_log.append(f"    {EMOJI_ERROR} Error removing {file_to_delete.name}: {e}")

    for dir_to_delete in [screenshots_dir, temp_image_dir]:
        if dir_to_delete.exists() and dir_to_delete.is_dir():
            dir_size = sum(f.stat().st_size for f in dir_to_delete.glob('**/*') if f.is_file())
            try:
                shutil.rmtree(dir_to_delete)
                deleted_files_log.append(f"    {EMOJI_CLEAN} Removed directory: {dir_to_delete.name} (approx. {dir_size} bytes)")
                total_bytes_deleted += dir_size
            except OSError as e: deleted_files_log.append(f"    {EMOJI_ERROR} Error removing directory {dir_to_delete.name}: {e}")
    return deleted_files_log, total_bytes_deleted

def run_cleanup_cli(args: argparse.Namespace):
    """Handles the --cleanup command line action."""
    print(f"{EMOJI_ROCKET} Game Info Generator v{SCRIPT_VERSION} - Cleanup Mode {EMOJI_ROCKET}")
    
    dirs_to_clean = []
    for path_str in args.input_dirs:
        current_path = Path(path_str).resolve()
        if not current_path.is_dir():
            print(f"{EMOJI_WARNING} Path '{path_str}' is not a valid directory. Skipping for cleanup.")
            continue
        
        if args.recursive:
            dirs_to_clean.extend([d for d in current_path.iterdir() if d.is_dir()])
        else:
            dirs_to_clean.append(current_path)
    
    if not dirs_to_clean:
        print(f"{EMOJI_QUESTION} No directories found to cleanup.")
        return

    print(f"{EMOJI_INFO} Starting cleanup for {len(dirs_to_clean)} director{'ies' if len(dirs_to_clean) > 1 else 'y'}...")
    total_cleaned_count = 0; grand_total_bytes_deleted = 0
    for game_dir in dirs_to_clean:
        print(f"  {EMOJI_PROCESS} Cleaning up in '{game_dir.name}'...")
        deleted_logs, bytes_deleted = cleanup_generated_files_for_game_cli(game_dir)
        for log_entry in deleted_logs: print(log_entry)
        if any(EMOJI_CLEAN in log for log in deleted_logs): total_cleaned_count +=1
        grand_total_bytes_deleted += bytes_deleted

    if total_cleaned_count > 0:
        space_freed_mb = grand_total_bytes_deleted / (1024 * 1024)
        print(f"\n{EMOJI_SUCCESS_DATA} Cleanup complete. Processed {total_cleaned_count} directories.")
        print(f"  Total space freed: {grand_total_bytes_deleted} bytes ({space_freed_mb:.2f} MB)")
    else: print(f"\n{EMOJI_INFO} No generated files found to remove during cleanup.")
    print(f"{EMOJI_PARTY} Cleanup finished.")

async def process_game_directory(session: aiohttp.ClientSession, game_dir_path: Path, game_title_cleaned: str, force_update: bool, inline_images: bool, log_method) -> Dict[str, Any]:
    """Main processing logic for a single game directory, returns stats."""
    start_time = time.monotonic()
    stats = {
        'status': 'ERROR', 'size_bytes': 0, 'files_generated': 0, 'files_downloaded': 0,
        'duration_s': 0, 'sources_summary': {}, 'failed_sources': []
    }

    game_temp_images_dir = game_dir_path / GAME_TEMP_IMAGES_SUBDIR

    try:
        if force_update:
            cleanup_generated_files_for_game(game_dir_path, lambda msg: None) # Silent cleanup

        if game_temp_images_dir.exists(): shutil.rmtree(game_temp_images_dir)
        game_temp_images_dir.mkdir(parents=True, exist_ok=True)

        aggregated_data: Dict[str, Any] = {"name": game_title_cleaned}
        
        def merge_data(target_dict, source_dict):
            if not source_dict: return
            for key, value in source_dict.items():
                if value is None or (isinstance(value, (str, list, dict)) and not value): continue
                if key in ["screenshots", "developers", "genres", "publishers", "platforms", "reviews"]:
                    if key not in target_dict or not isinstance(target_dict[key], list): target_dict[key] = []
                    current_list = target_dict[key]
                    if isinstance(value, list):
                        for item in value:
                            if key == "reviews" and isinstance(item, dict):
                                if not any(r.get("source") == item.get("source") and r.get("snippet") == item.get("snippet") for r in current_list): current_list.append(item)
                            elif key != "reviews":
                                str_item = str(item)
                                if not any(str_item.lower() == str(existing_item).lower() for existing_item in current_list): current_list.append(item)
                    elif key == "reviews" and isinstance(value, dict):
                        if not any(r.get("source") == value.get("source") and r.get("snippet") == value.get("snippet") for r in current_list): current_list.append(value)
                    elif key != "reviews" and not any(str(value).lower() == str(existing_item).lower() for existing_item in current_list): current_list.append(value)
                elif key not in target_dict or not target_dict[key]: target_dict[key] = value
                elif key == "name" and isinstance(value, str) and len(value) > len(target_dict.get("name","")): target_dict[key] = value
                elif key == "description_html" and isinstance(value, str) and len(value) > len(target_dict.get("description_html", "")): target_dict[key] = value
                elif key == "description_text" and isinstance(value, str) and (not target_dict.get("description_html")) and len(value) > len(target_dict.get("description_text", "")): target_dict[key] = value

        # --- Data Fetching ---
        steam_data = await fetch_steam_data(session, aggregated_data.get("name", game_title_cleaned), log_method)
        steam_app_id = None
        if steam_data:
            merge_data(aggregated_data, steam_data)
            stats['sources_summary']['Steam'] = f"AppID: {steam_data.get('app_id', 'N/A')}, {len(steam_data.get('screenshots', []))} screenshots"
            steam_app_id = steam_data.get("app_id")
            if steam_data.get("header_image_url"):
                header_src, dl_count = await download_header_image_async(session, steam_data["header_image_url"], game_dir_path, game_temp_images_dir, inline_images, log_method)
                if header_src: 
                    aggregated_data["header_image_src"] = header_src
                    stats['files_downloaded'] += dl_count
        else:
            stats['failed_sources'].append('Steam')
            
        current_best_name = aggregated_data.get("name", game_title_cleaned)

        fetch_tasks = {
            "Metacritic": fetch_metacritic_data(session, current_best_name, log_method),
            "Wikipedia": fetch_wikipedia_data(session, current_best_name, log_method),
            "MobyGames": fetch_mobygames_data(session, current_best_name, log_method),
        }
        if steam_app_id:
            fetch_tasks["Steam Reviews"] = fetch_steam_user_reviews(session, steam_app_id, log_method)
        
        results = await asyncio.gather(*fetch_tasks.values(), return_exceptions=True)
        source_data_map = dict(zip(fetch_tasks.keys(), results))

        # --- Data Aggregation ---
        # Process all non-review data first
        for source_name, result_data in source_data_map.items():
            if source_name == "Steam Reviews": continue # Handle reviews separately
            if isinstance(result_data, Exception) or not result_data:
                if source_name not in stats['failed_sources']: stats['failed_sources'].append(source_name)
                continue
            
            if source_name == 'Metacritic':
                score = result_data.get('metacritic_score', 'N/A')
                reviews_count = len(result_data.get('reviews', []))
                stats['sources_summary']['Metacritic'] = f"Score: {score}, {reviews_count} reviews"
                data_without_reviews = {k: v for k, v in result_data.items() if k != 'reviews'}
                merge_data(aggregated_data, data_without_reviews)
            else:
                stats['sources_summary'][source_name] = "Data found"
                merge_data(aggregated_data, result_data)

        # Prioritize and combine reviews
        final_reviews = []
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
            aggregated_data['reviews'] = final_reviews
            if "Steam Reviews" in fetch_tasks and steam_user_reviews:
                 stats['sources_summary']['Steam Reviews'] = f"{len(steam_user_reviews)} reviews found"
        elif "Steam Reviews" in fetch_tasks:
            stats['failed_sources'].append("Steam Reviews")


        # --- Image Processing ---
        if aggregated_data.get("description_html"):
            modified_html, dl_count = await download_and_rewrite_embedded_images(session, aggregated_data["description_html"], aggregated_data.get("base_url_for_description_images"), game_dir_path, game_temp_images_dir, inline_images, current_best_name, log_method)
            aggregated_data["description_html"] = modified_html
            stats['files_downloaded'] += dl_count

        screenshot_urls_to_download = aggregated_data.get("screenshots", [])
        if screenshot_urls_to_download:
            screenshot_sources, dl_count = await download_screenshots(session, screenshot_urls_to_download, game_dir_path, game_temp_images_dir, inline_images, current_best_name, log_method)
            aggregated_data["screenshot_sources"] = screenshot_sources
            stats['files_downloaded'] += dl_count

        # --- Finalization ---
        if not aggregated_data.get("description_html") and not aggregated_data.get("description_text") and (not aggregated_data.get("screenshot_sources") and not aggregated_data.get("header_image_src")):
            stats['status'] = 'INSUFFICIENT_DATA'
            return stats

        html_output = generate_html_content(aggregated_data)
        output_html_file = game_dir_path / DEFAULT_HTML_FILENAME
        with open(output_html_file, "w", encoding="utf-8") as f: f.write(html_output)
        
        stats['status'] = 'SUCCESS'
        stats['files_generated'] = 1 # The HTML file itself
        stats['size_bytes'] += output_html_file.stat().st_size
        if not inline_images:
            for f in game_dir_path.rglob('*'):
                if f.is_file() and f != output_html_file:
                    stats['size_bytes'] += f.stat().st_size

    except Exception as e:
        log_method(f"  {EMOJI_ERROR} An unexpected error occurred during processing of '{game_dir_path.name}': {e}")
        stats['status'] = 'ERROR'

    finally:
        if game_temp_images_dir.exists():
            try: shutil.rmtree(game_temp_images_dir)
            except OSError as e: log_method(f"      {EMOJI_WARNING} Could not delete temp image directory {game_temp_images_dir}: {e}")
        stats['duration_s'] = time.monotonic() - start_time
        return stats


async def main_loop(args: argparse.Namespace):
    """The main asynchronous loop for processing games."""
    global_start_time = time.monotonic()
    print(f"{EMOJI_ROCKET} Game Info Generator v{SCRIPT_VERSION} - Console Mode {EMOJI_ROCKET}")

    game_directories = []
    for path_str in args.input_dirs:
        current_path = Path(path_str).resolve()
        if not current_path.is_dir():
            print(f"{EMOJI_WARNING} Path '{path_str}' is not a valid directory. Skipping.")
            continue

        if args.recursive:
            game_directories.extend([d for d in current_path.iterdir() if d.is_dir()])
        else:
            game_directories.append(current_path)

    if not game_directories:
        print(f"{EMOJI_ERROR} No valid game directories found to process.")
        return

    total_count = len(game_directories)
    print(f"{EMOJI_SUBDIR} Found {total_count} game director{'y' if total_count == 1 else 'ies'} to process.")
    
    global_stats = {'SUCCESS': 0, 'SKIPPED': 0, 'INSUFFICIENT_DATA': 0, 'ERROR': 0, 'total_size_bytes': 0, 'total_files_gen': 0, 'total_files_dl': 0}

    connector = aiohttp.TCPConnector(limit_per_host=args.max_concurrent)
    async with aiohttp.ClientSession(connector=connector, headers={'User-Agent': USER_AGENT}, timeout=aiohttp.ClientTimeout(total=args.timeout)) as session:
        processed_count = 0
        for game_dir in game_directories:
            processed_count += 1
            game_title_cleaned = clean_game_title(game_dir.name)
            output_html_file = game_dir / DEFAULT_HTML_FILENAME

            print(f"\n{EMOJI_PROCESS} [{processed_count}/{total_count}] {game_dir.name} -> {game_title_cleaned}", end="")

            if output_html_file.exists() and not args.force:
                print(f" {C_YELLOW}(SKIPPED){C_RESET}")
                global_stats['SKIPPED'] += 1
                continue
            
            print() # Newline for processing jobs
            
            game_stats = await process_game_directory(session, game_dir, game_title_cleaned, args.force, args.inline, print)

            if game_stats['status'] != 'SKIPPED':
                status_emoji = {'SUCCESS': EMOJI_SUCCESS_HTML, 'INSUFFICIENT_DATA': EMOJI_SHRUG, 'ERROR': EMOJI_ERROR}
                print(f"  {EMOJI_STATS} Status: {status_emoji.get(game_stats['status'], '')} {game_stats['status']} | "
                      f"{EMOJI_CLOCK} Duration: {game_stats['duration_s']:.2f}s | "
                      f"Generated: {game_stats['files_generated']}, Downloaded: {game_stats['files_downloaded']} | "
                      f"Size: {format_bytes(game_stats['size_bytes'])}")
                
                for source, summary in game_stats['sources_summary'].items():
                    print(f"    {EMOJI_SUCCESS_DATA} {source}: {summary}")
                if game_stats['failed_sources']:
                    print(f"    {C_YELLOW}{EMOJI_SHRUG} No data from: {', '.join(game_stats['failed_sources'])}{C_RESET}")


            if game_stats['status'] in global_stats:
                global_stats[game_stats['status']] += 1
            global_stats['total_size_bytes'] += game_stats['size_bytes']
            global_stats['total_files_gen'] += game_stats['files_generated']
            global_stats['total_files_dl'] += game_stats['files_downloaded']

    total_duration = time.monotonic() - global_start_time
    print(f"\n{EMOJI_PARTY} All game processing finished! {EMOJI_PARTY}")

    if total_count > 0:
        print("\n" + "="*20 + f" {EMOJI_STATS} Run Summary " + "="*20)
        print(f"  {EMOJI_CLOCK} Total execution time: {total_duration:.2f} seconds")
        print(f"  Directories processed: {total_count}")
        print(f"  - {EMOJI_SUCCESS_HTML} Successful: {global_stats['SUCCESS']}")
        print(f"  - {EMOJI_SKIP} Skipped: {global_stats['SKIPPED']}")
        print(f"  - {EMOJI_SHRUG} Failed (No Data): {global_stats['INSUFFICIENT_DATA']}")
        print(f"  - {EMOJI_ERROR} Failed (Errors): {global_stats['ERROR']}")
        print(f"  Total files generated: {global_stats['total_files_gen']}")
        print(f"  Total files downloaded: {global_stats['total_files_dl']}")
        print(f"  Total size of generated files: {format_bytes(global_stats['total_size_bytes'])}")
        print("="*55)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate HTML descriptions for video game directories.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("input_dirs", nargs='+', metavar="INPUT_DIR", help="One or more paths to game directories. If -R is used, these are the root directories to scan.")
    parser.add_argument("-R", "--recursive", action="store_true", help="For each provided directory, scan its subdirectories, treating each as a game.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {SCRIPT_VERSION}")
    parser.add_argument("--force", action="store_true", help="Force regeneration of existing game_info.html files.")
    parser.add_argument("-C", "--cleanup", "--clean", "--remove", "--rm", dest="cleanup", action="store_true", help="Remove ALL script-generated files from the target directory/directories.")
    parser.add_argument("--inline", action="store_true", help="Embed images directly into HTML as Base64 Data URIs. If not set, images are saved as files.")
    parser.add_argument("--max-concurrent", type=int, default=3, help="Max concurrent HTTP requests per host (default: 3).")
    parser.add_argument("--timeout", type=int, default=20, help="Default network timeout for requests in seconds (default: 20).")
    parser.add_argument("--no-color", action="store_true", help="Disable color and emoji output, an alternative to the NO_COLOR env var.")

    args = parser.parse_args()
    
    setup_display_mode('NO_COLOR' in os.environ or args.no_color)

    if args.cleanup:
        run_cleanup_cli(args)
    else:
        try:
            asyncio.run(main_loop(args))
        except KeyboardInterrupt:
            print("\nProcess interrupted by user. Exiting.")
