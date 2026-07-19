import asyncio
import os
import json
import re
import shutil
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional

import aiohttp
import ffmpeg
import wikipedia
from bs4 import BeautifulSoup

from ..display import DisplayMode as D
from ..utils import run_in_executor
from ..cli import WIKIPEDIA_USER_AGENT


def _strip_html(text: Optional[str]) -> Optional[str]:
    """Flatten an HTML snippet (e.g. TVmaze summaries) to text, keeping breaks.

    <br> becomes a single newline and each <p> a blank-line-separated block, so
    the ``linebreaks`` template filter can rebuild paragraphs.
    """
    if not text:
        return None
    soup = BeautifulSoup(text, "html.parser")
    # Sentinel survives get_text(strip=True), which would drop a bare "\n" node.
    sentinel = "\x00"
    for br in soup.find_all("br"):
        br.replace_with(sentinel)
    blocks = soup.find_all("p") or [soup]
    parts = []
    for block in blocks:
        t = re.sub(rf"\s*{sentinel}\s*", "\n", block.get_text(" ", strip=True))
        if t:
            parts.append(t)
    return "\n\n".join(parts) or None


# --- Shared JSON-over-HTTP helper ---

# Public APIs rate-limit (429) and occasionally 500. Retry transient failures
# with exponential backoff so a series' burst of calls (find + details + one
# per owned season) survives a cold run.
_RETRY_STATUS = {429, 500, 502, 503, 504}


async def _get_json_retry(session: aiohttp.ClientSession, url: str, *, retries: int = 4, timeout: int = 15, headers=None):
    delay = 2.0
    last_exc = None
    for attempt in range(retries + 1):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout), headers=headers) as resp:
                if resp.status in _RETRY_STATUS and attempt < retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                resp.raise_for_status()
                return await resp.json()
        except asyncio.TimeoutError as e:
            last_exc = e
            if attempt < retries:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            raise
    if last_exc:
        raise last_exc
    return None


# --- TMDB (replaces the defunct imdbapi.dev) ---

# Requires a free API key (https://www.themoviedb.org/settings/api) in the
# TMDB_API_KEY environment variable. Accepts either a v3 key (hex string,
# passed as ?api_key=) or a v4 read access token (JWT, passed as a Bearer
# header). The Wikidata-resolved IMDb id maps to a TMDB id via /find.
_TMDB_API = "https://api.themoviedb.org/3"
_TMDB_IMG = "https://image.tmdb.org/t/p"


def tmdb_available() -> bool:
    return bool(os.environ.get("TMDB_API_KEY", "").strip())


async def _tmdb_get_json(session: aiohttp.ClientSession, path: str, **params):
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if not key:
        return None  # the CLI warns once at startup
    headers = None
    if "." in key:  # v4 read access token (JWT)
        headers = {"Authorization": f"Bearer {key}"}
    else:  # v3 key
        params["api_key"] = key
    url = f"{_TMDB_API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return await _get_json_retry(session, url, headers=headers)


async def _tmdb_find(session: aiohttp.ClientSession, imdb_id: str):
    """Map an IMDb id to ("movie"|"tv", tmdb result dict), or (None, None)."""
    data = await _tmdb_get_json(session, f"/find/{imdb_id}", external_source="imdb_id")
    for media_type, bucket in (("movie", "movie_results"), ("tv", "tv_results")):
        results = (data or {}).get(bucket) or []
        if results:
            return media_type, results[0]
    return None, None


def _tmdb_rating(node) -> Optional[float]:
    if not node.get("vote_count"):
        return None  # unvoted titles report vote_average 0, not "no data"
    avg = node.get("vote_average")
    return round(avg, 1) if avg else None


def _tmdb_person(p, image_size: Optional[str] = None) -> Dict[str, Any]:
    entry = {
        "name": p.get("name"),
        "url": f"https://www.themoviedb.org/person/{p['id']}" if p.get("id") else None,
    }
    if image_size:
        profile = p.get("profile_path")
        entry["image_url"] = f"{_TMDB_IMG}/{image_size}{profile}" if profile else None
    return entry


