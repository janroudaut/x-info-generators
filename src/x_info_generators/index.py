"""Build a browsable catalog (``index.html``) from already-generated pages.

The source of truth is the generated HTML on disk — there is no manifest or any
other sidecar file. Pages are located by scanning the given roots and recognised
by structure produced by the content templates (``templates/*.html.j2``). The
selectors below are the contract with those templates; keep them in sync if the
templates change.

Recognised hooks:
- ``<title>`` suffix encodes kind + title: "… - Movie Info" / "… - Series Info" /
  "… - Game Info". Season pages ("… - Season N") therefore never match.
- ``img.header-image`` — poster (movie/series) or header (game), a base64 data URI.
- ``.ratings .rating-badge`` (video) → ``.rating-name`` + ``.score``.
- ``#scores .score-section .score`` (game) → Metacritic.
- ``table.details-table`` rows (Genres / Year / Years / Released).
"""

import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from tqdm import tqdm

from .display import DisplayMode as D
from .images import downscale_data_uri
from .templates import render_template
from . import __version__

_TITLE_SUFFIXES = {
    " - Movie Info": "movie",
    " - Series Info": "series",
    " - Game Info": "game",
}
_H1_YEAR_RE = re.compile(r"\((\d{4})")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _num(text: str) -> Optional[float]:
    m = _NUM_RE.search(text or "")
    return float(m.group()) if m else None


def _details_rows(soup) -> Dict[str, str]:
    """Map detail-table headers (lowercased, no trailing ':') to their values."""
    rows: Dict[str, str] = {}
    for table in soup.select("table.details-table"):
        for tr in table.find_all("tr"):
            th, td = tr.find("th"), tr.find("td")
            if th and td:
                key = th.get_text(strip=True).rstrip(":").lower()
                rows.setdefault(key, td.get_text(" ", strip=True))
    return rows


def _parse_ratings(soup, kind: str) -> Dict[str, float]:
    ratings: Dict[str, float] = {}
    if kind == "game":
        score = soup.select_one("#scores .score-section .score")
        val = _num(score.get_text()) if score else None
        if val is not None:
            ratings["metacritic"] = int(val)
        return ratings
    for badge in soup.select(".ratings .rating-badge"):
        name_el, score_el = badge.select_one(".rating-name"), badge.select_one(".score")
        if not name_el or not score_el:
            continue
        val = _num(score_el.get_text())
        if val is None:
            continue
        name = name_el.get_text()
        if "TMDB" in name:
            ratings["tmdb"] = val
        elif "IMDb" in name:  # pages generated before the TMDB migration
            ratings["imdb"] = val
        elif "Tomatometer" in name:
            ratings["rt_critics"] = int(val)
        elif "Audience" in name:
            ratings["rt_audience"] = int(val)
    return ratings


