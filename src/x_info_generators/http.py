import asyncio
from pathlib import Path
from typing import Callable

import aiohttp
from tqdm.asyncio import tqdm

from .display import DisplayMode as D


def create_session(user_agent: str, max_concurrent: int = 3, timeout: int = 20) -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(limit_per_host=max_concurrent)
    return aiohttp.ClientSession(
        connector=connector,
        headers={"User-Agent": user_agent},
        timeout=aiohttp.ClientTimeout(total=timeout),
    )


async def download_file_with_progress(
    session: aiohttp.ClientSession,
    url: str,
    temp_file_path: Path,
    log: Callable,
    file_type: str = "file",
) -> bool:
    try:
        temp_file_path.parent.mkdir(parents=True, exist_ok=True)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as response:
            response.raise_for_status()
            total_size = int(response.headers.get("content-length", 0))
            with tqdm(
                total=total_size, unit="B", unit_scale=True, unit_divisor=1024,
                desc=f"  {D.DOWNLOAD} {file_type}", leave=False,
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]",
            ) as pbar:
                with open(temp_file_path, "wb") as f:
                    async for chunk in response.content.iter_chunked(8192):
                        f.write(chunk)
                        pbar.update(len(chunk))
        return True
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        if not (isinstance(e, aiohttp.ClientResponseError) and e.status in (403, 404)):
            log(f"      {D.ERROR} Download error for {url}: {type(e).__name__}")
    except Exception as e:
        log(f"      {D.ERROR} Unexpected download error for {url}: {e}")
    return False
