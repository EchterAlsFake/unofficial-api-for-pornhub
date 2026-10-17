"""Offline regressions for URL identity, pagination and listing contracts."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from base_api import BaseCore
from base_api.modules.config import RuntimeConfig
from pornhub_api import api as api


class EmptyStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class UrlHandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_filtered_profile_fetches_two_distinct_pages(self):
        media = api.Channel(url="https://de.pornhub.com/channels/Example/videos?o=mr&page=9#videos", core=object())
        capture = Mock(return_value=EmptyStream())
        from contextlib import ExitStack

        with ExitStack() as stack:
            for name in ("scrape_stream", "_scrape_stream"):
                if hasattr(api, name):
                    stack.enter_context(patch.object(api, name, capture))
            if hasattr(api, "Helper"):
                stack.enter_context(patch.object(api, "Helper", lambda **kwargs: SimpleNamespace(iterator=capture)))
            stack.enter_context(patch.object(api.Channel, "get_field", AsyncMock(return_value=2)))
            async for _ in media.get_videos(pages=2):
                pass
        urls = capture.call_args.kwargs["target_page_urls"]
        self.assertEqual(
            urls,
            [
                "https://de.pornhub.com/channels/Example/videos?o=mr&page=1",
                "https://de.pornhub.com/channels/Example/videos?o=mr&page=2",
            ],
        )

    def test_embed_uses_full_watch_page_on_same_host(self):
        video = api.Video(url="https://de.pornhub.com/embed/phAbC?utm_source=share", core=object())
        self.assertEqual(video.video_id, "phAbC")
        self.assertEqual(video.url, "https://de.pornhub.com/view_video.php?utm_source=share&viewkey=phAbC")

    async def test_client_preserves_configured_language(self):
        config = RuntimeConfig()
        config.locale = "de-DE,de;q=0.9"
        async with BaseCore(configuration=config) as core:
            api.Client(core=core)
            self.assertEqual(core.session.headers.get("Accept-Language"), config.locale)
            self.assertEqual(core.total_requests, 0)


if __name__ == "__main__":
    unittest.main()
