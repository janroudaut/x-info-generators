import asyncio
import base64
import difflib
import fnmatch
import mimetypes
import re
import unicodedata
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


def _norm_title(s: Optional[str]) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def title_similarity(a: Optional[str], b: Optional[str]) -> float:
    """Similarity ratio in [0, 1] between two titles, ignoring case,
    diacritics and punctuation."""
    a, b = _norm_title(a), _norm_title(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


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


def lang_label(code: Optional[str]) -> str:
    """Short text form of a language, for tooltips and <option> labels — the
    places markup can't go. Undetermined tracks get a question mark."""
    code = (code or "").strip().lower()
    return "?" if code in ("", "und") else code.upper()


_LANG_NAMES = {
    "eng": "English", "fre": "French", "spa": "Spanish", "ger": "German",
    "ita": "Italian", "jpn": "Japanese", "rus": "Russian", "por": "Portuguese",
    "dut": "Dutch", "chi": "Chinese", "kor": "Korean", "ara": "Arabic",
    "swe": "Swedish", "dan": "Danish", "nor": "Norwegian", "fin": "Finnish",
    "pol": "Polish", "cze": "Czech", "hun": "Hungarian", "tur": "Turkish",
    "gre": "Greek", "heb": "Hebrew", "hin": "Hindi", "tha": "Thai",
    "vie": "Vietnamese", "ind": "Indonesian", "may": "Malay", "rum": "Romanian",
    "ukr": "Ukrainian", "bul": "Bulgarian", "hrv": "Croatian", "srp": "Serbian",
    "slo": "Slovak", "slv": "Slovenian", "est": "Estonian", "lav": "Latvian",
    "lit": "Lithuanian", "ice": "Icelandic", "tam": "Tamil", "tel": "Telugu",
    "ben": "Bengali", "urd": "Urdu", "per": "Persian",
}


def lang_name(code) -> str:
    """Full language name for menus. Falls back to the bare code, which beats
    inventing a name for something we don't know."""
    code = canon_lang(code)
    if code in ("", "und"):
        return "Undetermined"
    return _LANG_NAMES.get(code, code.upper())


# Country whose flag stands for the language, mirroring flags.py's coverage.
_LANG_REGION = {
    "ara": "SA", "ben": "BD", "bul": "BG", "chi": "CN", "cze": "CZ",
    "dan": "DK", "dut": "NL", "eng": "GB", "est": "EE", "fin": "FI",
    "fre": "FR", "ger": "DE", "gre": "GR", "heb": "IL", "hin": "IN",
    "hrv": "HR", "hun": "HU", "ice": "IS", "ind": "ID", "ita": "IT",
    "jpn": "JP", "kor": "KR", "lav": "LV", "lit": "LT", "may": "MY",
    "nor": "NO", "per": "IR", "pol": "PL", "por": "PT", "rum": "RO",
    "rus": "RU", "slo": "SK", "slv": "SI", "spa": "ES", "srp": "RS",
    "swe": "SE", "tam": "IN", "tel": "IN", "tha": "TH", "tur": "TR",
    "ukr": "UA", "urd": "PK", "vie": "VN",
}


def lang_emoji(code: Optional[str]) -> str:
    """Flag emoji for a language, built from regional indicator letters.

    Only for <select> options, which cannot hold the SVG artwork used
    everywhere else. Windows ships no flag glyphs and will show the bare letter
    pair instead — a known, accepted trade-off here.
    """
    region = _LANG_REGION.get(canon_lang(code))
    if not region:
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in region)


def lang_svg(code: Optional[str]) -> str:
    """Inline flag artwork for a language, falling back to its code.

    Emoji flags are not an option: Windows ships no flag glyphs, so Chrome
    renders them as the bare letter pair the emoji is built from.
    """
    from .flags import FLAG_SVG
    svg = FLAG_SVG.get(canon_lang(code))
    return svg or f'<span class="flag-code">{lang_label(code)}</span>'
