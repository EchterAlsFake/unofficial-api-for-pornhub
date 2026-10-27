import os
import pytest
from pornhub_api import Client, Model
from pornhub_api.api import Album, UserHelper


@pytest.fixture
def client() -> Client:
    return Client()


SAMPLE_HTML_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "album_sample.html")


@pytest.mark.asyncio
async def test_album(client, tmp_path):
    album = await client.get_album("https://www.pornhub.com/album/80426065", load_html=True)

    # Core & identification
    assert album.album_id == "80426065"
    assert album.title == "Tits & Cleavage"
    assert isinstance(album.token, str) and len(album.token) > 0

    # Author details
    assert album.author_name == "LustyPeachesofSin"
    assert album.author_link == "https://www.pornhub.com/model/lustypeachesofsin"
    assert album.author_id == "3563319871"
    assert isinstance(album.avatar, str) and album.avatar.startswith("http")
    assert album.author_avatar == album.avatar
    assert album.is_verified is True

    # Author helpers
    author = await album.get_author()
    assert isinstance(author, (Model, UserHelper))
    assert author.url == album.author_link

    author_prop = await album.author
    assert isinstance(author_prop, (Model, UserHelper))
    assert author_prop.url == album.author_link

    # Stats & metadata
    assert isinstance(album.publish_date, str) and len(album.publish_date) > 0
    assert isinstance(album.rating_percentage, str) and len(album.rating_percentage) > 0
    assert isinstance(album.tags, dict) and len(album.tags) > 0
    assert "big boobs" in album.tags
    assert isinstance(album.views, str) and len(album.views) > 0
    assert isinstance(album.views_count, int) and album.views_count > 0
    assert isinstance(album.votes, str) and len(album.votes) > 0
    assert isinstance(album.vote_count, int) and album.vote_count > 0
    assert album.segment == "Miscellaneous"
    assert isinstance(album.total_pages, int) and album.total_pages >= 1

    # Photos field
    assert isinstance(album.photos, list) and len(album.photos) > 0
    assert album.photos_count == len(album.photos)

    # Photo iteration and download
    idx = 0
    async for photo in album.get_photos(pages=1):
        idx += 1
        assert isinstance(photo, dict)
        assert isinstance(photo.get("photo_id"), str)
        assert isinstance(photo.get("url"), str) and photo.get("url").startswith("http")
        assert isinstance(photo.get("views"), str)
        assert isinstance(photo.get("rating"), str)

        url = photo.get("download_url")
        assert isinstance(url, str) and url.startswith("http")

        # Download first photo to tmp_path
        if idx == 1:
            dest = str(tmp_path / "photo_test.jpg")
            assert await album.download_photo(url=url, path=dest) is True
            assert os.path.exists(dest) and os.path.getsize(dest) > 0

        if idx >= 3:
            break


def test_album_extract_html_sample():
    if not os.path.exists(SAMPLE_HTML_PATH):
        pytest.skip("Sample HTML file not found")

    with open(SAMPLE_HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    data = Album._extract_html(html, "https://www.pornhub.com/album/80426065")
    assert data["album_id"] == "80426065"
    assert data["title"] == "Tits & Cleavage"
    assert data["author_name"] == "LustyPeachesofSin"
    assert data["author_link"] == "https://www.pornhub.com/model/lustypeachesofsin"
    assert data["author_id"] == "3563319871"
    assert data["is_verified"] is True
    assert data["rating_percentage"] == "100%"
    assert data["vote_count"] == 12
    assert data["views_count"] == 1136
    assert data["publish_date"] == "5 months ago"
    assert data["segment"] == "Miscellaneous"
    assert isinstance(data["tags"], dict) and len(data["tags"]) == 3
    assert "big boobs" in data["tags"]
    assert "token" in data and len(data["token"]) > 0
    assert data["photos_count"] == 32

    photos = Album._parse_photos(html)
    assert len(photos) == 32
    assert photos[0]["photo_id"] == "864822725"
    assert photos[0]["url"] == "https://www.pornhub.com/photo/864822725"
    assert photos[0]["download_url"].startswith("http")
    assert photos[0]["rating"] == "100%"
    assert "132" in photos[0]["views"]


def test_album_extract_fallbacks():
    # Empty HTML fallback
    empty_html = "<html><body><div>Empty</div></body></html>"
    data = Album._extract_html(empty_html, "https://www.pornhub.com/album/998877")
    assert data["album_id"] == "998877"
    assert data["title"] is None
    assert data["author_name"] is None
    assert data["author_link"] is None
    assert data["author_id"] is None
    assert data["avatar"] is None
    assert data["author_avatar"] is None
    assert data["is_verified"] is False
    assert data["rating_percentage"] is None
    assert data["votes"] is None
    assert data["vote_count"] is None
    assert data["views"] is None
    assert data["views_count"] is None
    assert data["publish_date"] is None
    assert data["segment"] is None
    assert data["tags"] == {}
    assert data["token"] is None
    assert data["total_pages"] == 1
    assert data["photos"] == []
    assert data["photos_count"] == 0

    # Title fallback from <title> tag
    title_html = "<html><head><title>My Cool Album - ModelName's Albums</title></head><body></body></html>"
    data_title = Album._extract_html(title_html, "https://www.pornhub.com/album/555")
    assert data_title["title"] == "My Cool Album"
    assert data_title["author_name"] == "ModelName"

    # Photos parsing on empty content
    assert Album._parse_photos(empty_html) == []