async def _fetch_tmdb_movie(session: aiohttp.ClientSession, tmdb_id: int, log) -> Optional[Dict[str, Any]]:
    detail = await _tmdb_get_json(session, f"/movie/{tmdb_id}", append_to_response="credits")
    if not detail:
        return None
    credits = detail.get("credits") or {}
    cast = [
        {**_tmdb_person(c, "w342"), "character": c.get("character") or None}
        for c in (credits.get("cast") or [])[:12] if c.get("name")
    ]
    directors = [_tmdb_person(p) for p in credits.get("crew") or []
                 if p.get("job") == "Director" and p.get("name")]
    release = detail.get("release_date") or ""
    result = {
        "title": detail.get("title"),
        "year": release[:4] or None,
        "rating": _tmdb_rating(detail),
        "plot": detail.get("overview") or "Plot summary not available.",
        "poster_url": f"{_TMDB_IMG}/w780{detail['poster_path']}" if detail.get("poster_path") else None,
        "directors": directors,
        "cast": cast,
        "genres": [g["name"] for g in detail.get("genres") or [] if g.get("name")],
        "runtime_seconds": detail["runtime"] * 60 if detail.get("runtime") else None,
        "imdb_id": detail.get("imdb_id"),
        "tmdb_id": detail.get("id"),
        "tmdb_type": "movie",
    }
    log(f"    {D.SUCCESS_DATA} TMDB: Found '{result['title']}' ({result['year']})")
    return result


async def fetch_tmdb_detail(session: aiohttp.ClientSession, imdb_id: str, log) -> Optional[Dict[str, Any]]:
    """Fetch a movie's full metadata from TMDB via its IMDb id."""
    try:
        media_type, hit = await _tmdb_find(session, imdb_id)
        if media_type != "movie":
            return None
        result = await _fetch_tmdb_movie(session, hit["id"], log)
        if result and not result.get("imdb_id"):
            result["imdb_id"] = imdb_id
        return result
    except Exception as e:
        log(f"    {D.ERROR} TMDB: Error fetching detail for {imdb_id}: {e}")
        return None


async def fetch_tmdb_rating(session: aiohttp.ClientSession, imdb_id: str, log) -> Optional[Dict[str, Any]]:
    """Just the TMDB rating for a title (used to give series a rating badge)."""
    try:
        media_type, hit = await _tmdb_find(session, imdb_id)
        if not hit:
            return None
        rating = _tmdb_rating(hit)
        if rating is None:
            return None
        return {"rating": rating, "tmdb_id": hit.get("id"), "tmdb_type": media_type}
    except Exception as e:
        log(f"    {D.ERROR} TMDB: Error fetching rating for {imdb_id}: {e}")
        return None


async def fetch_tmdb_stills(session: aiohttp.ClientSession, imdb_id: str, n: int, log) -> Optional[List[str]]:
    """Up to ``n`` landscape backdrop image URLs for a title from TMDB.

    Used as an online alternative to local ffmpeg extraction. Prefers textless
    backdrops (iso_639_1 null) over ones carrying a language, then the
    community's vote order; works for both movies and series.
    """
    log(f"    {D.QUERY} TMDB: Fetching stills for {imdb_id}...")
    try:
        media_type, hit = await _tmdb_find(session, imdb_id)
        if not hit:
            return None
        data = await _tmdb_get_json(session, f"/{media_type}/{hit['id']}/images")
        backdrops = (data or {}).get("backdrops") or []
        ordered = sorted(backdrops, key=lambda b: (b.get("iso_639_1") is not None,
                                                   -(b.get("vote_average") or 0)))
        urls = [f"{_TMDB_IMG}/w1280{b['file_path']}" for b in ordered if b.get("file_path")][:n]
        if urls:
            log(f"    {D.SUCCESS_DATA} TMDB: {len(urls)} still(s) found.")
        return urls or None
    except Exception as e:
        log(f"    {D.ERROR} TMDB: Error fetching stills for {imdb_id}: {e}")
        return None


