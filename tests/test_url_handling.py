"""URL identity regressions and opt-in live channel pagination checks."""

import os
import unittest
from contextlib import aclosing
from urllib.parse import urlencode, urljoin

from base_api import BaseCore, ErrorMode, ResultOrder, make_iterator_config
from base_api.modules.config import RuntimeConfig
from pornhub_api import api
from selectolax.lexbor import LexborHTMLParser


class UrlHandlingTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(
        os.environ.get("PORNHUB_LIVE_CHANNEL_PAGINATION") == "1",
        "Set PORNHUB_LIVE_CHANNEL_PAGINATION=1 for live channel requests",
    )
    async def test_filtered_profile_matches_the_live_listing(self):
        configuration = RuntimeConfig()
        configuration.request_attempts = 1
        configuration.timeout = 15
        iterator = make_iterator_config(
            order=ResultOrder.ORIGINAL, load_specific_sources=(),
            page_error_mode=ErrorMode.RAISE, item_error_mode=ErrorMode.RAISE,
        )
        async with BaseCore(configuration=configuration) as core:
            client = api.Client(core=core)
            for host, order in (("www", "mr"), ("de", "vi"), ("www", "ra")):
                with self.subTest(host=host, order=order):
                    profile = f"https://{host}.pornhub.com/channels/candyai"
                    channel = await client.get_channel(f"{profile}/videos?o={order}&page=9#videos")
                    self.assertEqual(channel.resolved_url, profile)
                    # Match the site's parameter order so both scans compare one live ranking snapshot.
                    query = {} if order == "mr" else {"o": order}
                    query["page"] = "1"
                    following = f"{profile}/videos?{urlencode(query)}"
                    source_pages, visited = [], set()
                    while following:
                        self.assertNotIn(following, visited, "Source navigation looped")
                        visited.add(following)
                        tree = LexborHTMLParser(await core.fetch_text(following))
                        listing = tree.css_first("#showAllChanelVideos")
                        self.assertIsNotNone(listing)
                        ids = [row.attributes["data-video-vkey"] for row in listing.css("li[data-video-vkey]")]
                        self.assertTrue(ids)
                        source_pages.append(ids)
                        link = tree.css_first(".page_next:not(.disabled) a[href]")
                        following = urljoin(following, link.attributes["href"]) if link else None
                    self.assertGreaterEqual(len(source_pages), 2)
                    self.assertNotEqual(source_pages[0], source_pages[1])
                    expected = [video_id for page in source_pages for video_id in page]

                    async def collect(**options):
                        async with aclosing(channel.get_videos(iterator_config=iterator, **options)) as stream:
                            return [result.unwrap().video_id async for result in stream]

                    self.assertEqual(await collect(pages="all"), expected)
                    self.assertEqual(await collect(pages=2), source_pages[0] + source_pages[1])
                    self.assertEqual(await collect(pages=1, start_page=2), source_pages[1])
                    self.assertEqual(await collect(pages=1, start_page=len(source_pages)), source_pages[-1])
                    self.assertEqual(await collect(pages=1, start_page=len(source_pages) + 1), [])
                    print(f"Matched {len(expected)} videos across {len(source_pages)} pages for {host}, {order}.")

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
