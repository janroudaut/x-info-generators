#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# movie_info_generator_v3.py
#
# Description:
# A script to generate a self-contained HTML summary for local movie files.
# It extracts the movie title, fetches metadata from IMDb (primary) and
# Wikipedia (enrichment), finds trailers, generates screenshots, and
# compiles everything into a rich HTML file.
#
# Author: Gemini & Jan
# Date: 2025-09-20
#
# Prerequisites:
# 1. FFmpeg: This script requires the ffmpeg command-line tool to be installed
#    and available in your system's PATH to generate video screenshots.
#
# 2. Python Libraries: You need to install several Python packages. You can
#    install them all with the following command:
#    pip install cinemagoer requests beautifulsoup4 ffmpeg-python aiohttp tqdm lxml wikipedia

import argparse
import asyncio
import base64
import json
import mimetypes
import os
import re
import shutil
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional

import aiohttp
import ffmpeg
import wikipedia
from bs4 import BeautifulSoup
from imdb import Cinemagoer
from tqdm.asyncio import tqdm

# --- Configuration & Constants ---
SCRIPT_VERSION = "3.5.3"
DEFAULT_HTML_FILENAME = "movie_info.html"
# Persistent storage directories if not inlining
ASSETS_DIR_NAME_PREFIX = "_"
SCREENSHOTS_SUBDIR_NAME = "screenshots"
# Temporary storage during generation
TEMP_PROCESSING_SUBDIR = "_temp_processing_assets"
USER_AGENT = f"MovieInfoGenerator/{SCRIPT_VERSION} (Personal, manual use script)"
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv')
NOISE_REGEX = re.compile(
    r'(\b(5 1|7 1|8bit|10bit|2160p|1080p|720p|480p|aac|ac3|bluray|brrip|directors cut|dts|dual|dvdrip|extended|hdrip|hevc|multi|repack|remastered|uncut|unrated|uhd|4k|hdr|web dl|webrip|x264|x265|yify|yts)\b)',
    re.IGNORECASE
)

# --- Emojis and Colors (can be disabled) ---
EMOJI_ROCKET, EMOJI_CLEAN, EMOJI_PROCESS, EMOJI_SKIP, EMOJI_SUCCESS_HTML, EMOJI_SUCCESS_DATA, EMOJI_DOWNLOAD, EMOJI_ERROR, EMOJI_WARNING, EMOJI_INFO, EMOJI_QUERY, EMOJI_SHRUG, EMOJI_PARTY, EMOJI_SUBDIR, EMOJI_STATS, EMOJI_CLOCK, EMOJI_FFMPEG, EMOJI_YOUTUBE, EMOJI_TRAILER, EMOJI_PERSON = \
"🚀", "🧹", "✨", "⏩", "📄", "✔️", "🖼️", "❌", "⚠️", "ℹ️", "📡", "🤷", "🎉", "📁", "📊", "⏱️", "🎬", "📺", "🎞️", "👤"
C_YELLOW, C_RED, C_RESET = '\033[93m', '\033[91m', '\033[0m'

def setup_display_mode(no_color_flag: bool):
    """Disables emojis and colors globally based on the flag."""
    global EMOJI_ROCKET, EMOJI_CLEAN, EMOJI_PROCESS, EMOJI_SKIP, EMOJI_SUCCESS_HTML, EMOJI_SUCCESS_DATA, EMOJI_DOWNLOAD, EMOJI_ERROR, EMOJI_WARNING, EMOJI_INFO, EMOJI_QUERY, EMOJI_SHRUG, EMOJI_PARTY, EMOJI_SUBDIR, EMOJI_STATS, EMOJI_CLOCK, EMOJI_FFMPEG, EMOJI_YOUTUBE, EMOJI_TRAILER, EMOJI_PERSON
    global C_YELLOW, C_RED, C_RESET
    if no_color_flag or not sys.stdout.isatty():
        EMOJI_ROCKET, EMOJI_CLEAN, EMOJI_PROCESS, EMOJI_SKIP, EMOJI_SUCCESS_HTML, EMOJI_SUCCESS_DATA, EMOJI_DOWNLOAD, EMOJI_ERROR, EMOJI_WARNING, EMOJI_INFO, EMOJI_QUERY, EMOJI_SHRUG, EMOJI_PARTY, EMOJI_SUBDIR, EMOJI_STATS, EMOJI_CLOCK, EMOJI_FFMPEG, EMOJI_YOUTUBE, EMOJI_TRAILER, EMOJI_PERSON = ("",) * 20
        C_YELLOW, C_RED, C_RESET = ("",) * 3