async def fetch_tmdb_search(session: aiohttp.ClientSession, title: str, year: Optional[str], log) -> Optional[Dict[str, Any]]:
    """Fallback movie lookup via TMDB search (when Wikidata yields no IMDb id)."""
    log(f"    {D.QUERY} TMDB: Searching for '{title}' ({year or 'N/A'})...")
    try:
        params = {"query": title}
        if year:
            params["primary_release_year"] = year
        data = await _tmdb_get_json(session, "/search/movie", **params)
        results = (data or {}).get("results") or []
        if not results and year:  # year too strict? retry without it
            data = await _tmdb_get_json(session, "/search/movie", query=title)
            results = (data or {}).get("results") or []
        if not results:
            return None
        return await _fetch_tmdb_movie(session, results[0]["id"], log)
    except Exception as e:
        log(f"    {D.ERROR} TMDB: Error querying: {e}")
        return None


# --- Wikidata (reliable IMDb id resolver for movies) ---

# Wikidata "instance of" (P31) values that count as a film.
_WIKIDATA_FILM_TYPES = {"Q11424", "Q24862", "Q202866", "Q506240", "Q24869"}
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
# Wikimedia rejects generic User-Agents with HTTP 403; a descriptive UA with
# contact info is required by their robot policy.
_WIKIDATA_HEADERS = {"User-Agent": WIKIPEDIA_USER_AGENT}


async def _wikidata_qids_fulltext(session, query: str) -> List[str]:
    """Full-text (CirrusSearch) item search — finds films whose title is a common
    word (e.g. 'Ferdinand'), which a label-only search misses."""
    url = (f"{_WIKIDATA_API}?action=query&list=search&format=json&srlimit=7"
           f"&srsearch={urllib.parse.quote(query)}")
    data = await _get_json_retry(session, url, headers=_WIKIDATA_HEADERS)
    return [r["title"] for r in data.get("query", {}).get("search", []) if r.get("title")]


async def _wikidata_qids_label(session, title: str) -> List[str]:
    url = (f"{_WIKIDATA_API}?action=wbsearchentities&search={urllib.parse.quote(title)}"
           "&language=en&type=item&limit=7&format=json")
    data = await _get_json_retry(session, url, headers=_WIKIDATA_HEADERS)
    return [r["id"] for r in data.get("search", []) if r.get("id")]


async def _wikidata_pick_film(session, qids: List[str], year: Optional[str]) -> Optional[str]:
    """Batch-fetch claims for candidate QIDs and return the best IMDb id: a film
    (P31) matching the year (P577) if possible, else any film, else any with P345."""
    if not qids:
        return None
    url = f"{_WIKIDATA_API}?action=wbgetentities&ids={'|'.join(qids)}&props=claims&format=json"
    entities = (await _get_json_retry(session, url, headers=_WIKIDATA_HEADERS)).get("entities", {})

    def imdb_of(claims):
        c = claims.get("P345")
        return c[0]["mainsnak"]["datavalue"]["value"] if c else None

    def types_of(claims):
        return {c["mainsnak"].get("datavalue", {}).get("value", {}).get("id")
                for c in claims.get("P31", [])}

    def year_of(claims):
        for c in claims.get("P577", []):
            t = c["mainsnak"].get("datavalue", {}).get("value", {}).get("time")
            if t:
                return t[1:5]  # "+1999-..." -> "1999"
        return None

    films, fallback = [], None
    for qid in qids:  # preserve search relevance order
        claims = entities.get(qid, {}).get("claims", {})
        imdb = imdb_of(claims)
        if not imdb:
            continue
        fallback = fallback or imdb
        if types_of(claims) & _WIKIDATA_FILM_TYPES:
            films.append((imdb, year_of(claims)))

    if year:
        for imdb, y in films:
            if y == str(year):
                return imdb
    return films[0][0] if films else fallback


async def resolve_imdb_id_wikidata(session: aiohttp.ClientSession, title: str, year: Optional[str], log) -> Optional[str]:
    """Resolve a film's IMDb id via Wikidata, bypassing the flaky IMDb search.

    Tries a full-text search with year/'film' context first (robust for
    common-word titles), then falls back to a plain label search.
    """
    log(f"    {D.QUERY} Wikidata: Resolving IMDb id for '{title}'...")
    try:
        query = f"{title} {year} film" if year else f"{title} film"
        imdb = await _wikidata_pick_film(session, await _wikidata_qids_fulltext(session, query), year)
        if not imdb:
            imdb = await _wikidata_pick_film(session, await _wikidata_qids_label(session, title), year)
        if imdb:
            log(f"    {D.SUCCESS_DATA} Wikidata: {imdb}")
        else:
            log(f"    {D.SHRUG} Wikidata: no IMDb id found for '{title}'.")
        return imdb
    except Exception as e:
        log(f"    {D.ERROR} Wikidata: Error resolving: {e}")
        return None


