import argparse
import os
import re
import sys
from pathlib import Path

import wikipedia

from .display import DisplayMode as D
from . import __version__, REPO_URL

DEFAULT_INDEX_OUTPUT = "00_INDEX.html"


def resolve_index_target(index_value, paths):
    """Disambiguate the optional ``--index [OUTPUT]`` value.

    If the value is an existing directory it's meant as a path to scan (so
    ``--index .`` works); otherwise it's the output file. Returns
    ``(output_path, scan_paths)``.
    """
    if index_value and Path(index_value).is_dir():
        return DEFAULT_INDEX_OUTPUT, [index_value, *paths]
    return (index_value or DEFAULT_INDEX_OUTPUT), paths

def _error_message(message: str) -> str:
    """Format a CLI error for stderr, honoring --no-color / NO_COLOR / tty.

    Decided independently of DisplayMode.setup(), because argparse raises
    errors before setup_environment() has run.
    """
    if "NO_COLOR" in os.environ or "--no-color" in sys.argv:
        return f"** {message}"
    if sys.stderr.isatty():
        return f"\033[1;91m{D.ERROR} {message}\033[0m"
    return f"{D.ERROR} {message}"


class GnuHelpFormatter(argparse.HelpFormatter):
    """GNU/eza-style --help: short and long options on one line, descriptions
    aligned in a column, uppercase section headings, ``Usage:`` prefix."""

    def __init__(self, prog):
        super().__init__(prog, max_help_position=32)

    def start_section(self, heading):
        if heading:
            # Uppercase the heading but keep flag tokens (e.g. --index) lowercase.
            heading = re.sub(r"--[\w-]+", lambda m: m.group(0).lower(), heading.upper())
        super().start_section(heading)

    def format_help(self):
        # argparse hardcodes a trailing ":" on section headings; strip it (those
        # are the only column-0 lines ending in ":"; "Usage:" and body are not).
        help_text = super().format_help()
        return re.sub(r"(?m)^(\S[^\n]*):$", r"\1", help_text)

    def _format_usage(self, usage, actions, groups, prefix):
        if prefix is None:
            prefix = "Usage: "
        return super()._format_usage(usage, actions, groups, prefix)

    def _format_action_invocation(self, action):
        # Positionals: unchanged.
        if not action.option_strings:
            return super()._format_action_invocation(action)
        # Flags: just the option names ("-R, --recursive").
        if action.nargs == 0:
            return ", ".join(action.option_strings)
        # Valued options: names then the metavar once ("--max-depth N").
        metavar = self._format_args(action, self._get_default_metavar_for_optional(action))
        return f"{', '.join(action.option_strings)} {metavar}"


class InfoArgumentParser(argparse.ArgumentParser):
    """Print a short usage line plus a highlighted error on stderr, then exit 2.

    Used for both argparse-level mistakes (unknown flag, bad value) and our own
    ``parser.error()`` invocation checks, so the two behave identically.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("formatter_class", GnuHelpFormatter)
        super().__init__(*args, **kwargs)

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write(_error_message(message) + "\n")
        self.exit(2)


def validate_invocation(parser, args, paths):
    """Reject a malformed invocation (usage + error, exit 2).

    Runtime "nothing found" outcomes are not handled here — the invocation was
    well-formed, there was simply nothing to do.
    """
    if args.purge_cache:
        return
    if args.index is not None:
        _, scan = resolve_index_target(args.index, paths)
        if not scan:
            parser.error("--index needs at least one path to scan")
        return
    if not paths:
        parser.error("no input paths given")


# Wikimedia rejects the wikipedia lib's default User-Agent with HTTP 403.
# A descriptive UA with contact info is required by their robot policy.
WIKIPEDIA_USER_AGENT = f"x-info-generators/{__version__} (+{REPO_URL})"


def add_common_arguments(parser: argparse.ArgumentParser) -> dict:
    """Add the options shared by both CLIs, grouped by topic in --help.

    Returns the created argument groups (keyed by name) so each CLI can add its
    own options under the matching topic.
    """
    gen = parser.add_argument_group("generation")
    gen.add_argument(
        "--force", action="store_true",
        help="Force regeneration of existing info files.")
    gen.add_argument(
        "-R", "--recursive", action="store_true",
        help="Scan directories recursively.")
    gen.add_argument(
        "-C", "--cleanup", "--clean", "--remove", "--rm",
        dest="cleanup", action="store_true",
        help="Remove generated HTML files.")
    gen.add_argument(
        "--max-screenshots", type=int, default=8, metavar="N",
        help="Maximum number of screenshots (default: 8).")

    catalog = parser.add_argument_group("catalog (--index)")
    catalog.add_argument(
        "--index", nargs="?", const="00_INDEX.html", default=None, metavar="OUTPUT",
        help="Build a browsable catalog of already-generated pages found under the "
             "given paths, then exit (no generation, no network). An optional value "
             "is the output file — but a directory there is treated as a path to scan "
             "(default output: 00_INDEX.html).")
    catalog.add_argument(
        "--max-depth", type=int, default=5, metavar="N",
        help="Max directory depth scanned by --index (default: 5).")
    catalog.add_argument(
        "--wsl", action="store_true",
        help="For --index: emit Windows file:// links (e.g. D:/…) for /mnt/<drive>/ "
             "paths, so a catalog built under WSL opens correctly in a Windows browser.")
    catalog.add_argument(
        "--title", default=None, metavar="TEXT",
        help="For --index: catalog page title (default: derived from contents — the "
             "single type if there's only one, else \"Catalog\").")

    cache = parser.add_argument_group("caching")
    cache.add_argument(
        "--no-cache", action="store_true",
        help="Disable the on-disk fetch cache (always hit the network).")
    cache.add_argument(
        "--update-cache", action="store_true", dest="update_cache",
        help="Re-fetch everything and overwrite cached entries (refresh stale data).")
    cache.add_argument(
        "--offline", "--cache-only", action="store_true", dest="offline",
        help="Use only cached data; make no network requests (re-render from cache).")
    cache.add_argument(
        "--purge-cache", action="store_true", dest="purge_cache",
        help="Delete cache entries older than --cache-ttl days, then exit.")
    cache.add_argument(
        "--cache-ttl", type=int, default=30, metavar="DAYS",
        help="Age (days) used by --purge-cache; 0 purges everything (default: 30). "
             "The cache itself never expires on its own.")

    display = parser.add_argument_group("display & diagnostics")
    display.add_argument(
        "--no-color", action="store_true",
        help="Disable color and emoji output.")
    display.add_argument(
        "--debug", action="store_true",
        help="Print debug information.")
    display.add_argument(
        "-V", "--version", action="version",
        version=f"%(prog)s {__version__}")

    return {"generation": gen, "caching": cache, "catalog": catalog, "display": display}


def setup_environment(args, script_name: str):
    D.setup("NO_COLOR" in os.environ or args.no_color)
    wikipedia.set_user_agent(WIKIPEDIA_USER_AGENT)
    print(f"{D.ROCKET} {script_name}")
