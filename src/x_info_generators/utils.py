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


# ISO 639 languages: canonical 639-2/B code, its aliases (639-2/T, 639-1),
# and the emoji flag.
_LANGS = [
    ("eng", ("en",), "🇬🇧"),
    ("fre", ("fra", "fr"), "🇫🇷"),
    ("spa", ("es",), "🇪🇸"),
    ("ger", ("deu", "de"), "🇩🇪"),
    ("ita", ("it",), "🇮🇹"),
    ("por", ("pt",), "🇵🇹"),
    ("dut", ("nld", "nl"), "🇳🇱"),
    ("rus", ("ru",), "🇷🇺"),
    ("jpn", ("ja",), "🇯🇵"),
    ("kor", ("ko",), "🇰🇷"),
    ("chi", ("zho", "zh"), "🇨🇳"),
    ("ara", ("ar",), "🇸🇦"),
    ("pol", ("pl",), "🇵🇱"),
    ("swe", ("sv",), "🇸🇪"),
    ("nor", ("no",), "🇳🇴"),
    ("dan", ("da",), "🇩🇰"),
    ("fin", ("fi",), "🇫🇮"),
    ("cze", ("ces", "cs"), "🇨🇿"),
    ("hun", ("hu",), "🇭🇺"),
    ("gre", ("ell", "el"), "🇬🇷"),
    ("rum", ("ron", "ro"), "🇷🇴"),
    ("tur", ("tr",), "🇹🇷"),
    ("heb", ("he",), "🇮🇱"),
    ("hin", ("hi",), "🇮🇳"),
    ("tha", ("th",), "🇹🇭"),
    ("ukr", ("uk",), "🇺🇦"),
    ("vie", ("vi",), "🇻🇳"),
    ("ind", ("id",), "🇮🇩"),
    ("bul", ("bg",), "🇧🇬"),
    ("est", ("et",), "🇪🇪"),
    ("hrv", ("hr",), "🇭🇷"),
    ("ice", ("isl", "is"), "🇮🇸"),
    ("lit", ("lt",), "🇱🇹"),
    ("lav", ("lv",), "🇱🇻"),
    ("may", ("msa", "ms"), "🇲🇾"),
    ("slo", ("slk", "sk"), "🇸🇰"),
    ("slv", ("sl",), "🇸🇮"),
    ("srp", ("sr",), "🇷🇸"),
    ("tam", ("ta",), "🇮🇳"),
    ("tel", ("te",), "🇮🇳"),
    ("ben", ("bn",), "🇧🇩"),
    ("urd", ("ur",), "🇵🇰"),
    ("per", ("fas", "fa"), "🇮🇷"),
]
_LANG_FLAGS = {}
_LANG_CANON = {}
for _canon, _aliases, _flag in _LANGS:
    for _c in (_canon, *_aliases):
        _LANG_FLAGS[_c] = _flag
        _LANG_CANON[_c] = _canon


def canon_lang(code: Optional[str]) -> str:
    """Collapse 639-1/639-2 variants to one canonical code (fr/fra → fre)."""
    code = (code or "").strip().lower()
    return _LANG_CANON.get(code, code)


def lang_flag(code: Optional[str]) -> str:
    """Emoji flag for a language code; undetermined tracks get a neutral
    globe, other unknown codes show as text."""
    code = (code or "").strip().lower()
    if code in ("", "und"):
        return "🌐"
    return _LANG_FLAGS.get(code, code.upper())