# --- TVmaze (TV series metadata + episodes + cast in one call) ---

async def fetch_tvmaze_series(session: aiohttp.ClientSession, title: str, log) -> Optional[Dict[str, Any]]:
    log(f"    {D.QUERY} TVmaze: Querying series '{title}'...")
    url = ("https://api.tvmaze.com/singlesearch/shows"
           f"?q={urllib.parse.quote(title)}&embed[]=episodes&embed[]=cast")
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            d = await resp.json()

        embedded = d.get("_embedded", {})
        network = (d.get("network") or d.get("webChannel") or {}) or {}
        rating = (d.get("rating") or {}).get("average")

        episodes_by_season: Dict[int, List[Dict[str, Any]]] = {}
        for e in embedded.get("episodes", []):
            try:
                season = int(e.get("season"))
                number = int(e.get("number"))
            except (TypeError, ValueError):
                continue
            episodes_by_season.setdefault(season, []).append({
                "number": number,
                "title": e.get("name") or f"Episode {number}",
                "plot": _strip_html(e.get("summary")),
                "rating": (e.get("rating") or {}).get("average"),
            })
        for eps in episodes_by_season.values():
            eps.sort(key=lambda x: x["number"])

        # One actor may voice several characters (e.g. Seth MacFarlane) — dedupe by
        # person and join their roles.
        cast = []
        by_person = {}
        for c in embedded.get("cast", []):
            person = c.get("person") or {}
            char = (c.get("character") or {}).get("name")
            pid = person.get("id")
            if not person.get("name"):
                continue
            if pid in by_person:
                if char:
                    existing = by_person[pid]
                    existing["character"] = ", ".join(filter(None, [existing["character"], char]))
                continue
            entry = {
                "name": person["name"],
                "url": f"https://www.tvmaze.com/people/{person['id']}" if pid else None,
                "character": char,
                "image_url": (person.get("image") or {}).get("medium"),
            }
            by_person[pid] = entry
            cast.append(entry)
            if len(cast) >= 12:
                break

        premiered = d.get("premiered") or ""
        ended = d.get("ended") or ""
        result = {
            "title": d.get("name"),
            "year": premiered[:4] or None,
            "end_year": ended[:4] or None,
            "rating": rating,
            "plot": _strip_html(d.get("summary")),
            "poster_url": (d.get("image") or {}).get("original"),
            "genres": d.get("genres", []),
            "network": network.get("name"),
            "episode_runtime": d.get("averageRuntime") or d.get("runtime"),
            "cast": cast,
            "imdb_id": (d.get("externals") or {}).get("imdb"),
            "episodes_by_season": episodes_by_season,
        }
        log(f"    {D.SUCCESS_DATA} TVmaze: Found '{result['title']}' ({result['year']})")
        return result
    except Exception as e:
        log(f"    {D.ERROR} TVmaze: Error querying series: {e}")
        return None


# --- FFmpeg screenshots ---

