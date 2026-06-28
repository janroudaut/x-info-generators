import base64
import hashlib
import io
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

import aiohttp
from PIL import Image

from .http import download_file_with_progress
from .utils import encode_image_to_base64_data_uri


def optimize_image(image_path: Path, max_width: int = 1280, quality: int = 75) -> Path:
    """Resize and convert an image to WebP for smaller base64 output.

    Animated images (GIFs) are returned as-is to preserve animation.
    """
    try:
        with Image.open(image_path) as img:
            # Preserve animated images (GIFs with multiple frames)
            if getattr(img, "n_frames", 1) > 1:
                return image_path
            output_path = image_path.with_suffix(".webp")
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            if img.width > max_width:
                ratio = max_width / img.width
                new_size = (max_width, int(img.height * ratio))
                img = img.resize(new_size, Image.LANCZOS)
            img.save(output_path, "WEBP", quality=quality)
        return output_path
    except Exception:
        return image_path


def optimize_and_encode(image_path: Path, max_width: int = 1280, quality: int = 75) -> Optional[str]:
    """Optimize an image and return it as a base64 data URI."""
    optimized = optimize_image(image_path, max_width, quality)
    return encode_image_to_base64_data_uri(optimized)


def downscale_data_uri(data_uri: Optional[str], max_px: int = 360, quality: int = 70) -> Optional[str]:
    """Shrink an existing base64 data URI to a small WebP thumbnail.

    Used to keep the catalog index lightweight: page posters are already inlined
    at up to 1280px, far larger than a card thumbnail needs. Decodes the data URI,
    fits it within ``max_px`` (longest side), re-encodes as WebP, and returns a new
    data URI. Returns the input unchanged on any failure (incl. non-data URIs).
    """
    if not data_uri or not data_uri.startswith("data:"):
        return data_uri
    try:
        header, b64 = data_uri.split(",", 1)
        raw = base64.b64decode(b64)
        with Image.open(io.BytesIO(raw)) as img:
            # Animated images: keep as-is to avoid freezing a single frame.
            if getattr(img, "n_frames", 1) > 1:
                return data_uri
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.thumbnail((max_px, max_px), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "WEBP", quality=quality)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/webp;base64,{encoded}"
    except Exception:
        return data_uri


_ALLOWED_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif")


async def cached_image_data_uri(
    session: aiohttp.ClientSession, url: str, cache, temp_dir: Path,
    log: Callable, label: str = "Image",
) -> Optional[str]:
    """Return an optimized base64 data URI for ``url``, using the disk cache.

    On a cache miss the image is downloaded, optimized to WebP, encoded, and the
    resulting data URI is stored under the shared ``image`` namespace (keyed by URL).
    """
    if not url or url.startswith("data:"):
        return None
    hit, value = cache.get("image", url)
    if hit:
        return value
    if cache.offline:
        return None  # never download in offline mode

    file_ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if file_ext not in _ALLOWED_EXTS:
        file_ext = ".jpg"
    stem = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    temp_path = temp_dir / f"{stem}{file_ext}"

    data_uri = None
    if await download_file_with_progress(session, url, temp_path, log, label):
        data_uri = optimize_and_encode(temp_path)
    cache.set("image", url, data_uri)
    return data_uri
