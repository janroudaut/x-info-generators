import asyncio
import json
import re
import urllib.parse
from typing import Dict, Any, List, Optional

import aiohttp
import wikipedia
from bs4 import BeautifulSoup

from ..display import DisplayMode as D
from .. import __version__, REPO_URL

USER_AGENT = f"GameInfoGenerator/{__version__} (I'm a kind scraper, called manually and used for personal use <3; +{REPO_URL})"


async def fetch_steam_data(session: aiohttp.ClientSession, game_title: str, log) -> Optional[Dict[str, Any]]:
    log(f"    {D.QUERY} Steam: Querying for '{game_title}'...")
    search_url = f"https://store.steampowered.com/api/storesearch/?term={urllib.parse.quote(game_title)}&l=english&cc=US"
    app_id = None
    game_data: Dict[str, Any] = {}
    try:
        async with session.get(search_url, timeout=10) as response:
            if response.status != 200:
                log(f"    {D.WARNING} Steam: Search request failed {response.status} for '{game_title}'.")
                return None
            search_results = await response.json()
            if search_results.get("total", 0) > 0 and search_results.get("items"):
                best_match = None
                for item in search_results["items"]:
                    if game_title.lower() == item.get("name", "").lower():
                        best_match = item
                        break
                if not best_match:
                    best_match = search_results["items"][0]
                app_id = best_match.get("id")
                if not game_data.get("name"):
                    game_data["name"] = best_match.get("name")
                game_data["app_id"] = app_id
    except Exception as e:
        log(f"    {D.ERROR} Steam: Error during search for '{game_title}': {e}")
        return None

    if app_id:
        details_url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&l=english&cc=US"
        try:
            async with session.get(details_url, timeout=15) as response:
                if response.status == 200:
                    app_details_json = await response.json()
                    data = app_details_json.get(str(app_id), {}).get("data", {})
                    if data:
                        game_data["name"] = data.get("name", game_data.get("name"))
                        # Prefer "about_the_game" (the neutral "About This Game" section)
                        # over "detailed_description", which Steam prefixes with edition/
                        # DLC/pre-order marketing banners.
                        game_data["description_html"] = (
                            data.get("about_the_game") or data.get("detailed_description")
                        )
                        game_data["steam_url"] = f"https://store.steampowered.com/app/{app_id}/"
                        game_data["base_url_for_description_images"] = f"https://store.steampowered.com/app/{app_id}/"
                        game_data["release_date"] = data.get("release_date", {}).get("date")
                        game_data["developers"] = data.get("developers", [])
                        game_data["publishers"] = data.get("publishers", [])
                        game_data["genres"] = [g["description"] for g in data.get("genres", []) if isinstance(g, dict)]
                        game_data["header_image_url"] = data.get("header_image")
                        raw_screenshots = data.get("screenshots", [])
                        if isinstance(raw_screenshots, list):
                            game_data["screenshots"] = [
                                sc["path_full"] for sc in raw_screenshots
                                if isinstance(sc, dict) and "path_full" in sc
                            ]
                        else:
                            game_data["screenshots"] = []
                        if data.get("website"):
                            game_data["website"] = data["website"]
                        if data.get("metacritic"):
                            game_data["metacritic_score_from_steam"] = data["metacritic"].get("score")
                            game_data["metacritic_url_from_steam"] = data["metacritic"].get("url")
                        return game_data
                    else:
                        log(f"    {D.WARNING} Steam: No data in appdetails for app ID {app_id}.")
                else:
                    log(f"    {D.WARNING} Steam: Appdetails request failed {response.status} for app ID {app_id}.")
        except Exception as e:
            log(f"    {D.ERROR} Steam: Error fetching appdetails for '{game_title}': {e}")
    return game_data if game_data.get("name") else None


async def fetch_steam_user_reviews(session: aiohttp.ClientSession, app_id: Optional[str], log) -> Optional[List[Dict[str, str]]]:
    if not app_id:
        return None
    log(f"    {D.QUERY} Steam Reviews: Querying for app ID '{app_id}'...")
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
                        recommended = bool(review.get("voted_up"))
                        steam_reviews_data.append({
                            "source": "Steam User",
                            "recommendation": "Recommended" if recommended else "Not Recommended",
                            "recommended": recommended,
                            "score": f"{votes_up} votes",
                            "snippet": review_text[:300] + "..." if len(review_text) > 300 else review_text,
                            "url": f"https://steamcommunity.com/profiles/{author_id}/recommended/{app_id}/",
                        })
                    return steam_reviews_data
    except Exception:
        pass
    return steam_reviews_data if steam_reviews_data else None


def _extract_jsonld_aggregate_rating(soup: BeautifulSoup) -> Optional[int]:
    """Parse the Metacritic score from the page's JSON-LD aggregateRating.

    Modern Metacritic renders scores client-side, but embeds a
    <script type="application/ld+json"> block with the Metascore.
    """
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            rating = entry.get("aggregateRating")
            if isinstance(rating, dict) and rating.get("ratingValue") is not None:
                try:
                    return int(rating["ratingValue"])
                except (ValueError, TypeError):
                    continue
    return None