def parse_page(html_path: Path) -> Optional[Dict]:
    """Extract catalog metadata from a generated page, or ``None`` if unrecognised."""
    try:
        html = html_path.read_text(encoding="utf-8")
    except Exception:
        return None
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("title")
    if not title_tag:
        return None
    title_text = title_tag.get_text()
    kind = title = None
    for suffix, k in _TITLE_SUFFIXES.items():
        if title_text.endswith(suffix):
            kind = k
            title = title_text[: -len(suffix)].strip()
            break
    if not kind:
        return None  # season page, or HTML not produced by this tool

    year = None
    h1 = soup.find("h1")
    if h1:
        m = _H1_YEAR_RE.search(h1.get_text())
        if m:
            year = m.group(1)
    if not year:
        rows = _details_rows(soup)
        for key in ("year", "years", "released"):
            if key in rows:
                m = _YEAR_RE.search(rows[key])
                if m:
                    year = m.group()
                    break

    # Some titles embed the year (e.g. a game named "Beneath a Steel Sky (1994)").
    # Drop a trailing "(<year>)" so it isn't shown twice alongside the year field.
    if year and title:
        title = re.sub(r"\s*\(" + re.escape(year) + r"\)\s*$", "", title).strip()

    rows = _details_rows(soup)
    genres = []
    grow = rows.get("genres")
    if grow:
        genres = [g.strip() for g in grow.split(",") if g.strip()]

    # Movies expose "Runtime" (e.g. "1h 21min"); series expose "Episode length"
    # (e.g. "~30 min"). The value is already display-formatted by the templates.
    runtime = rows.get("runtime") or rows.get("episode length")

    thumb = None
    img = soup.select_one("img.header-image")
    if img and img.get("src"):
        thumb = downscale_data_uri(img["src"])

    # People the search box can match: directors + cast — videos only (matching
    # a game by its studio isn't useful).
    people = []
    if kind != "game":
        if "director(s)" in rows:
            people += [p.strip() for p in rows["director(s)"].split(",") if p.strip()]
        for el in soup.select(".cast-list .cast-info"):
            name_el = el.select_one("a, span:not(.cast-char)")
            if name_el:
                people.append(name_el.get_text(strip=True))
        seen = set()
        people = [p for p in people if not (p.lower() in seen or seen.add(p.lower()))]

    return {
        "people": people,
        "kind": kind,
        "title": title,
        "year": year,
        "runtime": runtime,
        "ratings": _parse_ratings(soup, kind),
        "genres": genres,
        "thumb": thumb,
        "html_path": str(html_path),
    }


def _sort_score(ratings: Dict[str, float]) -> float:
    """A single 0–10 value for sorting across mixed rating scales."""
    if "tmdb" in ratings:
        return ratings["tmdb"]
    if "imdb" in ratings:
        return ratings["imdb"]
    if "rt_critics" in ratings:
        return ratings["rt_critics"] / 10
    if "metacritic" in ratings:
        return ratings["metacritic"] / 10
    if "rt_audience" in ratings:
        return ratings["rt_audience"] / 10
    return 0.0


_WSL_MOUNT_RE = re.compile(r"^/mnt/([a-zA-Z])(/.*)?$")


def _windows_file_uri(path: Path) -> str:
    """Turn a /mnt/<letter>/… WSL path into a Windows ``file:///D:/…`` URI.

    Non-mount paths fall back to a regular ``file://`` URI.
    """
    m = _WSL_MOUNT_RE.match(path.as_posix())
    if not m:
        return path.as_uri()
    drive = m.group(1).upper()
    rest = m.group(2) or "/"
    return f"file:///{drive}:{urllib.parse.quote(rest)}"


def _href(out_dir: Path, html_path: Path, wsl: bool = False) -> str:
    """Link from the index to a page.

    Default: a relative path (best for same-drive catalogs; works in WSL *and*
    Windows). With ``wsl=True``: an absolute Windows ``file://`` URI, so a catalog
    built under WSL (paths like /mnt/d/…) opens correctly in a Windows browser,
    even when the catalog and its pages sit on different drives.
    """
    if wsl:
        return _windows_file_uri(html_path)
    try:
        rel = os.path.relpath(html_path, out_dir)
        return urllib.parse.quote(rel.replace(os.sep, "/"))
    except ValueError:  # different drive on native Windows
        return html_path.as_uri()


def _iter_html(roots, output_path: Path, max_depth: int = 5):
    """Yield ``(candidate, scan_root)`` pairs for ``*.html`` under ``roots``, deduped.

    Walks with ``os.walk`` (following symlinked directories) and avoids a per-file
    ``resolve()``. ``max_depth`` caps recursion depth (0 = the root itself). The
    root is yielded alongside so callers can derive root-relative paths.
    """
    seen = set()
    for root in roots:
        root = Path(root).resolve()
        if root.is_file():
            if root.suffix.lower() == ".html" and root != output_path:
                seen.add(root)
                yield root, root.parent
            continue
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
            depth = len(Path(dirpath).parts) - base_depth
            if depth >= max_depth:
                dirnames[:] = []
            for fn in filenames:
                if not fn.lower().endswith(".html"):
                    continue
                f = Path(dirpath, fn)
                if f == output_path or f in seen:
                    continue
                seen.add(f)
                yield f, root


