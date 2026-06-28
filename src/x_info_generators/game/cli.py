import argparse
import asyncio
import sys
import time
from pathlib import Path

from ..display import DisplayMode as D
from ..cli import add_common_arguments, setup_environment
from ..cache import FetchCache, default_cache_root, purge_cache
from ..http import create_session
from ..processing import RunStats, print_run_summary, cleanup_html_files
from ..index import build_catalog
from ..utils import format_bytes
from .. import __version__
from .processing import clean_game_title, process_game_directory, DEFAULT_HTML_FILENAME

USER_AGENT = "GameInfoGenerator/1.0.0 (I'm a kind scraper, called manually and used for personal use <3)"


async def _main_loop(args: argparse.Namespace):
    setup_environment(
        args,
        "Creating catalog from HTML files..." if args.index is not None else "Game Info Generator",
    )

    if args.purge_cache:
        removed, freed = purge_cache(default_cache_root(), args.cache_ttl)
        print(f"{D.CLEAN} Cleaned {removed} cache entr{'y' if removed == 1 else 'ies'}, freed {format_bytes(freed)}")
        return

    if args.index is not None:
        if not args.input_dirs:
            print(f"{D.ERROR} --index needs at least one path to scan.")
            return
        total, by_kind = build_catalog(args.input_dirs, Path(args.index), print, args.max_depth, args.wsl)
        print(f"{D.SUCCESS_HTML} Catalog: {total} item(s) "
              f"({by_kind['game']} games, {by_kind['movie']} movies, {by_kind['series']} series) "
              f"→ {args.index}")
        return

    if not args.input_dirs:
        print(f"{D.ERROR} No paths given.")
        return

    # Collect game directories
    game_directories = []
    for path_str in args.input_dirs:
        current_path = Path(path_str).resolve()
        if not current_path.is_dir():
            print(f"{D.WARNING} Path '{path_str}' is not a valid directory. Skipping.")
            continue
        if args.recursive:
            game_directories.extend(sorted(d for d in current_path.iterdir() if d.is_dir()))
        else:
            game_directories.append(current_path)

    if not game_directories:
        print(f"{D.ERROR} No valid game directories found to process.")
        return

    # Cleanup mode
    if args.cleanup:
        print(f"{D.CLEAN} Cleanup mode enabled...")
        cleanup_html_files(
            game_directories, DEFAULT_HTML_FILENAME, recursive=False, log=print,
        )
        return

    total_count = len(game_directories)
    print(f"{D.SUBDIR} Found {total_count} game director{'y' if total_count == 1 else 'ies'} to process.")

    run_stats = RunStats()
    global_start_time = time.monotonic()

    cache = FetchCache(default_cache_root(), ttl_days=args.cache_ttl,
                       enabled=(not args.no_cache) or args.offline,
                       refresh=args.update_cache and not args.offline,
                       offline=args.offline)

    session = create_session(USER_AGENT, max_concurrent=args.max_concurrent, timeout=args.timeout)
    try:
        for i, game_dir in enumerate(game_directories, 1):
            game_title = clean_game_title(game_dir.name)
            output_html = game_dir / DEFAULT_HTML_FILENAME

            print(f"\n{D.PROCESS} [{i}/{total_count}] {game_dir.name} -> {game_title}", end="")

            if output_html.exists() and not args.force:
                print(f" {D.C_YELLOW}(SKIPPED){D.C_RESET}")
                run_stats.record("SKIPPED")
                continue
            print()

            item_stats = await process_game_directory(
                session, game_dir, game_title, args.force, args.max_screenshots, print, cache,
            )

            run_stats.record(item_stats.status, item_stats.size_bytes)

            status_emoji = {"SUCCESS": D.SUCCESS_HTML, "INSUFFICIENT_DATA": D.SHRUG, "ERROR": D.ERROR}
            print(
                f"  {D.STATS} Status: {status_emoji.get(item_stats.status, '')} {item_stats.status} | "
                f"{D.CLOCK} Duration: {item_stats.duration_s:.2f}s | "
                f"Size: {format_bytes(item_stats.size_bytes)}"
            )
            for source, summary in item_stats.sources_summary.items():
                print(f"    {D.SUCCESS_DATA} {source}: {summary}")
            if item_stats.failed_sources:
                print(f"    {D.C_YELLOW}{D.SHRUG} No data from: {', '.join(item_stats.failed_sources)}{D.C_RESET}")
    finally:
        await session.close()

    total_duration = time.monotonic() - global_start_time
    print_run_summary(run_stats, total_count, total_duration, "Game Info Generator")


def main():
    parser = argparse.ArgumentParser(
        description="Generate HTML descriptions for video game directories.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "input_dirs", nargs="*", metavar="INPUT_DIR",
        help="One or more paths to game directories. If -R is used, these are root directories to scan.",
    )
    add_common_arguments(parser)
    parser.add_argument(
        "--max-concurrent", type=int, default=3,
        help="Max concurrent HTTP requests per host (default: 3).",
    )
    parser.add_argument(
        "--timeout", type=int, default=20,
        help="Default network timeout in seconds (default: 20).",
    )

    args = parser.parse_args()

    try:
        asyncio.run(_main_loop(args))
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting.")
