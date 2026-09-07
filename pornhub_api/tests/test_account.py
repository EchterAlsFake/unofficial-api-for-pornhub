import os
import pytest
from pornhub_api import Client

email = os.environ.get("PORNHUB_EMAIL")
password = os.environ.get("PORNHUB_PASSWORD")


@pytest.mark.asyncio
async def test_all():
    if not email or not password:
        pytest.skip("Pornhub credentials not configured in environment (PORNHUB_EMAIL, PORNHUB_PASSWORD)")
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
