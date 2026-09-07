import pytest

from base_api import BaseCore
from pornhub_api.modules.consts import get_m3u8_urls


@pytest.mark.asyncio
async def test_missing_dimensions_preserve_pornhub_quality_tiers():
    expected_qualities = [240, 480, 720, 1080]
    media_definitions = [
        {
            "format": "hls",
            "quality": quality,
            "videoUrl": f"https://example.test/{quality}P_/index.m3u8",
        }
        for quality in expected_qualities
    ]

    playlist_lines = ["#EXTM3U"]
    for (width, height), url in get_m3u8_urls(media_definitions).items():
        playlist_lines.append(
            f"#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION={width}x{height}"
        )
        playlist_lines.append(url)

    async with BaseCore() as core:
        assert await core.list_available_qualities(
            "\n".join(playlist_lines)
        ) == expected_qualities
