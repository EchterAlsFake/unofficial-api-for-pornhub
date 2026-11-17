"""Live regression for model upload tabs that alias the main video listing."""
from contextlib import aclosing
from urllib.parse import urljoin

import pytest
from base_api import BaseCore, ErrorMode, ResultOrder, make_iterator_config
from pornhub_api import Client
from selectolax.lexbor import LexborHTMLParser


@pytest.mark.asyncio
async def test_aliased_uploads_match_complete_live_listing():
    url = "https://www.pornhub.com/model/porn-force"
    config = make_iterator_config(
        order=ResultOrder.ORIGINAL, load_specific_sources=(),
        page_error_mode=ErrorMode.RAISE, item_error_mode=ErrorMode.RAISE,
    )
    async with BaseCore() as core:
        # Independently read the rendered listing and its real navigation links.
        pages, expected, visited = [], [], set()
        following = url + "/videos/upload?page=1"
        while following:
            assert following not in visited, "Source navigation looped"
            visited.add(following)
            tree = LexborHTMLParser(await core.fetch_text(following))
            listing = tree.css_first("#mostRecentVideosSection")
            assert listing is not None, "Missing source video listing"
            ids = [row.attributes["data-video-vkey"] for row in listing.css("li[data-video-vkey]")]
            assert ids, "Unexpected empty source page"
            pages.append(ids)
            expected.extend(ids)
            link = tree.css_first(".page_next:not(.disabled) a[href]")
            following = urljoin(following, link.attributes["href"]) if link else None
        assert len(pages) > 1 and len(expected) > 40

        target = await Client(core=core).get_model(url)

        async def collect(**options):
            async with aclosing(target.get_uploads(iterator_config=config, **options)) as stream:
                return [r.unwrap().video_id async for r in stream]

        actual = await collect(pages="all")
        assert actual == expected, "Upload stream differs from the live listing order"
        assert await collect(pages=1, start_page=2) == pages[1]
        assert await collect(pages=2) == pages[0] + pages[1]
        assert await collect(pages=1, start_page=len(pages)) == pages[-1]
        assert await collect(pages=1, start_page=len(pages) + 1) == []
        print(f"Matched {len(actual)} videos across {len(pages)} live pages.")