async def fetch_metacritic_data(session: aiohttp.ClientSession, game_title: str, log) -> Optional[Dict[str, Any]]:
    log(f"    {D.QUERY} Metacritic: Querying for '{game_title}'...")
    slug_title = re.sub(r'[^\w\s-]', '', game_title.lower())
    slug_title = re.sub(r'\s+', '-', slug_title).strip('-')
    possible_slugs = list(dict.fromkeys([
        slug_title,
        slug_title.replace(":", ""),
        re.sub(r'-edition$', '', slug_title, flags=re.IGNORECASE),
        re.sub(r'-goty$', '', slug_title, flags=re.IGNORECASE),
    ]))
    for current_slug in possible_slugs:
        # New Metacritic URLs no longer include the platform segment.
        page_url = f"https://www.metacritic.com/game/{current_slug}/"
        try:
            async with session.get(page_url, timeout=15, headers={
                "Accept-Language": "en-US,en;q=0.5", "User-Agent": USER_AGENT,
            }) as response:
                if response.status == 200:
                    soup = BeautifulSoup(await response.text(), "lxml")
                    score = _extract_jsonld_aggregate_rating(soup)
                    if score is not None:
                        return {"metacritic_url": page_url, "metacritic_score": score}
                elif response.status not in (403, 404):
                    log(f"    {D.WARNING} Metacritic: Request failed {response.status} for slug '{current_slug}'.")
        except Exception as e:
            log(f"    {D.ERROR} Metacritic: Error scraping '{current_slug}': {e}")
    log(f"    {D.SHRUG} Metacritic: No data found for '{game_title}' after trying all slugs.")
    return None


async def fetch_wikipedia_data(session: aiohttp.ClientSession, game_title: str, log) -> Optional[Dict[str, Any]]:
    log(f"    {D.QUERY} Wikipedia: Querying for '{game_title}'...")
    try:
        def _build_result(page):
            result = {"wikipedia_url": page.url, "description_text": page.summary}
            if page.categories and any("video game" in cat.lower() for cat in page.categories):
                result["is_video_game_page"] = True
            return result

        def sync_search():
            try:
                # Try the exact title first; wikipedia.suggest() often returns a
                # fuzzy/typo'd variant (e.g. "indians jones...") that then 404s.
                try:
                    page = wikipedia.page(game_title, auto_suggest=False, redirect=True)
                    return _build_result(page)
                except wikipedia.exceptions.PageError:
                    suggestion = wikipedia.suggest(game_title)
                    if not suggestion:
                        log(f"    {D.SHRUG} Wikipedia: Page not found for '{game_title}'.")
                        return None
                    page = wikipedia.page(suggestion, auto_suggest=False, redirect=True)
                    return _build_result(page)
            except wikipedia.exceptions.DisambiguationError as e:
                log(f"    {D.WARNING} Wikipedia: Disambiguation for '{game_title}'.")
                if e.options:
                    try:
                        page = wikipedia.page(e.options[0], auto_suggest=False, redirect=True)
                        return {"wikipedia_url": page.url, "description_text": page.summary}
                    except Exception:
                        return None
                return None
            except Exception as e_sync:
                log(f"    {D.ERROR} Wikipedia: Error for '{game_title}': {e_sync}")
                return None

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, sync_search)
    except Exception as e:
        log(f"    {D.ERROR} Wikipedia: Async error for '{game_title}': {e}")
        return None


async def fetch_mobygames_data(session: aiohttp.ClientSession, game_title: str, log) -> Optional[Dict[str, Any]]:
    log(f"    {D.QUERY} MobyGames: Querying for '{game_title}'...")
    search_query = urllib.parse.quote(game_title)
    search_url = f"https://www.mobygames.com/search/?q={search_query}"
    game_data: Dict[str, Any] = {}
    try:
        async with session.get(search_url, timeout=15, headers={"User-Agent": USER_AGENT}) as response:
            if response.status != 200:
                if response.status not in (403, 404):
                    log(f"    {D.WARNING} MobyGames: Search fail {response.status} for '{game_title}'.")
                return None
            html_content = await response.text()
            soup = BeautifulSoup(html_content, "lxml")
            # Game result links look like /game/<id>/<slug>/ — pick the first.
            game_link = next(
                (a.get("href") for a in soup.find_all("a", href=True)
                 if re.search(r"/game/\d+/", a["href"])),
                None,
            )
            if not game_link:
                log(f"    {D.SHRUG} MobyGames: No game link in search for '{game_title}'.")
                return None
            game_page_url = urllib.parse.urljoin("https://www.mobygames.com", game_link)
            game_data["mobygames_url"] = game_page_url

        async with session.get(game_page_url, timeout=15, headers={"User-Agent": USER_AGENT}) as resp:
            if resp.status != 200:
                if resp.status not in (403, 404):
                    log(f"    {D.WARNING} MobyGames: Game page fail {resp.status}.")
                return game_data
            game_html = await resp.text()
            game_soup = BeautifulSoup(game_html, "lxml")
            title_el = game_soup.select_one('h1[itemprop="name"], h1')
            if title_el:
                game_data["name"] = title_el.text.strip()
            desc_el = game_soup.select_one('div[itemprop="description"]')
            if desc_el:
                game_data["description_text"] = desc_el.get_text(separator="\n", strip=True)
            return game_data
    except Exception as e:
        log(f"    {D.ERROR} MobyGames: Error scraping '{game_title}': {e}")
    return game_data if game_data else None
