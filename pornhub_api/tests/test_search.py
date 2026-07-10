import pytest
from pornhub_api import Client

@pytest.fixture
def client():
    return Client()


@pytest.mark.asyncio
async def test_gif_from_search(client):

    idx = 0
    async for gif in client.search_gifs("fortnite", load_html=True):
        idx += 1
        assert isinstance(gif.video.title, str) and len(gif.video.title) > 0
        assert isinstance(gif.video.thumbnail, str) and len(gif.video.thumbnail) > 0
        assert isinstance(gif.video.publish_date, str) and len(gif.video.publish_date) > 0
        assert isinstance(gif.video.content_url, str) and len(gif.video.content_url) > 0
        assert isinstance(gif.video.tags, dict) and len(gif.video.tags) > 0
        assert isinstance(gif.video.vote_count, str)
        assert isinstance(gif.video.vote_percentage, str)

        if idx >= 5:
            break

@pytest.mark.asyncio
async def test_search(client):
    idx = 0
    async for video in client.search_videos("fortnite", load_html=False, load_api=True):
        idx += 1
        assert isinstance(video.video.title, str) and len(video.video.title) > 0
        assert isinstance(video.video.duration, int)

        if idx == 5:
            break
