email = None
password = None

import pytest
from pornhub_api import Client


@pytest.mark.asyncio
async def test_all():
    client = Client(email=email, password=password)
    await client.login()

    async for result in client.get_history():
        video = result.unwrap()
        assert isinstance(video.title, str) and len(video.title) > 1

    async for result in client.get_favorites():
        video = result.unwrap()
        assert isinstance(video.title, str) and len(video.title) > 1

    async for result in client.get_recommended():
        video = result.unwrap()
        assert isinstance(video.title, str) and len(video.title) > 1