def _sync_generate_screenshots(video_path: Path, output_dir: Path, num_screenshots: int = 4) -> List[Path]:
    probe = ffmpeg.probe(str(video_path))
    duration = float(probe["format"]["duration"])
    screenshot_paths = []
    for i in range(num_screenshots):
        timestamp = duration * ((i + 1) / (num_screenshots + 1))
        output_file = output_dir / f"screenshot_{i + 1}.jpg"
        (
            ffmpeg.input(str(video_path), ss=timestamp)
            .output(str(output_file), vframes=1, **{"q:v": 3})
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        screenshot_paths.append(output_file)
    return screenshot_paths


async def generate_screenshots_async(video_path: Path, temp_dir: Path, num_screenshots: int, log) -> List[Path]:
    if not shutil.which("ffmpeg"):
        return []  # ffmpeg not installed — skip screenshots (warned once in the CLI)
    log(f"    {D.FFMPEG} FFmpeg: Generating {num_screenshots} screenshots...")
    try:
        screenshot_paths = await run_in_executor(_sync_generate_screenshots, video_path, temp_dir, num_screenshots)
        if screenshot_paths:
            log(f"    {D.SUCCESS_DATA} FFmpeg: Generated {len(screenshot_paths)} screenshots.")
            return screenshot_paths
        else:
            log(f"    {D.WARNING} FFmpeg: Screenshot generation produced no files.")
            return []
    except ffmpeg.Error as e:
        stderr_output = e.stderr.decode(errors="ignore").strip() if e.stderr else "No stderr output."
        log(f"    {D.ERROR} FFmpeg: Failed to generate screenshots.")
        log(f"      {D.C_RED}FFmpeg Error: {stderr_output}{D.C_RESET}")
        return []


# --- Media info (ffprobe) ---

_CHANNEL_LAYOUTS = {1: "1.0", 2: "2.0", 3: "2.1", 6: "5.1", 7: "6.1", 8: "7.1"}


def _resolution_label(width: Optional[int], height: Optional[int]) -> Optional[str]:
    # Label by width — scope crops shrink the height (e.g. 1920×816 is 1080p).
    w = width or 0
    for min_w, label in ((3200, "2160p"), (2200, "1440p"), (1800, "1080p"), (1000, "720p")):
        if w >= min_w:
            return label
    return "SD" if w else None


def _sync_probe_media_info(video_path: Path) -> Optional[Dict[str, Any]]:
    streams = ffmpeg.probe(str(video_path)).get("streams", [])
    video, audio, subtitles = None, [], []
    for s in streams:
        tags = s.get("tags") or {}
        lang = (tags.get("language") or "und").lower()
        kind = s.get("codec_type")
        if kind == "video" and not video:
            w, h = s.get("width"), s.get("height")
            video = {"width": w, "height": h, "label": _resolution_label(w, h),
                     "codec": (s.get("codec_name") or "").upper() or None}
        elif kind == "audio":
            audio.append({"lang": lang, "codec": (s.get("codec_name") or "").upper() or None,
                          "channels": _CHANNEL_LAYOUTS.get(s.get("channels")),
                          "title": tags.get("title")})
        elif kind == "subtitle":
            subtitles.append({"lang": lang, "title": tags.get("title")})
    if not (video or audio or subtitles):
        return None
    return {"video": video, "audio": audio, "subtitles": subtitles}


async def probe_media_info(video_path: Path, log) -> Optional[Dict[str, Any]]:
    """Resolution + audio/subtitle tracks of the local file, or None (no
    ffprobe, unreadable file…)."""
    if not shutil.which("ffprobe"):
        return None
    try:
        info = await run_in_executor(_sync_probe_media_info, video_path)
        if info:
            n_a, n_s = len(info["audio"]), len(info["subtitles"])
            log(f"    {D.SUCCESS_DATA} ffprobe: {info['video']['label'] if info['video'] else '?'}, "
                f"{n_a} audio, {n_s} subtitle(s)")
        return info
    except Exception:
        return None  # e.g. name-only generation on a stub file


# --- Rotten Tomatoes ---

_RT_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _parse_rt_scores(html: str) -> Dict[str, Any]:
    """Extract Tomatometer (critics) and Popcornmeter (audience) from an RT page."""
    out: Dict[str, Any] = {}
    try:
        tag = BeautifulSoup(html, "html.parser").find("script", id="media-scorecard-json")
        if not tag or not tag.string:
            return out
        data = json.loads(tag.string)

        def _score(node):
            try:
                return int(node.get("score"))
            except (TypeError, ValueError, AttributeError):
                return None

        critics = data.get("criticsScore") or {}
        audience = data.get("audienceScore") or {}
        out["rt_critics_score"] = _score(critics)
        out["rt_critics_certified"] = bool(critics.get("certified"))
        out["rt_audience_score"] = _score(audience)
    except Exception:
        pass
    return out


async def fetch_rotten_tomatoes_data(session: aiohttp.ClientSession, title: str, year: Optional[str], log, kind: str = "movie") -> Optional[Dict[str, Any]]:
    log(f"    {D.QUERY} Rotten Tomatoes: Querying for '{title}'...")
    # Resolve the RT page directly by slug instead of scraping Google (which blocks
    # scrapers and is the biggest IP-ban risk). RT slugs are the lowercased title
    # with non-alphanumeric runs collapsed to underscores; remakes append the year.
    section = "tv" if kind == "series" else "m"
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    if not slug:
        return None
    candidates = [slug] + ([f"{slug}_{year}"] if year else [])
    for slug_try in candidates:
        rt_url = f"https://www.rottentomatoes.com/{section}/{slug_try}"
        try:
            async with session.get(
                rt_url, timeout=aiohttp.ClientTimeout(total=10),
                headers={"User-Agent": _RT_BROWSER_UA},
            ) as response:
                if response.status == 200:
                    html = await response.text()
                    result = {"rotten_tomatoes_url": str(response.url)}
                    result.update(_parse_rt_scores(html))
                    return result
        except Exception:
            continue
    return None


# --- Wikipedia ---

def _sync_fetch_wikipedia_summary(title: str, year: Optional[str], kind: str):
    category_keyword = "television" if kind == "series" else "film"
    descriptor = "TV series" if kind == "series" else "film"
    try:
        search_term = f"{title} ({year} {descriptor})" if year else f"{title} ({descriptor})"
        page = wikipedia.page(search_term, auto_suggest=True, redirect=True)
        if any(category_keyword in cat.lower() for cat in page.categories):
            return {"wikipedia_url": page.url, "wikipedia_summary": page.summary}
        return None
    except (wikipedia.exceptions.PageError, wikipedia.exceptions.DisambiguationError):
        return None


async def fetch_wikipedia_data(title: str, year: Optional[str], log, kind: str = "film") -> Optional[Dict[str, Any]]:
    log(f"    {D.QUERY} Wikipedia: Querying for '{title}'...")
    return await run_in_executor(_sync_fetch_wikipedia_summary, title, year, kind)


# --- YouTube ---

async def _fetch_youtube_videos(session: aiohttp.ClientSession, search_query: str, max_videos: int) -> List[Dict[str, str]]:
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(search_query)}"
    videos = []
    try:
        async with session.get(search_url, timeout=15) as response:
            response.raise_for_status()
            html_content = await response.text()
            match = re.search(r"var ytInitialData = (\{.*?\});", html_content)
            if not match:
                return []
            data = json.loads(match.group(1))
            video_renderers = (
                data["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"]
                ["sectionListRenderer"]["contents"][0]["itemSectionRenderer"]["contents"]
            )
            for item in video_renderers:
                if "videoRenderer" in item:
                    vd = item["videoRenderer"]
                    video_id = vd.get("videoId")
                    video_title = vd.get("title", {}).get("runs", [{}])[0].get("text")
                    thumbnail_url = vd.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url")
                    if video_id and video_title and thumbnail_url:
                        if thumbnail_url.startswith("//"):
                            thumbnail_url = "https:" + thumbnail_url
                        videos.append({"id": video_id, "title": video_title, "thumbnail_url": thumbnail_url})
                    if len(videos) >= max_videos:
                        break
    except Exception:
        return []
    return videos


async def fetch_youtube_trailer(session: aiohttp.ClientSession, title: str, year: Optional[str], log) -> Optional[Dict[str, Any]]:
    log(f"    {D.TRAILER} YouTube: Querying for official trailer...")
    search_query = f"{title} {year} official trailer"
    videos = await _fetch_youtube_videos(session, search_query, 1)
    if videos:
        return {"youtube_trailer_url": f"https://www.youtube.com/watch?v={videos[0]['id']}"}
    return None


async def fetch_youtube_reviews(session: aiohttp.ClientSession, title: str, year: Optional[str], log, max_videos: int = 4) -> Optional[Dict[str, Any]]:
    log(f"    {D.YOUTUBE} YouTube: Querying for reviews/trivia...")
    search_query = f"{title} {year} review analysis trivia"
    videos = await _fetch_youtube_videos(session, search_query, max_videos)
    return {"youtube_reviews": videos} if videos else None
