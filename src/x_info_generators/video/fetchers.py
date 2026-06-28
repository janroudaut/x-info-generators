import asyncio
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


# --- FreeIMDb API (replaces cinemagoer) ---

# api.imdbapi.dev rate-limits aggressively (429) and occasionally 500s. Retry
# transient failures with exponential backoff so a series' burst of calls
# (search + details + one per owned season) survives a cold run.
_IMDB_RETRY_STATUS = {429, 500, 502, 503, 504}


async def _imdb_get_json(session: aiohttp.ClientSession, url: str, *, retries: int = 4, timeout: int = 15, headers=None):
    delay = 2.0
    last_exc = None
    for attempt in range(retries + 1):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout), headers=headers) as resp:
                if resp.status in _IMDB_RETRY_STATUS and attempt < retries:
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


def _imdb_persons(persons) -> List[Dict[str, str]]:
    if not persons:
        return []
    return [
        {"id": p.get("id", ""), "name": p.get("displayName", "")}
        for p in persons if p.get("displayName")
    ]


async def _fetch_imdb_cast(session: aiohttp.ClientSession, imdb_id: str) -> List[Dict[str, Any]]:
    """Cast with character names and actor photos via /credits."""
    data = await _imdb_get_json(session, f"https://api.imdbapi.dev/titles/{imdb_id}/credits?categories=cast")
    cast = []
    for c in (data or {}).get("credits", [])[:12]:
        name = c.get("name") or {}
        if not name.get("displayName"):
            continue
        chars = c.get("characters") or []
        cast.append({
            "name": name["displayName"],
            "url": f"https://www.imdb.com/name/{name['id']}" if name.get("id") else None,
            "character": ", ".join(chars) if chars else None,
            "image_url": (name.get("primaryImage") or {}).get("url"),
        })
    return cast


async def fetch_imdb_detail(session: aiohttp.ClientSession, imdb_id: str, log) -> Optional[Dict[str, Any]]:
    """Fetch a movie's full metadata from the reliable /titles/{id} endpoint."""
    try:
        detail = await _imdb_get_json(session, f"https://api.imdbapi.dev/titles/{imdb_id}")
        if not detail:
            return None
        try:
            cast = await _fetch_imdb_cast(session, imdb_id)
        except Exception:
            cast = []
        if not cast:  # fallback to the lightweight stars list (names only)
            cast = [{"name": p["name"], "url": f"https://www.imdb.com/name/{p['id']}" if p["id"] else None,
                     "character": None, "image_url": None}
                    for p in _imdb_persons(detail.get("stars", []))[:10]]
        result = {
            "title": detail.get("primaryTitle"),
            "year": detail.get("startYear"),
            "rating": detail.get("rating", {}).get("aggregateRating") if detail.get("rating") else None,
            "plot": detail.get("plot", "Plot summary not available."),
            "poster_url": detail.get("primaryImage", {}).get("url") if detail.get("primaryImage") else None,
            "directors": _imdb_persons(detail.get("directors", [])),
            "cast": cast,
            "genres": detail.get("genres", []),
            "imdb_id": detail.get("id"),
        }
        log(f"    {D.SUCCESS_DATA} IMDb: Found '{result['title']}' ({result['year']})")
        return result
    except Exception as e:
        log(f"    {D.ERROR} IMDb: Error fetching detail for {imdb_id}: {e}")
        return None


async def fetch_imdb_rating(session: aiohttp.ClientSession, imdb_id: str, log) -> Optional[Dict[str, Any]]:
    """Just the IMDb aggregate rating for a title (used to give series an IMDb badge)."""
    try:
        detail = await _imdb_get_json(session, f"https://api.imdbapi.dev/titles/{imdb_id}")
        rating = (detail or {}).get("rating", {}).get("aggregateRating") if detail else None
        return {"rating": rating} if rating is not None else None
    except Exception as e:
        log(f"    {D.ERROR} IMDb: Error fetching rating for {imdb_id}: {e}")
        return None


async def fetch_imdb_data(session: aiohttp.ClientSession, title: str, year: Optional[str], log) -> Optional[Dict[str, Any]]:
    """Fallback movie lookup via the (flaky) IMDb search endpoint."""
    log(f"    {D.QUERY} IMDb: Searching for '{title}' ({year or 'N/A'})...")
    search_url = f"https://api.imdbapi.dev/search/titles?query={urllib.parse.quote(title)}&limit=10"
    try:
        data = await _imdb_get_json(session, search_url)

        results = data.get("titles", []) if isinstance(data, dict) else data if isinstance(data, list) else []
        if not results:
            return None

        # Filter: prefer year match + type=="movie"
        candidates = results
        if year:
            year_matches = [m for m in candidates if str(m.get("startYear", "")) == str(year)]
            if year_matches:
                candidates = year_matches

        movie_matches = [m for m in candidates if m.get("type") == "movie"]
        best = movie_matches[0] if movie_matches else (candidates[0] if candidates else None)
        if not best or not best.get("id"):
            return None

        return await fetch_imdb_detail(session, best["id"], log)
    except Exception as e:
        log(f"    {D.ERROR} IMDb: Error querying: {e}")
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
    data = await _imdb_get_json(session, url, headers=_WIKIDATA_HEADERS)
    return [r["title"] for r in data.get("query", {}).get("search", []) if r.get("title")]


async def _wikidata_qids_label(session, title: str) -> List[str]:
    url = (f"{_WIKIDATA_API}?action=wbsearchentities&search={urllib.parse.quote(title)}"
           "&language=en&type=item&limit=7&format=json")
    data = await _imdb_get_json(session, url, headers=_WIKIDATA_HEADERS)
    return [r["id"] for r in data.get("search", []) if r.get("id")]


async def _wikidata_pick_film(session, qids: List[str], year: Optional[str]) -> Optional[str]:
    """Batch-fetch claims for candidate QIDs and return the best IMDb id: a film
    (P31) matching the year (P577) if possible, else any film, else any with P345."""
    if not qids:
        return None
    url = f"{_WIKIDATA_API}?action=wbgetentities&ids={'|'.join(qids)}&props=claims&format=json"
    entities = (await _imdb_get_json(session, url, headers=_WIKIDATA_HEADERS)).get("entities", {})

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
