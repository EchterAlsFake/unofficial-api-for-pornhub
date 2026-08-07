import pytest

from pornhub_api import Client, User

@pytest.fixture
def client():
    return Client()

@pytest.mark.asyncio
async def test_playlist(client):
    playlist = await client.get_playlist("https://www.pornhub.com/playlist/119820351")
    assert isinstance(playlist.title, str) and len(playlist.title) > 0
    assert isinstance(playlist.tags, dict)
    assert isinstance(playlist.token, str) and len(playlist.token) > 0
    assert isinstance(playlist.playlist_id, str) and len(playlist.playlist_id) > 0
    assert isinstance(playlist.views, str) and len(playlist.views) > 0
    assert isinstance(playlist.unavailable_videos, int)
    assert isinstance(playlist.rating_percent, str) and len(playlist.rating_percent) > 0
    assert isinstance(playlist.video_count, str) and len(str(playlist.video_count)) > 0
    assert isinstance(playlist.description, str) and len(playlist.description) > 0
    assert isinstance(playlist.likes, str)
    assert isinstance(playlist.dislikes, str)
    assert isinstance(await playlist.get_author(), User)

    idx = 0
    async for result in playlist.get_videos():
        idx += 1
        assert isinstance(result.unwrap().title, str)

        if idx == 5:
            break
