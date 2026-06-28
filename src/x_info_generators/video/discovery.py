"""Content-based classification of video files into media items.

The directory name is treated as a meaningless organizational placeholder
("old", "films 2024", a "collection"…). What an item *is* — a standalone movie
or an episode of a series — is decided from the file/episode tokens, never from
the folder name. Episodes are grouped into one series item regardless of where
they live (dedicated ``Season N`` subfolders or loose at the root); every other
video becomes its own movie item.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from .processing import (
    NOISE_REGEX, clean_filename_to_title, _get_html_path,
)

# Primary token: S01E01 / S37E01 / S01.E01. Secondary: 1x05 (guarded by word
# boundaries so codec tags like "x265" — no digit before the 'x' — never match).
_EPISODE_PATTERNS = [
    re.compile(r'[Ss](\d{1,2})[\s._-]?[Ee](\d{1,2})'),
    re.compile(r'\b(\d{1,2})x(\d{2})\b'),
]

_SEASON_FOLDER_RE = re.compile(r'(?i)^(season[\s._-]*\d+|s\d{1,2})\b')
_ILLEGAL_FS = re.compile(r'[<>:"/\\|?*]')
_YEAR_RE = re.compile(r'\b(19[0-9]{2}|20[0-3][0-9])\b')


@dataclass
class Episode:
    season: int
    number: int
    path: Path


@dataclass
class SeasonGroup:
    number: int
    episodes: List[Episode]
    folder: Optional[Path] = None      # dedicated folder distinct from series root
    html_path: Optional[Path] = None   # season page, only when folder is dedicated


@dataclass
class MediaItem:
    kind: str                          # "movie" | "series"
    title: str
    year: Optional[str] = None
    html_path: Optional[Path] = None   # primary output
    video_path: Optional[Path] = None  # movies
    root: Optional[Path] = None        # series
    seasons: List[SeasonGroup] = field(default_factory=list)

    def all_html_paths(self) -> List[Path]:
        """Primary page + any season pages (used for skip/cleanup)."""
        paths = [self.html_path]
        paths.extend(s.html_path for s in self.seasons if s.html_path)
        return [p for p in paths if p]

    def owned_episodes(self) -> set:
        return {(s.number, e.number) for s in self.seasons for e in s.episodes}


def parse_episode(name: str) -> Optional[Tuple[int, int, int]]:
    """Return ``(season, episode, match_start)`` if name contains an episode token."""
    for pat in _EPISODE_PATTERNS:
        m = pat.search(name)
        if m:
            return int(m.group(1)), int(m.group(2)), m.start()
    return None


def _clean_series_name(raw: str) -> str:
    name = raw.replace('.', ' ').replace('_', ' ').replace('-', ' ')
    name = NOISE_REGEX.sub('', name)
    return re.sub(r'\s+', ' ', name).strip(' -')


def _clean_folder_title(folder: str) -> str:
    name = re.sub(r'\[.*?\]', '', folder)
    name = re.sub(r'\(.*?\)', '', name)
    name = re.sub(r'(?i)\bseason[\s._-]*\d+\b', '', name)
    name = re.sub(r'(?i)[\s._-]s\d{1,2}\b', '', name)
    name = name.replace('.', ' ').replace('_', ' ')
    name = NOISE_REGEX.sub('', name)
    return re.sub(r'\s+', ' ', name).strip(' -')


def _series_name_from_path(path: Path, match_start: int) -> str:
    """Series title from the filename prefix, falling back to ancestor folders."""
    prefix = _clean_series_name(path.stem[:match_start])
    if len(prefix) >= 2:
        return prefix
    # Filename had no usable prefix (e.g. "S01E01 - We Is Us.mkv"): walk up,
    # skipping season/release folders, to the first meaningful directory name.
    for parent in path.parents:
        if not parent.name or _SEASON_FOLDER_RE.match(parent.name):
            continue
        cleaned = _clean_folder_title(parent.name)
        if len(cleaned) >= 2:
            return cleaned
    return prefix


def _safe_filename(name: str) -> str:
    safe = re.sub(r'\s+', ' ', _ILLEGAL_FS.sub('', name)).strip()
    return safe or "series"


def _extract_year(text: str) -> Optional[str]:
    m = _YEAR_RE.search(text or "")
    return m.group(1) if m else None


def _build_series_item(name: str, eps: List[Tuple[int, int, Path]]) -> MediaItem:
    paths = [p for (_, _, p) in eps]
    root = Path(os.path.commonpath([str(p.parent) for p in paths]))
    # If the shared root is itself a "Season N" folder, lift to its parent so the
    # series page sits above its seasons (e.g. "Pluribus (2025)/Season 01" -> series).
    if _SEASON_FOLDER_RE.match(root.name):
        root = root.parent

    safe = _safe_filename(name)
    by_season: dict[int, List[Episode]] = {}
    for season, number, p in eps:
        by_season.setdefault(season, []).append(Episode(season, number, p))

    seasons: List[SeasonGroup] = []
    for snum in sorted(by_season):
        season_eps = sorted(by_season[snum], key=lambda e: e.number)
        dirs = {e.path.parent for e in season_eps}
        folder = html_path = None
        if len(dirs) == 1:
            only = next(iter(dirs))
            if only != root:
                folder = only
                html_path = only / f"{safe} - Season {snum}.html"
        seasons.append(SeasonGroup(snum, season_eps, folder, html_path))

    return MediaItem(
        kind="series", title=name,
        year=_extract_year(root.name),
        html_path=root / f"{safe}.html",
        root=root, seasons=seasons,
    )


def classify_items(video_paths: List[Path]) -> List[MediaItem]:
    """Group episode files into series items; everything else is a movie item."""
    series_eps: dict[str, List[Tuple[int, int, Path]]] = {}
    series_name: dict[str, str] = {}
    movies: List[Path] = []

    for path in video_paths:
        parsed = parse_episode(path.stem)
        if parsed is None:
            movies.append(path)
            continue
        season, number, start = parsed
        name = _series_name_from_path(path, start)
        if not name:
            movies.append(path)  # episode token but no identifiable series
            continue
        key = name.lower()
        series_eps.setdefault(key, []).append((season, number, path))
        series_name.setdefault(key, name)

    items: List[MediaItem] = []
    for p in movies:
        title, year = clean_filename_to_title(p)
        items.append(MediaItem(
            kind="movie", title=title or p.stem, year=year,
            video_path=p, html_path=_get_html_path(p),
        ))
    for key, eps in series_eps.items():
        items.append(_build_series_item(series_name[key], eps))
    return items
