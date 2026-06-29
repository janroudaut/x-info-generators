import argparse
import asyncio
import shutil
import sys
import time
from pathlib import Path

from ..display import DisplayMode as D
from ..cli import (
    add_common_arguments, setup_environment, resolve_index_target,
    InfoArgumentParser, validate_invocation,
)
from ..cache import FetchCache, default_cache_root, purge_cache
from ..http import create_session
from ..processing import RunStats, print_run_summary
from ..index import build_catalog
from ..utils import format_bytes, path_matches_ignore
from .. import __version__, REPO_URL
from .processing import (
    find_movie_files, process_movie_file, process_series,
    VIDEO_EXTENSIONS,
)
from .discovery import classify_items

USER_AGENT = f"VideoInfoGenerator/{__version__} (Personal, manual use script; +{REPO_URL})"


def _collect_video_files(paths, recursive, ignore=None):
    """Gather all candidate video files from the given paths, minus ignored ones."""
    # Leniency on extension only when a *single* file is given explicitly (the user
    # clearly means that file). A glob like /videos/* expands to many entries
    # (incl. .srt/.nfo/.jpg…), so there we still filter files by extension.
    lenient = len(paths) == 1 and Path(paths[0]).resolve().is_file()
    video_files = []
    for path_str in paths:
        path = Path(path_str).resolve()
        if path.is_file():
            if lenient or path.suffix.lower() in VIDEO_EXTENSIONS:
                video_files.append(path)
        elif path.is_dir():
            if recursive:
                video_files.extend(find_movie_files(path))
            else:
                video_files.extend(
                    f for f in path.iterdir()
                    if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
                )
    if ignore:
        video_files = [f for f in video_files if not path_matches_ignore(f, ignore)]
    return video_files


def _cleanup_movie_files(paths, recursive, log, ignore=None):
    """Remove generated HTML files for movies and series (incl. season pages)."""
    items = classify_items(_collect_video_files(paths, recursive, ignore))
    cleaned = 0
    total_bytes = 0
    for item in items:
        for html_file in item.all_html_paths():
            if html_file.exists():
                size = html_file.stat().st_size
                html_file.unlink()
                log(f"    {D.CLEAN} Removed: {html_file.name} ({format_bytes(size)})")
                cleaned += 1
                total_bytes += size
    if cleaned:
        log(f"\n{D.SUCCESS_DATA} Removed {cleaned} file(s), freed {format_bytes(total_bytes)}")
    else:
        log(f"\n{D.INFO} No generated files found to remove.")


async def _main_loop(args: argparse.Namespace):
    setup_environment(
        args,
        "Creating catalog from HTML files..." if args.index is not None else "Video Info Generator",
    )

    if args.purge_cache:
        removed, freed = purge_cache(default_cache_root(), args.cache_ttl)
        print(f"{D.CLEAN} Cleaned {removed} cache entr{'y' if removed == 1 else 'ies'}, freed {format_bytes(freed)}")
        return

    if args.index is not None:
        output, scan = resolve_index_target(args.index, args.paths)
        total, by_kind = build_catalog(scan, Path(output), print, args.max_depth, args.wsl, args.title)
        print(f"{D.SUCCESS_HTML} Catalog: {total} item(s) "
              f"({by_kind['game']} games, {by_kind['movie']} movies, {by_kind['series']} series) "
              f"→ {output}")
        return

    if not shutil.which("ffmpeg"):
        print(f"{D.C_YELLOW}{D.WARNING} ffmpeg not found in PATH — screenshots will be skipped (everything else still works).{D.C_RESET}")

    # Collect candidate video files, then classify them into items (movies + series)
    video_files = _collect_video_files(args.paths, args.recursive, args.ignore)
    if not video_files:
        print(f"{D.SHRUG} No video files found to process.", file=sys.stderr)
        return 1

    # Cleanup mode
    if args.cleanup:
        print(f"{D.CLEAN} Cleanup mode enabled...")
        _cleanup_movie_files(args.paths, args.recursive, print, args.ignore)
        return

    items = classify_items(video_files)
    n_movies = sum(1 for it in items if it.kind == "movie")
    n_series = sum(1 for it in items if it.kind == "series")
    total_count = len(items)
    print(f"{D.SUBDIR} Found {total_count} item(s): {n_movies} movie(s), {n_series} series.")

    run_stats = RunStats()
    global_start_time = time.monotonic()

    cache = FetchCache(default_cache_root(), ttl_days=args.cache_ttl,
                       enabled=(not args.no_cache) or args.offline,
                       refresh=args.update_cache and not args.offline,
                       offline=args.offline)

    session = create_session(USER_AGENT)
    try:
        for i, item in enumerate(items, 1):
            label = "SERIES" if item.kind == "series" else "MOVIE"
            print(f"\n{D.PROCESS} [{i}/{total_count}] ({label}) {item.title}", end="")
            if item.html_path.exists() and not args.force:
                print(f" {D.C_YELLOW}(SKIPPED){D.C_RESET}")
                run_stats.record("SKIPPED")
                continue
            print()

            if item.kind == "series":
                item_stats = await process_series(
                    session, item, args.force, args.max_screenshots, args.debug, print, cache,
                    args.screenshot_source,
                )
            else:
                item_stats = await process_movie_file(
                    session, item.video_path, args.force, args.max_screenshots, args.debug, print, cache,
                    args.screenshot_source,
                )

            run_stats.record(item_stats.status, item_stats.size_bytes)

            print(
                f"  {D.STATS} Status: {item_stats.status} | "
                f"{D.CLOCK} Duration: {item_stats.duration_s:.2f}s | "
                f"Size: {format_bytes(item_stats.size_bytes)}"
            )
            if item_stats.failed_sources:
                print(f"    {D.C_YELLOW}{D.WARNING} No data from: {', '.join(item_stats.failed_sources)}{D.C_RESET}")
    finally:
        await session.close()

    total_duration = time.monotonic() - global_start_time
    print_run_summary(run_stats, total_count, total_duration, "Video Info Generator")


def main():
    parser = InfoArgumentParser(
        description="Generate HTML descriptions for local movie files and TV series.",
    )
    parser.add_argument(
        "paths", nargs="*", metavar="PATH",
        help="One or more paths to movie files or directories.",
    )
    groups = add_common_arguments(parser)
    groups["generation"].add_argument(
        "--ignore", action="append", metavar="PATTERN", dest="ignore",
        help="Exclude paths matching PATTERN (glob-like, case-insensitive; "
             "wrap in /.../ for a regex). Repeatable.")
    groups["generation"].add_argument(
        "--screenshot-source", choices=["auto", "online", "ffmpeg", "off"],
        default="auto", metavar="MODE", dest="screenshot_source",
        help="Where stills come from: auto (online imdbapi.dev stills, ffmpeg "
             "fallback) [default], online (imdbapi.dev only), ffmpeg (local file "
             "only), off (none).")

    # Bare invocation: show the full help and exit cleanly (onboarding).
    if len(sys.argv) == 1:
        parser.print_help()
        return

    args = parser.parse_args()
    validate_invocation(parser, args, args.paths)

    try:
        sys.exit(asyncio.run(_main_loop(args)))
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting.", file=sys.stderr)
        sys.exit(130)