# --- Utility Functions ---

def format_bytes(size_bytes: int) -> str:
    """Formats bytes into a human-readable string (KB, MB, etc.)."""
    if size_bytes < 1024: return f"{size_bytes} bytes"
    if size_bytes < 1024**2: return f"{size_bytes/1024:.2f} KB"
    if size_bytes < 1024**3: return f"{size_bytes/1024**2:.2f} MB"
    return f"{size_bytes/1024**3:.2f} GB"

def find_movie_files(path: Path) -> List[Path]:
    """Recursively finds all video files in a given path."""
    movie_files = []
    if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
        movie_files.append(path)
    elif path.is_dir():
        for root, _, files in os.walk(path):
            for file in files:
                if file.lower().endswith(VIDEO_EXTENSIONS):
                    movie_files.append(Path(root) / file)
    return movie_files

def clean_filename_to_title(filepath: Path) -> (str, Optional[str]):
    """Cleans a filename to extract a searchable movie title and year."""
    name_without_ext = filepath.stem
    # First, remove content in brackets and parentheses, which often contains release info
    cleaned_name = re.sub(r'\[.*?\]', '', name_without_ext)
    cleaned_name = re.sub(r'\(.*?\)', '', cleaned_name)
    # Then, replace separators
    cleaned_name = re.sub(r'[\._-]', ' ', cleaned_name)
    
    year_match = re.search(r'\b(19[0-9]{2}|20[0-2][0-9]|2030)\b', cleaned_name)
    year = None
    if year_match:
        year = year_match.group(1)
        cleaned_name = cleaned_name[:year_match.start()].strip()
    
    # Final cleanup of technical keywords and spacing
    cleaned_name = NOISE_REGEX.sub('', cleaned_name).strip()
    cleaned_name = re.sub(r'\s+', ' ', cleaned_name)
    return cleaned_name, year

