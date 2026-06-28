import argparse
import os

import wikipedia

from .display import DisplayMode as D
from . import __version__

# Wikimedia rejects the wikipedia lib's default User-Agent with HTTP 403.
# A descriptive UA with contact info is required by their robot policy.
WIKIPEDIA_USER_AGENT = (
    f"x-info-generators/{__version__} (+https://github.com/janroudaut/x-info-generators)"
)


def add_common_arguments(parser: argparse.ArgumentParser):
    parser.add_argument(
        "--force", action="store_true",
        help="Force regeneration of existing info files.")
    parser.add_argument(
        "-R", "--recursive", action="store_true",
        help="Scan directories recursively.")
    parser.add_argument(
        "-C", "--cleanup", "--clean", "--remove", "--rm",
        dest="cleanup", action="store_true",
        help="Remove generated HTML files.")
    parser.add_argument(
        "--no-color", action="store_true",
        help="Disable color and emoji output.")
    parser.add_argument(
        "--debug", action="store_true",
        help="Print debug information.")
    parser.add_argument(
        "-V", "--version", action="version",
        version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--max-screenshots", type=int, default=8,
        help="Maximum number of screenshots (default: 8).")
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Disable the on-disk fetch cache (always hit the network).")
    parser.add_argument(
        "--update-cache", action="store_true", dest="update_cache",
        help="Re-fetch everything and overwrite cached entries (refresh stale data).")
    parser.add_argument(
        "--offline", "--cache-only", action="store_true", dest="offline",
        help="Use only cached data; make no network requests (re-render from cache).")
    parser.add_argument(
        "--purge-cache", action="store_true", dest="purge_cache",
        help="Delete cache entries older than --cache-ttl days, then exit.")
    parser.add_argument(
        "--cache-ttl", type=int, default=30,
        help="Age (days) used by --purge-cache; 0 purges everything (default: 30). "
             "The cache itself never expires on its own.")


def setup_environment(args, script_name: str):
    D.setup("NO_COLOR" in os.environ or args.no_color)
    wikipedia.set_user_agent(WIKIPEDIA_USER_AGENT)
    print(f"{D.ROCKET} {script_name} v{__version__} {D.ROCKET}")
