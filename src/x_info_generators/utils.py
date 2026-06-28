import asyncio
import base64
import fnmatch
import mimetypes
import re
from pathlib import Path
from typing import Optional, Sequence


def path_matches_ignore(path: Path, patterns: Optional[Sequence[str]]) -> bool:
    """True if ``path`` matches any ignore pattern (case-insensitive).

    Each pattern is glob-like by default and matches the full path string or any
    single path component, so ``*ARTE*`` or ``Le dessous*`` both exclude a folder
    by name. A pattern wrapped in slashes (``/.../``) is treated as a regular
    expression searched against the full path and each component.
    """
    if not patterns:
        return False
    full = str(path).lower()
    parts = [p.lower() for p in Path(path).parts]
    for raw in patterns:
        if len(raw) >= 2 and raw.startswith("/") and raw.endswith("/"):
            try:
                rx = re.compile(raw[1:-1], re.IGNORECASE)
            except re.error:
                continue
            if rx.search(full) or any(rx.search(part) for part in parts):
                return True
        else:
            pat = raw.lower()
            if fnmatch.fnmatch(full, pat) or any(fnmatch.fnmatch(part, pat) for part in parts):
                return True
    return False


def format_bytes(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} bytes"
    elif size_bytes < 1024**2:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024**3:
        return f"{size_bytes / 1024**2:.2f} MB"
    return f"{size_bytes / 1024**3:.2f} GB"


_EXT_TO_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
}


def encode_image_to_base64_data_uri(image_path: Path) -> Optional[str]:
    if not image_path.exists() or not image_path.is_file():
        return None
    try:
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type or not mime_type.startswith("image/"):
            mime_type = _EXT_TO_MIME.get(image_path.suffix.lower(), "image/octet-stream")
        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"
    except Exception:
        return None


async def run_in_executor(func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)