def encode_image_to_base64_data_uri(image_path: Path) -> Optional[str]:
    """Encodes an image file to a Base64 data URI."""
    if not image_path.is_file(): return None
    try:
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type or not mime_type.startswith("image/"):
            mime_type = 'image/jpeg' # Default fallback
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
            with tqdm(total=total_size, unit='B', unit_scale=True, unit_divisor=1024,
                      desc=f"  {EMOJI_DOWNLOAD} {file_type}", leave=False,
                      bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]') as pbar:
                with open(temp_file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        f.write(chunk)
                        pbar.update(len(chunk))
            return True
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        if not isinstance(e, aiohttp.ClientResponseError) or e.status not in [403, 404]:
            log_method(f"      {EMOJI_ERROR} Download error for {url}: {type(e).__name__}")
    return False

# --- ASYNC WRAPPERS FOR SYNC LIBRARIES ---

async def run_in_executor(func, *args):
    """Runs a synchronous function in a thread pool executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)

def _sync_get_movie_info_from_imdb(title: str, year: Optional[str]) -> Optional[Dict[str, Any]]:
    """Synchronous core logic for fetching IMDb data with improved matching."""
    try:
        ia = Cinemagoer()
        movies = ia.search_movie(title, results=10)
        if not movies: return None

        best_match = None
        candidates = movies

        if year:
            year_matches = [m for m in candidates if str(m.get('year', '')) == year]
            if year_matches:
                candidates = year_matches

        movie_matches = [m for m in candidates if m.get('kind') == 'movie']
        if movie_matches:
            best_match = movie_matches[0]
        elif candidates:
            best_match = candidates[0]
        
        if not best_match: return None

        movie = best_match
        ia.update(movie, info=['main', 'full credits'])
        
        def get_person_data(person_list):
            return [{'id': p.personID, 'name': p['name']} for p in person_list if p.personID and p.get('name')]

        return {
            'title': movie.get('title'),
            'year': movie.get('year'),
            'rating': movie.get('rating'),
            'plot': movie.get('plot outline', 'Plot summary not available.'),
            'poster_url': movie.get('full-size cover url'),
            'directors': get_person_data(movie.get('directors', [])),
            'cast': get_person_data(movie.get('cast', [])[:10]),
            'genres': movie.get('genres', []),
            'imdb_id': f"tt{movie.movieID}"
        }
    except Exception:
        return None

def _sync_generate_screenshots(video_path: Path, output_dir: Path, num_screenshots: int = 4) -> List[Path]:
    """Synchronous core logic for generating screenshots with ffmpeg."""
    probe = ffmpeg.probe(str(video_path))
    duration = float(probe['format']['duration'])
    screenshot_paths = []
    for i in range(num_screenshots):
        timestamp = duration * ((i + 1) / (num_screenshots + 1))
        output_file = output_dir / f'screenshot_{i+1}.jpg'
        ffmpeg.input(str(video_path), ss=timestamp).output(str(output_file), vframes=1, **{'q:v': 3}).overwrite_output().run(capture_stdout=True, capture_stderr=True)
        screenshot_paths.append(output_file)
    return screenshot_paths

# --- Data Fetchers ---

async def fetch_imdb_data(title: str, year: Optional[str], log_method) -> Optional[Dict[str, Any]]:
    log_method(f"    {EMOJI_QUERY} IMDb: Querying for '{title}' ({year or 'N/A'})...")
    movie_data = await run_in_executor(_sync_get_movie_info_from_imdb, title, year)
    if movie_data:
        log_method(f"    {EMOJI_SUCCESS_DATA} IMDb: Found '{movie_data['title']}' ({movie_data['year']})")
    return movie_data

async def generate_screenshots_async(video_path: Path, temp_dir: Path, log_method) -> List[Path]:
    log_method(f"    {EMOJI_FFMPEG} FFmpeg: Generating screenshots...")
    try:
        screenshot_paths = await run_in_executor(_sync_generate_screenshots, video_path, temp_dir)
        if screenshot_paths:
            log_method(f"    {EMOJI_SUCCESS_DATA} FFmpeg: Generated {len(screenshot_paths)} screenshots.")
            return screenshot_paths
        else:
            log_method(f"    {EMOJI_WARNING} FFmpeg: Screenshot generation produced no files (e.g., video duration is zero).")
            return []
    except ffmpeg.Error as e:
        stderr_output = e.stderr.decode(errors='ignore').strip() if e.stderr else "No stderr output."
        log_method(f"    {EMOJI_ERROR} FFmpeg: Failed to generate screenshots.")
        log_method(f"      {C_RED}FFmpeg Error: {stderr_output}{C_RESET}")
        return []


async def fetch_rotten_tomatoes_data(session: aiohttp.ClientSession, title: str, year: Optional[str], log_method) -> Optional[Dict[str, Any]]:
    log_method(f"    {EMOJI_QUERY} Rotten Tomatoes: Querying for '{title}'...")
    query = f"{title} {year} rotten tomatoes movie"
    search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
    try:
        async with session.get(search_url, timeout=10) as response:
            response.raise_for_status()
            soup = BeautifulSoup(await response.text(), 'lxml')
            for link in soup.find_all('a'):
                href = link.get('href')
                if href and 'rottentomatoes.com/m/' in href and href.startswith('/url?q='):
                    rt_url = href.split('/url?q=')[1].split('&sa=U')[0]
                    return {'rotten_tomatoes_url': rt_url} # For now, just getting the URL is reliable.
    except Exception:
        pass
    return None

def _sync_fetch_wikipedia_summary(title, year):
    """Synchronous function to get a Wikipedia summary."""
    try:
        search_term = f"{title} ({year} film)" if year else f"{title} (film)"
        page = wikipedia.page(search_term, auto_suggest=True, redirect=True)
        if any("film" in cat.lower() for cat in page.categories):
            return {"wikipedia_url": page.url, "wikipedia_summary": page.summary}
        return None
    except (wikipedia.exceptions.PageError, wikipedia.exceptions.DisambiguationError):
        return None

async def fetch_wikipedia_data(title: str, year: Optional[str], log_method) -> Optional[Dict[str, Any]]:
    log_method(f"    {EMOJI_QUERY} Wikipedia: Querying for '{title}'...")
    data = await run_in_executor(_sync_fetch_wikipedia_summary, title, year)
    return data

async def _fetch_youtube_videos(session: aiohttp.ClientSession, search_query: str, max_videos: int) -> List[Dict[str, str]]:
    """Helper function to perform a YouTube search and parse results."""
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(search_query)}"
    videos = []
    try:
        async with session.get(search_url, timeout=15) as response:
            response.raise_for_status()
            html_content = await response.text()
            match = re.search(r"var ytInitialData = (\{.*?\});", html_content)
            if not match: return []
            data = json.loads(match.group(1))
            video_renderers = data['contents']['twoColumnSearchResultsRenderer']['primaryContents']['sectionListRenderer']['contents'][0]['itemSectionRenderer']['contents']
            for item in video_renderers:
                if 'videoRenderer' in item:
                    video_data = item['videoRenderer']
                    video_id = video_data.get('videoId')
                    video_title = video_data.get('title', {}).get('runs', [{}])[0].get('text')
                    thumbnail_url = video_data.get('thumbnail', {}).get('thumbnails', [{}])[-1].get('url')
                    if video_id and video_title and thumbnail_url:
                        if thumbnail_url.startswith('//'): thumbnail_url = 'https:' + thumbnail_url
                        videos.append({'id': video_id, 'title': video_title, 'thumbnail_url': thumbnail_url})
                    if len(videos) >= max_videos: break
    except Exception:
        return []
    return videos

async def fetch_youtube_trailer(session: aiohttp.ClientSession, title: str, year: Optional[str], log_method) -> Optional[Dict[str, Any]]:
    log_method(f"    {EMOJI_TRAILER} YouTube: Querying for official trailer...")
    search_query = f"{title} {year} official trailer"
    videos = await _fetch_youtube_videos(session, search_query, 1)
    if videos:
        return {'youtube_trailer_url': f"https://www.youtube.com/watch?v={videos[0]['id']}"}
    return None

async def fetch_youtube_reviews(session: aiohttp.ClientSession, title: str, year: Optional[str], log_method, max_videos: int = 4) -> Optional[Dict[str, Any]]:
    log_method(f"    {EMOJI_YOUTUBE} YouTube: Querying for reviews/trivia...")
    search_query = f"{title} {year} review analysis trivia"
    videos = await _fetch_youtube_videos(session, search_query, max_videos)
    return {'youtube_reviews': videos} if videos else None


# --- HTML Generation ---
def generate_html_content(movie_data: Dict[str, Any]) -> str:
    title = movie_data.get("title", "Movie Information")
    header_image_src = movie_data.get("poster_src")
    header_image_html = f'<img src="{header_image_src}" alt="Poster for {title}" class="header-image">' if header_image_src else ""

    description_html = f"<p><em>{movie_data['plot']}</em></p>" if movie_data.get('plot') else ""
    if movie_data.get("wikipedia_summary"):
        description_html += f"<h2>From Wikipedia</h2><p>{movie_data['wikipedia_summary']}</p>"
    if not description_html: description_html = "<p>No description available.</p>"

    def format_score(score):
        try: score = float(score)
        except (ValueError, TypeError): return "N/A"
        color_class = "score-unknown"
        if score >= 9.0: color_class = "score-9x"
        elif score >= 8.0: color_class = "score-8x"
        elif score >= 7.0: color_class = "score-7x"
        elif score >= 6.0: color_class = "score-6x"
        elif score >= 5.0: color_class = "score-5x"
        else: color_class = "score-0x"
        return f'<span class="score {color_class}">{score}</span>'

    imdb_rating_html = format_score(movie_data.get("rating"))

    screenshots_html = ""
    if screenshot_sources := movie_data.get("screenshot_sources", []):
        screenshots_html = "".join([f'<a href="{src}" target="_blank"><img src="{src}" alt="Screenshot of {title}"></a>' for src in screenshot_sources if src])
    if not screenshots_html: screenshots_html = "<p>No screenshots available.</p>"
    
    youtube_videos_html = ""
    if videos := movie_data.get("youtube_reviews", []):
        youtube_videos_html = "".join([f"""<div class="video-card">
            <a href="https://www.youtube.com/watch?v={v['id']}" target="_blank" rel="noopener noreferrer">
                <img src="{v['thumbnail_url']}" alt="Thumbnail for {v['title']}"><div class="video-title">{v['title']}</div>
            </a></div>""" for v in videos])
    if not youtube_videos_html: youtube_videos_html = "<p>No related videos found.</p>"

    def generate_person_link(person):
        return f'<a href="https://www.imdb.com/name/nm{person["id"]}" target="_blank">{person["name"]}</a>'

    links_html = "<ul>"
    def add_link(url, text, is_trailer=False):
        nonlocal links_html
        if url: links_html += f'<li><a href="{url}" target="_blank" rel="noopener noreferrer" class="{"trailer-link" if is_trailer else ""}">{text}</a></li>'
    
    add_link(movie_data.get("youtube_trailer_url"), "▶️ Official Trailer on YouTube", is_trailer=True)
    add_link(f"https://www.imdb.com/title/{movie_data.get('imdb_id')}", "IMDb Page")
    add_link(movie_data.get("rotten_tomatoes_url"), "Rotten Tomatoes Page")
    add_link(movie_data.get("wikipedia_url"), "Wikipedia Page")
    links_html += "</ul>"

    directors_html = ", ".join(generate_person_link(d) for d in movie_data.get("directors", []))
    cast_html = "<ul>" + "".join(f"<li>{generate_person_link(actor)}</li>" for actor in movie_data.get("cast", [])) + "</ul>" if movie_data.get("cast") else "<p>No cast information found.</p>"
    
    details_html = ""
    details_map = {"Director(s)": directors_html, "Genres": ", ".join(movie_data.get("genres", [])), "Year": str(movie_data.get("year", "N/A"))}
    for label, value in details_map.items():
        if value: details_html += f"<tr><th>{label}:</th><td>{value}</td></tr>"
    if details_html: details_html = f"<table class='details-table'>{details_html}</table>"

    return f"""
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{title} - Movie Info</title><style>
body{{font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;margin:0;padding:0;background-color:#1e1e1e;color:#d4d4d4;line-height:1.6}}
.container{{max-width:1400px;margin:20px auto;background-color:#2a2a2a;padding:25px;border-radius:8px;box-shadow:0 0 15px rgba(0,0,0,0.5)}}
header{{text-align:center;margin-bottom:20px;}} .header-image{{max-width:300px;border-radius:6px;margin-bottom:15px;display:block;margin-left:auto;margin-right:auto}}
header h1{{color:#569cd6;font-size:2.8em;margin:0;border-bottom:2px solid #444;padding-bottom:10px}}
h2{{color:#9cf;margin-top:25px;border-bottom:1px solid #444;padding-bottom:8px;font-size:1.8em}}
.main-content-grid{{display:grid;grid-template-columns:1fr;gap:25px}}
@media (min-width:992px){{.main-content-grid{{grid-template-columns:300px 1fr}}.poster-section{{grid-column:1/2;grid-row:1/2}}.content-section{{grid-column:2/3;grid-row:1/2}}}}
.content-section,.screenshots-section,.videos-section,.cast-section{{background-color:#333;padding:20px;border-radius:6px; margin-bottom: 25px;}}
.screenshots-container,.videos-container{{display:grid;grid-template-columns:repeat(auto-fill, minmax(280px, 1fr));gap:15px;margin-top:15px}}
.screenshots-container img{{width:100%;border-radius:4px;border:2px solid #444;transition:transform .2s ease-in-out,box-shadow .2s ease-in-out}}
.screenshots-container img:hover{{transform:scale(1.03);box-shadow:0 0 10px rgba(86,156,214,0.7)}}
.video-card{{background-color:#404040;border-radius:4px;overflow:hidden;transition:transform .2s ease, box-shadow .2s ease;}}
.video-card:hover{{transform:translateY(-5px);box-shadow:0 8px 15px rgba(0,0,0,0.4)}}
.video-card a{{text-decoration:none;color:#d4d4d4;display:block}} .video-card img{{width:100%;display:block;border-bottom:2px solid #569cd6}} .video-title{{padding:10px;font-size:0.9em;min-height:40px}}
.score{{font-weight:700;padding:5px 10px;border-radius:5px;color:#fff;display:inline-block;min-width:30px;text-align:center}}
.score-9x{{background-color:#4CAF50}} .score-8x{{background-color:#8BC34A}} .score-7x{{background-color:#CDDC39;color:#333}} .score-6x{{background-color:#FFEB3B;color:#333}} .score-5x{{background-color:#FFC107;color:#333}} .score-0x{{background-color:#F44336}} .score-unknown{{background-color:#777}}
ul{{list-style-type:none;padding-left:0}} li{{margin-bottom:8px}} a{{color:#569cd6;text-decoration:none}} a:hover{{text-decoration:underline;color:#9cdcfe}}
a.trailer-link{{font-weight:bold;font-size:1.1em;color:#ff8a8a;}} a.trailer-link:hover{{color:#ffc1c1;}}
.details-table{{width:100%;border-collapse:collapse;margin-top:10px;background-color:#383838;border-radius:5px;overflow:hidden}}
.details-table th,.details-table td{{padding:10px 15px;text-align:left;border-bottom:1px solid #4a4a4a}}
.details-table th{{font-weight:700;color:#9cdcfe;width:120px}} .details-table tr:last-child th,.details-table tr:last-child td{{border-bottom:none}}
footer{{text-align:center;margin-top:30px;padding-top:15px;border-top:1px solid #444;font-size:.9em;color:#888}}
</style></head><body><div class="container"><header><h1>{title} ({movie_data.get('year', 'N/A')})</h1></header>
<div class="main-content-grid">
    <aside class="poster-section">{header_image_html}</aside>
    <main class="content-section">
        <h2>Details</h2><p><strong>IMDb Rating:</strong> {imdb_rating_html}</p>{details_html}
        <h2>Links</h2>{links_html}
        <h2>Plot Summary</h2><div class="description-content">{description_html}</div>
    </main>
</div>
<section class="cast-section"><h2>Cast</h2><div class="cast-container">{cast_html}</div></section>
<section class="screenshots-section"><h2>Screenshots</h2><div class="screenshots-container">{screenshots_html}</div></section>
<section class="videos-section"><h2>Related Videos</h2><div class="videos-container">{youtube_videos_html}</div></section>
<footer>Generated by MovieInfoGenerator v{SCRIPT_VERSION} on {time.strftime('%Y-%m-%d %H:%M:%S')}</footer></div></body></html>
"""

# --- Main Processing Logic ---

def get_asset_paths(movie_file_path: Path):
    """Gets all paths related to a movie file."""
    base_name = movie_file_path.stem
    dir_path = movie_file_path.parent
    return {
        "html": dir_path / f"{base_name}.html",
        "assets_dir": dir_path / f"{ASSETS_DIR_NAME_PREFIX}{base_name}_assets",
        "temp_dir": dir_path / TEMP_PROCESSING_SUBDIR / base_name
    }

async def process_movie_file(session: aiohttp.ClientSession, movie_path: Path, force: bool, inline: bool, debug: bool, log_method) -> Dict[str, Any]:
    """Main processing logic for a single movie file, returns stats."""
    start_time = time.monotonic()
    stats = {'status': 'ERROR', 'size_bytes': 0, 'files_generated': 0, 'duration_s': 0, 'failed_sources': []}
    paths = get_asset_paths(movie_path)

    try:
        if force:
            cleanup_generated_files_for_movie(movie_path, lambda msg: None)

        if paths['temp_dir'].exists(): shutil.rmtree(paths['temp_dir'])
        paths['temp_dir'].mkdir(parents=True, exist_ok=True)

        clean_title, year = clean_filename_to_title(movie_path)
        if not clean_title:
            return {**stats, 'status': 'ERROR', 'reason': 'Could not extract title'}
        
        imdb_data = await fetch_imdb_data(clean_title, year, log_method)
        if not imdb_data:
            return {**stats, 'status': 'INSUFFICIENT_DATA', 'reason': 'IMDb lookup failed'}
        
        aggregated_data = imdb_data

        # Create a dictionary of all supplementary async tasks
        tasks = {
            "wikipedia": fetch_wikipedia_data(aggregated_data['title'], aggregated_data.get('year'), log_method),
            "rotten_tomatoes": fetch_rotten_tomatoes_data(session, aggregated_data['title'], aggregated_data.get('year'), log_method),
            "youtube_trailer": fetch_youtube_trailer(session, aggregated_data['title'], aggregated_data.get('year'), log_method),
            "youtube_reviews": fetch_youtube_reviews(session, aggregated_data['title'], aggregated_data.get('year'), log_method),
            "screenshots": generate_screenshots_async(movie_path, paths['temp_dir'], log_method),
        }
        if aggregated_data.get('poster_url'):
            tasks["poster"] = download_file_with_progress(session, aggregated_data['poster_url'], paths['temp_dir'] / 'poster.jpg', log_method, "Poster")

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        task_results = dict(zip(tasks.keys(), results))

        # Process results, updating aggregated_data
        for source_name, data in task_results.items():
            if isinstance(data, Exception):
                stats['failed_sources'].append(source_name)
                if debug: log_method(f"    {EMOJI_ERROR} Task '{source_name}' failed: {data}")
                continue
            if not data:
                stats['failed_sources'].append(source_name)
                continue

            if source_name in ["wikipedia", "rotten_tomatoes", "youtube_trailer", "youtube_reviews"]:
                aggregated_data.update(data)
            elif source_name == "poster":
                temp_poster_path = paths['temp_dir'] / 'poster.jpg'
                if inline:
                    aggregated_data['poster_src'] = encode_image_to_base64_data_uri(temp_poster_path)
                else:
                    paths['assets_dir'].mkdir(exist_ok=True)
                    final_path = paths['assets_dir'] / 'poster.jpg'
                    shutil.move(temp_poster_path, final_path)
                    aggregated_data['poster_src'] = str(final_path.relative_to(movie_path.parent)).replace(os.sep, '/')
            elif source_name == "screenshots":
                screenshot_sources = []
                screenshots_dir = paths['assets_dir'] / SCREENSHOTS_SUBDIR_NAME
                if not inline: screenshots_dir.mkdir(parents=True, exist_ok=True)
                for sc_path in data:
                    if inline:
                        screenshot_sources.append(encode_image_to_base64_data_uri(sc_path))
                    else:
                        final_path = screenshots_dir / sc_path.name
                        shutil.move(sc_path, final_path)
                        screenshot_sources.append(str(final_path.relative_to(movie_path.parent)).replace(os.sep, '/'))
                aggregated_data['screenshot_sources'] = screenshot_sources

        if debug:
            print(f"    {EMOJI_INFO} Final aggregated data for '{aggregated_data['title']}':")
            print(json.dumps(aggregated_data, indent=2, ensure_ascii=False))

        html_output = generate_html_content(aggregated_data)
        paths['html'].write_text(html_output, encoding="utf-8")
        
        stats.update({'status': 'SUCCESS', 'files_generated': 1, 'size_bytes': paths['html'].stat().st_size})
        if not inline and paths['assets_dir'].exists():
            stats['size_bytes'] += sum(f.stat().st_size for f in paths['assets_dir'].rglob('*') if f.is_file())

    except Exception as e:
        log_method(f"    {EMOJI_ERROR} Unexpected error processing '{movie_path.name}': {e}")
        stats['status'] = 'ERROR'
    finally:
        if paths['temp_dir'].exists(): shutil.rmtree(paths['temp_dir'])
        stats['duration_s'] = time.monotonic() - start_time
        return stats

def cleanup_generated_files_for_movie(movie_path: Path, log_method):
    """Cleans up all files generated by the script for a specific movie file."""
    paths = get_asset_paths(movie_path)
    cleaned = False
    if paths['html'].exists():
        paths['html'].unlink(); log_method(f"    {EMOJI_CLEAN} Removed: {paths['html'].name}"); cleaned = True
    if paths['assets_dir'].exists():
        shutil.rmtree(paths['assets_dir']); log_method(f"    {EMOJI_CLEAN} Removed directory: {paths['assets_dir'].name}"); cleaned = True
    return cleaned

async def main():
    parser = argparse.ArgumentParser(description="Generate HTML descriptions for local movie files.", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("paths", nargs='+', metavar="PATH", help="One or more paths to movie files or directories.")
    parser.add_argument("-R", "--recursive", action="store_true", help="Scan directory recursively.")
    parser.add_argument("--force", action="store_true", help="Force regeneration of existing info files.")
    parser.add_argument("--cleanup", action="store_true", help="Remove ALL script-generated files for the target movie(s).")
    parser.add_argument("--inline", action="store_true", help="Embed all images into the HTML as Base64. Creates larger but portable files.")
    parser.add_argument("--no-color", action="store_true", help="Disable color and emoji output.")
    parser.add_argument("--debug", action="store_true", help="Print the final aggregated data dictionary for debugging.")
    args = parser.parse_args()

    setup_display_mode(args.no_color)
    print(f"{EMOJI_ROCKET} Movie Info Generator v{SCRIPT_VERSION} {EMOJI_ROCKET}")

    if not shutil.which('ffmpeg'):
        print(f"{C_RED}{EMOJI_ERROR} ffmpeg is not installed or not in your PATH. It is required to generate screenshots.{C_RESET}")
        sys.exit(1)
    
    movie_files = []
    unique_root_dirs = set()
    for path_str in args.paths:
        target_path = Path(path_str).resolve()
        unique_root_dirs.add(target_path.parent if target_path.is_file() else target_path)

        if args.recursive and target_path.is_dir():
            movie_files.extend(find_movie_files(target_path))
        elif target_path.is_dir():
            movie_files.extend([f for f in target_path.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS])
        elif target_path.is_file() and target_path.suffix.lower() in VIDEO_EXTENSIONS:
            movie_files.append(target_path)
        else:
            print(f"{C_YELLOW}{EMOJI_WARNING} Path is not a valid movie file/directory, skipping: {target_path}{C_RESET}")

    if not movie_files:
        print(f"{EMOJI_SHRUG} No movie files found to process.")
        return

    if args.cleanup:
        print(f"{EMOJI_CLEAN} Cleanup mode enabled...")
        cleaned_count = 0
        for movie_file in movie_files:
            if cleanup_generated_files_for_movie(movie_file, print): cleaned_count += 1
        print(f"{EMOJI_PARTY} Cleanup finished. Removed files for {cleaned_count} movies.")
        return

    total_count = len(movie_files)
    print(f"{EMOJI_SUBDIR} Found {total_count} movie file(s) to process.")
    global_stats = {'SUCCESS': 0, 'SKIPPED': 0, 'INSUFFICIENT_DATA': 0, 'ERROR': 0, 'total_size_bytes': 0}
    global_start_time = time.monotonic()
    
    session = aiohttp.ClientSession(headers={'User-Agent': USER_AGENT})
    try:
        for i, movie_file in enumerate(movie_files):
            print(f"\n{EMOJI_PROCESS} [{i+1}/{total_count}] {movie_file.name}", end="")
            html_file = get_asset_paths(movie_file)['html']
            if html_file.exists() and not args.force:
                print(f" {C_YELLOW}(SKIPPED){C_RESET}")
                global_stats['SKIPPED'] += 1
                continue
            print()
            
            stats = await process_movie_file(session, movie_file, args.force, args.inline, args.debug, print)
            
            global_stats[stats['status']] += 1
            global_stats['total_size_bytes'] += stats['size_bytes']
            
            print(f"  {EMOJI_STATS} Status: {stats['status']} | {EMOJI_CLOCK} Duration: {stats['duration_s']:.2f}s | Size: {format_bytes(stats['size_bytes'])}")
            if stats['failed_sources']:
                print(f"    {C_YELLOW}{EMOJI_WARNING} No data from: {', '.join(stats['failed_sources'])}{C_RESET}")
    finally:
        await session.close()
        # Final cleanup of all temp directories used during the run
        for root_dir in unique_root_dirs:
            temp_dir_to_clean = root_dir / TEMP_PROCESSING_SUBDIR
            if temp_dir_to_clean.exists():
                try:
                    shutil.rmtree(temp_dir_to_clean)
                    print(f"{EMOJI_CLEAN} Cleaned up temporary directory: {temp_dir_to_clean}")
                except OSError as e:
                    print(f"{EMOJI_WARNING} Could not clean up temporary directory {temp_dir_to_clean}: {e}")

    total_duration = time.monotonic() - global_start_time
    print(f"\n{EMOJI_PARTY} All processing finished! {EMOJI_PARTY}")
    print("\n" + "="*20 + f" {EMOJI_STATS} Run Summary " + "="*20)
    print(f"  {EMOJI_CLOCK} Total execution time: {total_duration:.2f} seconds")
    print(f"  - {EMOJI_SUCCESS_HTML} Successful: {global_stats['SUCCESS']}")
    print(f"  - {EMOJI_SKIP} Skipped: {global_stats['SKIPPED']}")
    print(f"  - {EMOJI_SHRUG} Failed (No Data): {global_stats['INSUFFICIENT_DATA']}")
    print(f"  - {EMOJI_ERROR} Failed (Errors): {global_stats['ERROR']}")
    print(f"  Total size of generated files: {format_bytes(global_stats['total_size_bytes'])}")
    print("="*55)
    

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting.")

