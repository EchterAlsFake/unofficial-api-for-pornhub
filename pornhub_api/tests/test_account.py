email = None
password = None

import pytest
from pornhub_api import Client


@pytest.mark.asyncio
async def test_all():
    client = Client(email=email, password=password)
    await client.login()

    async for video in client.get_history():
        assert isinstance(video.video.title, str) and len(video.video.title) > 1

    async for video in client.get_favorites():
        assert isinstance(video.video.title, str) and len(video.video.title) > 1

    async for video in client.get_recommended():
        assert isinstance(video.video.title, str) and len(video.video.title) > 1