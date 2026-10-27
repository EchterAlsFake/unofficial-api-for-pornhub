
import os
import re
import pytest
from base_api import DownloadConfigHLS

from pornhub_api import Client
from pornhub_api.api import Short


@pytest.fixture
def client():
    return Client()


@pytest.mark.asyncio
async def test_short(client, tmp_path):
    short = await client.get_short("https://www.pornhub.com/shorties/6a346596ea4ff", load_html=True)
    assert isinstance(short.title, str) and len(short.title) > 0
    assert short.video_key == "6a346596ea4ff"
    assert short.video_id == "486701795"
    assert short.author_name == "Mia Maripolla"
    assert short.author_link == "https://www.pornhub.com/model/mia-maripolla"
    assert short.is_hd is True
    assert isinstance(short.thumbnail, str) and short.thumbnail.startswith("http")
    assert isinstance(short.avatar, str) and short.avatar.startswith("http")
    assert isinstance(short.likes, str)
    assert isinstance(short.dislikes, str)
    assert isinstance(short.favorites, str)
    assert isinstance(short.video_url, str) and "viewkey=6a346596ea4ff" in short.video_url
    assert isinstance(short.m3u8_base_url, str) and "#EXTM3U" in short.m3u8_base_url
    assert isinstance(short.media_definitions, list) and len(short.media_definitions) > 0

    # New attributes
    assert short.duration == 98
    assert short.categories is None or (isinstance(short.categories, list) and len(short.categories) > 0)
    assert short.tags is None or (isinstance(short.tags, list) and len(short.tags) > 0)
    assert short.is_verified is True
    assert isinstance(short.like_count, int)
    assert isinstance(short.dislike_count, int)
    assert isinstance(short.like_info, str)
    assert isinstance(short.favorite_info, str)
    assert isinstance(short.token, str) and len(short.token) > 0
    assert short.author_id == "2163184851"
    assert short.author_type == "Mpp"
    assert short.external_link == "https://onlyfans.com/miahomemade"
    assert short.external_link_text == "More of Me"
    assert isinstance(short.large_preview_url, str) and short.large_preview_url.startswith("http")
    assert isinstance(short.shortie_url, str) and "6a346596ea4ff" in short.shortie_url
    assert isinstance(short.direct_video_url, str) and short.direct_video_url.startswith("http")

    # Helper methods
    author = await short.get_author()
    assert author is not None
    assert author.url == short.author_link

    video = await short.get_video()
    assert video is not None
    assert video.url == short.video_url

    # Download
    config = DownloadConfigHLS(quality="240", path=str(tmp_path))
    assert await short.download(config) is True


def test_short_extract_html_sample(client):
    sample_file = os.path.join(os.path.dirname(__file__), "fixtures", "short_sample.html")
    with open(sample_file) as f:
        sample_html = f.read()

    short = Short(core=client.core, url="https://www.pornhub.com/shorties/6a346596ea4ff")
    data = short._extract_html(sample_html)

    # First shortie in feed
    assert data["title"] == "The Wife's Turn - It's Time for Her Wet Pussy to Get All Attention"
    assert data["video_id"] == "486701795"
    assert data["video_key"] == "6a346596ea4ff"
    assert data["author_name"] == "Mia Maripolla"
    assert data["author_link"] == "https://www.pornhub.com/model/mia-maripolla"
    assert data["author_id"] == "2163184851"
    assert data["author_type"] == "Mpp"
    assert data["is_hd"] is True
    assert data["duration"] == 98
    assert data["like_count"] == 2856
    assert data["dislike_count"] == 126
    assert data["like_info"] == "2.9K"
    assert data["favorite_info"] == "394"
    assert data["is_verified"] is True
    assert data["external_link"] == "https://onlyfans.com/miahomemade"
    assert data["external_link_text"] == "More of Me"
    assert "Amateur" in data["categories"]
    assert "Pussy fingering" in data["tags"]
    assert data["token"] is not None and len(data["token"]) > 0
    assert data["m3u8_base_url"] is not None and "#EXTM3U" in data["m3u8_base_url"]

    # Specific vkey matching (second shortie in feed)
    data2 = short._extract_html(sample_html, url="https://www.pornhub.com/shorties/ph6083545d78c6e")
    assert data2["title"] == "You Need To Suck My Big Porn Titties After You Fuck Me"
    assert data2["video_id"] == "387046921"
    assert data2["video_key"] == "ph6083545d78c6e"
    assert data2["author_name"] == "Stacey Squeaks"
    assert data2["author_link"] == "https://www.pornhub.com/model/stacey-squeaks"
    assert data2["duration"] == 117
    assert data2["like_count"] == 6763
    assert data2["dislike_count"] == 615


def test_short_extract_html_fallbacks(client):
    sample_file = os.path.join(os.path.dirname(__file__), "fixtures", "short_sample.html")
    with open(sample_file) as f:
        sample_html = f.read()

    # Strip script tags to test pure DOM extraction
    no_scripts = re.sub(r"<script.*?</script>", "", sample_html, flags=re.DOTALL)
    short = Short(core=client.core, url="https://www.pornhub.com/shorties/6a346596ea4ff")
    data = short._extract_html(no_scripts)

    assert data["title"] == "The Wife's Turn - It's Time for Her Wet Pussy to Get All Attention"
    assert data["author_name"] == "Mia Maripolla"
    assert data["video_key"] == "6a346596ea4ff"
    assert data["like_count"] == 2856
    assert data["dislike_count"] == 126
    assert data["is_verified"] is True
    assert data["token"] is not None

    # Empty HTML with URL containing vkey (verifies URL fallback for video_key)
    data_with_vkey = short._extract_html("")
    assert data_with_vkey["title"] is None
    assert data_with_vkey["video_key"] == "6a346596ea4ff"
    assert data_with_vkey["author_name"] is None
    assert data_with_vkey["is_hd"] is False

    # Empty HTML with generic URL
    generic_short = Short(core=client.core, url="https://www.pornhub.com/shorties")
    data_empty = generic_short._extract_html("")
    assert data_empty["title"] is None
    assert data_empty["video_key"] is None
    assert data_empty["author_name"] is None
    assert data_empty["is_hd"] is False

    # URL slug title fallback
    short_slug = Short(core=client.core, url="https://www.pornhub.com/shorties/amazing-short-video")
    data_slug = short_slug._extract_html("<html><body></body></html>")
    assert data_slug["title"] == "Amazing Short Video"