_KIND_TITLE = {"game": "Games", "movie": "Movies", "series": "Series"}


def build_catalog(roots, output_path, log: Callable, max_depth: int = 5,
                  wsl: bool = False, title: str | None = None) -> Tuple[int, Dict[str, int]]:
    """Scan ``roots`` for generated pages and write a self-contained ``index.html``.

    Pages are found and parsed in a single stream (no upfront full-tree scan).
    """
    output_path = Path(output_path).resolve()
    out_dir = output_path.parent
    roots = [Path(r) for r in roots]
    log(f"{D.SUBDIR} Scanning {len(roots)} path(s) for generated pages (max depth {max_depth}):")
    for r in roots:
        log(f"    {r}")

    entries: List[Dict] = []
    by_kind = {"game": 0, "movie": 0, "series": 0}
    skipped = 0
    bar = tqdm(desc=f"{D.PROCESS} Scanning", unit=" file", leave=True)
    for f, scan_root in _iter_html(roots, output_path, max_depth):
        bar.update(1)
        rec = parse_page(f)
        if not rec:
            skipped += 1
            continue
        rec["href"] = _href(out_dir, f, wsl)
        # Root-relative directories for the search box — videos only. Years are
        # stripped so folder names never leak into text search (that goes
        # through data-year).
        rec["search_path"] = ""
        if rec["kind"] != "game":
            try:
                rel_dir = f.parent.relative_to(scan_root).as_posix()
            except ValueError:
                rel_dir = f.parent.name
            if rel_dir != ".":
                path = re.sub(r"\b(19|20)\d{2}\b", " ", rel_dir.lower())
                path = re.sub(r"[(\[]\s*[-–]?\s*[)\]]", " ", path)
                rec["search_path"] = re.sub(r"\s+", " ", path).strip()
        rec["sort_score"] = _sort_score(rec["ratings"])
        entries.append(rec)
        by_kind[rec["kind"]] += 1
        if D.PROCESS:  # emojis enabled (cleared to "" under --no-color)
            bar.set_postfix_str(f"🎮 {by_kind['game']}  🎬 {by_kind['movie']}  📺 {by_kind['series']}")
        else:
            bar.set_postfix_str(f"{by_kind['game']}g/{by_kind['movie']}m/{by_kind['series']}s")
        year = f" ({rec['year']})" if rec["year"] else ""
        bar.write(f"  {D.SUCCESS_DATA} [{len(entries)}] {rec['kind']:<6} {rec['title']}{year}")
    bar.close()

    log(f"{D.INFO} Recognized {len(entries)} page(s), skipped {skipped} (season/other).")
    entries.sort(key=lambda e: e["title"].lower())
    log(f"{D.PROCESS} Rendering catalog → {output_path}")

    # With a single kind present, default the title to that kind and drop the type
    # filter (nothing to filter). Otherwise fall back to a generic "Catalog".
    present_kinds = [k for k in ("game", "movie", "series") if by_kind[k]]
    if title is None:
        title = _KIND_TITLE[present_kinds[0]] if len(present_kinds) == 1 else "Catalog"

    # Genre filter data: dedupe case-insensitively but keep the first-seen
    # display casing, count occurrences, sort alphabetically.
    genre_display: Dict[str, str] = {}
    genre_counts: Dict[str, int] = {}
    for e in entries:
        for g in e["genres"]:
            key = g.lower()
            genre_display.setdefault(key, g)
            genre_counts[key] = genre_counts.get(key, 0) + 1
    all_genres = [{"value": k, "label": genre_display[k], "count": genre_counts[k]}
                  for k in sorted(genre_display)]

    html = render_template(
        "index.html.j2",
        entries=entries,
        by_kind=by_kind,
        total=len(entries),
        title=title,
        all_genres=all_genres,
        show_type_filter=len(present_kinds) > 1,
        generator_name="CatalogIndexGenerator",
        version=__version__,
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    # Write atomically: temp file in the same directory + os.replace (atomic rename).
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(html, encoding="utf-8")
        os.replace(tmp, output_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return len(entries), by_kind
