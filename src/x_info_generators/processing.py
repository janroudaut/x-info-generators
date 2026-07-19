from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Callable, Optional

from .display import DisplayMode as D
from .utils import format_bytes


@dataclass
class ItemStats:
    status: str = "ERROR"
    size_bytes: int = 0
    duration_s: float = 0.0
    failed_sources: List[str] = field(default_factory=list)
    sources_summary: Dict[str, str] = field(default_factory=dict)
    title: Optional[str] = None  # resolved display title ("The Abyss (1989)")


STATUS_EMOJI = {"SUCCESS": "📄", "SKIPPED": "⏩", "INSUFFICIENT_DATA": "🤷", "ERROR": "❌"}


def format_item_status(stats: ItemStats) -> str:
    """One-line per-item summary: resolved title, status, duration, size."""
    title = f"{stats.title} | " if stats.title else ""
    emoji = STATUS_EMOJI.get(stats.status, "")
    emoji = f"{emoji} " if D.STATS and emoji else ""
    return (f"  {D.STATS} {title}{emoji}{stats.status} | "
            f"{D.CLOCK} {stats.duration_s:.2f}s | {format_bytes(stats.size_bytes)}")


@dataclass
class RunStats:
    success: int = 0
    skipped: int = 0
    insufficient_data: int = 0
    error: int = 0
    total_size_bytes: int = 0

    def record(self, status: str, size_bytes: int = 0):
        match status:
            case "SUCCESS":
                self.success += 1
            case "SKIPPED":
                self.skipped += 1
            case "INSUFFICIENT_DATA":
                self.insufficient_data += 1
            case _:
                self.error += 1
        self.total_size_bytes += size_bytes


def print_run_summary(stats: RunStats, total_count: int, duration: float, script_name: str):
    print(f"\n{D.PARTY} All processing finished! {D.PARTY}")
    print("\n" + "=" * 20 + f" {D.STATS} Run Summary " + "=" * 20)
    print(f"  {D.CLOCK} Total execution time: {duration:.2f} seconds")
    print(f"  Items processed: {total_count}")
    print(f"  - {D.SUCCESS_HTML} Successful: {stats.success}")
    print(f"  - {D.SKIP} Skipped: {stats.skipped}")
    print(f"  - {D.SHRUG} Failed (No Data): {stats.insufficient_data}")
    print(f"  - {D.ERROR} Failed (Errors): {stats.error}")
    print(f"  Total size of generated files: {format_bytes(stats.total_size_bytes)}")
    print("=" * 55)


def cleanup_html_files(paths: List[Path], pattern: str, recursive: bool, log: Callable):
    """Remove generated HTML files matching pattern."""
    cleaned = 0
    total_bytes = 0
    for path in paths:
        targets = list(path.rglob(pattern)) if recursive else list(path.glob(pattern))
        for html_file in targets:
            if html_file.is_file():
                size = html_file.stat().st_size
                html_file.unlink()
                log(f"    {D.CLEAN} Removed: {html_file} ({format_bytes(size)})")
                cleaned += 1
                total_bytes += size
    if cleaned:
        log(f"\n{D.SUCCESS_DATA} Removed {cleaned} file(s), freed {format_bytes(total_bytes)}")
    else:
        log(f"\n{D.INFO} No generated files found to remove.")
