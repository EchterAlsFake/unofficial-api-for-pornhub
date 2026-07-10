import pytest
from base_api import DownloadConfigHLS

from pornhub_api import Client

@pytest.fixture
def client():
    return Client()

@pytest.mark.asyncio
async def test_video(client):
    client = Client()
    # By default this uses HubTraffic API
    video = await client.get_video("https://www.pornhub.com/view_video.php?viewkey=ph61d5d646249b2", load_api=True, load_html=True)

    # These should be available via API data without HTML
    assert isinstance(video.title, str) and len(video.title) > 3
    assert isinstance(video.publish_date, str) and len(video.publish_date) > 1
    assert isinstance(video.duration, int)
    assert isinstance(video.likes, str)
    assert isinstance(video.thumbnail, str)

    assert isinstance(video.available_qualities, list) and len(video.available_qualities) > 0
    assert isinstance(video.is_vertical, bool)
    assert isinstance(video.is_video_unavailable, bool)
    assert isinstance(video.is_vr, bool)
    assert isinstance(video.is_hd, bool)
    assert isinstance(video.author_thumbnail, str) and len(video.author_thumbnail) > 0
    # After HTML fetch, these might be dicts if from scrape or list if from API
    assert isinstance(video.categories, (dict, list))
    assert isinstance(video.tags, (dict, list))
    assert isinstance(video.m3u8_base_url, str)
    assert isinstance(video.is_video_unavailable_in_your_country, bool)

    config = DownloadConfigHLS(quality="best", return_report=True, path="./")
    stuff = await video.download(config)
    assert stuff["status"] == "completed"

