"""Bounded HLS recovery with the MP4 sources supplied by PornHub."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import chompjs
from base_api import CachePolicy, DownloadConfigHLS, DownloadConfigRAW
from base_api.modules.errors import AccessDeniedError, DownloadCancelled, HTTPStatusError, NotFound, ResourceGone
from curl_cffi.requests.exceptions import HTTPError

from pornhub_api.api import build_m3u8_master
from pornhub_api.modules.consts import REGEX_VIDEO_FLASHVARS
from pornhub_api.modules.errors import SourcesUnavailable

logger = logging.getLogger(__name__)

def _source_rejected(error: Exception, *, manifest: bool = False) -> bool:
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, HTTPError) and current.response is not None:
            if current.response.status_code in {403, 404, 410} and not manifest:
                return True
        if isinstance(current, (ResourceGone, NotFound, AccessDeniedError)) or (
            isinstance(current, HTTPStatusError) and current.status_code in {403, 404, 410}
        ):
            url = getattr(current, "url", None)
            if not url:
                match = re.search(r"for URL:\s*(\S+)", str(current))
                url = match.group(1) if match else ""
            if not manifest or urlsplit(url).path.lower().endswith(".m3u8"):
                return True
        pending.extend(e for e in (current.__cause__, current.__context__) if e is not None)
    return False


def _definitions(page: str) -> list[dict]:
    match = REGEX_VIDEO_FLASHVARS.search(page)
    if not match:
        # Parsing/site changes must not be reported as a known CDN failure.
        raise ValueError("PornHub video page has no readable media definitions")
    return chompjs.parse_js_object(match.group(1)).get("mediaDefinitions") or []


def _check_stop(configuration):
    if configuration.stop_event is not None and configuration.stop_event.is_set():
        raise DownloadCancelled("Download cancelled.")


async def _raw_url(core, definitions: list[dict], quality) -> str | None:
    sources = []
    for definition in definitions:
        if definition.get("format") != "mp4" or not definition.get("videoUrl"):
            continue
        if definition.get("remote"):
            try:
                payload = await core.fetch_text(definition["videoUrl"], cache_policy=CachePolicy.BYPASS)
            except Exception as error:
                if _source_rejected(error):
                    continue
                raise
            sources.extend(json.loads(payload))
        else:
            sources.append(definition)
    renditions = {}
    for source in sources:
        if source.get("format") != "mp4" or not source.get("videoUrl"):
            continue
        value = str(source.get("quality", "")).removesuffix("p")
        if value.isdigit():
            renditions[int(value)] = source["videoUrl"]
    if not renditions:
        return None
    # Never increase a numeric selection, including the free quality ceiling.
    if str(quality).removesuffix("p").isdigit():
        allowed = [q for q in renditions if q <= int(str(quality).removesuffix("p"))]
        return renditions[max(allowed)] if allowed else None
    from base_api.modules.static_functions import choose_quality_from_list
    return renditions[choose_quality_from_list(renditions, quality)]


async def download(video, hls: DownloadConfigHLS, raw: DownloadConfigRAW, *, on_raw=None, raw_only=False):
    """Try four refreshed HLS pages, then RAW; stop after eight total refreshes."""
    _check_stop(hls)
    await video.load_fields("m3u8_base_url")
    core = video.core
    validate_playback = getattr(core, "validate_playback", None)
    if validate_playback is not None:
        premium = video.is_premium if video.is_field_loaded("is_premium") else None
        validate_playback(video.url, is_premium=premium)
    master = video.m3u8_base_url
    definitions = []
    last_error = None
    # A retained RAW file must not be overwritten by a later HLS attempt.
    raw_checkpoint = Path(raw.resume_state_path or f"{raw.path}.raw-state.json")
    resume_raw = raw_checkpoint.is_file()
    for refresh in range(9):
        _check_stop(hls)
        if refresh:
            logger.info("Refreshing PornHub download sources (%s/8)", refresh)
            page = await core.fetch_text(video.url, cache_policy=CachePolicy.BYPASS)
            _check_stop(hls)
            if validate_playback is not None:
                refreshed = await asyncio.to_thread(video._extract_html, page, video.url)
                validate_playback(video.url, is_premium=refreshed.get("is_premium"))
            definitions = await asyncio.to_thread(_definitions, page)
            master = build_m3u8_master(definitions)
        if master and not resume_raw and not raw_only:
            config = copy.copy(hls)
            config.m3u8_base_url = master
            try:
                return await core.download(configuration=config)
            except Exception as error:
                if not _source_rejected(error, manifest=True):
                    raise
                last_error = error
        if refresh >= 4 or ((resume_raw or raw_only) and refresh):
            _check_stop(hls)
            url = await _raw_url(core, definitions, raw.quality)
            if url:
                try:
                    # Validate source availability before RAW creates a partial file.
                    await core.request(url, method="HEAD")
                except Exception as error:
                    if not _source_rejected(error):
                        raise
                    last_error = error
                    continue
                _check_stop(hls)
                logger.info("Using PornHub MP4 fallback")
                if on_raw:
                    on_raw()
                try:
                    result = await core.legacy_download(url=url, configuration=raw)
                except Exception as error:
                    if not _source_rejected(error):
                        raise
                    last_error = error
                    resume_raw = raw_checkpoint.is_file()
                    continue
                if result is not False:
                    # HLS and RAW have separate checkpoints; release stale HLS
                    # files only once RAW has actually completed.
                    if hls.segment_dir:
                        await asyncio.to_thread(shutil.rmtree, hls.segment_dir, True)
                    if hls.segment_state_path:
                        Path(hls.segment_state_path).unlink(missing_ok=True)
                return result
    _check_stop(hls)
    raise SourcesUnavailable("PornHub did not provide a working HLS or MP4 source after 8 page refreshes.", url=video.url) from last_error
