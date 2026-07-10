import pytest
from pornhub_api import Client

@pytest.fixture
def client():
    return Client()


@pytest.mark.asyncio
async def test_gif_from_pornstar(client):
    pornstar = await client.get_pornstar("https://www.pornhub.com/model/teddy-tarantino")
    idx = 0
    async for gif in pornstar.get_gifs(load_html=True):
        idx += 1
        assert isinstance(gif.video.title, str) and len(gif.video.title) > 0
        assert isinstance(gif.video.thumbnail, str) and len(gif.video.thumbnail) > 0
        assert isinstance(gif.video.publish_date, str) and len(gif.video.publish_date) > 0
        assert isinstance(gif.video.content_url, str) and len(gif.video.content_url) > 0
        assert isinstance(gif.video.tags, dict) and len(gif.video.tags) > 0
        assert isinstance(gif.video.vote_percentage, str)

        if idx >= 5:
            break

@pytest.mark.asyncio
async def test_pornstar(client):
    pornstar = await client.get_pornstar("https://www.pornhub.com/pornstar/danny-d")
    assert isinstance(pornstar.bio, str) and len(pornstar.bio) > 0
    assert isinstance(pornstar.about, str) and len(pornstar.about) > 0
    assert isinstance(pornstar.info, dict) and len(pornstar.info) > 0

    idx = 0
    async for video in pornstar.get_videos():
        assert isinstance(video.video.title, str) and len(video.video.title) > 0
        idx += 1

        if idx >= 5:
            break

    idx = 0
    async for video in pornstar.get_uploads():
        assert isinstance(video.video.title, str) and len(video.video.title) > 0
        idx += 1

        if idx >= 5:
            break

@pytest.mark.asyncio
async def test_model(client):
    pornstar = await client.get_model("https://www.pornhub.com/model/catalina-days")
    assert isinstance(pornstar.about, str) and len(pornstar.about) > 0
    assert isinstance(pornstar.info, dict) and len(pornstar.info) > 0

    idx = 0
    async for video in pornstar.get_videos():
        assert isinstance(video.video.title, str) and len(video.video.title) > 0
        idx += 1

        if idx >= 5:
            break