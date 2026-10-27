import os
import pytest
from pornhub_api import Client
from pornhub_api.api import UserHelper

@pytest.fixture
def client():
    return Client()


@pytest.mark.asyncio
async def test_gif_from_pornstar(client):
    pornstar = await client.get_pornstar("https://www.pornhub.com/model/teddy-tarantino")
    idx = 0
    async for result in pornstar.get_gifs():
        gif = result.unwrap()
        await gif.load_sources("html")
        idx += 1
        assert isinstance(gif.title, str) and len(gif.title) > 0
        assert isinstance(gif.thumbnail, str) and len(gif.thumbnail) > 0
        assert isinstance(gif.publish_date, str) and len(gif.publish_date) > 0
        assert isinstance(gif.content_url, str) and len(gif.content_url) > 0
        assert isinstance(gif.tags, dict) and len(gif.tags) > 0
        assert isinstance(gif.vote_percentage, str)

        if idx >= 5:
            break

@pytest.mark.asyncio
async def test_pornstar(client):
    pornstar = await client.get_pornstar("https://www.pornhub.com/pornstar/danny-d")
    assert isinstance(pornstar.bio, str) and len(pornstar.bio) > 0
    assert isinstance(pornstar.about, str) and len(pornstar.about) > 0
    assert isinstance(pornstar.info, dict) and len(pornstar.info) > 0
    assert isinstance(pornstar.name, str) and len(pornstar.name) > 0
    assert isinstance(pornstar.user_id, str) and len(pornstar.user_id) > 0
    assert isinstance(pornstar.avatar, str) and len(pornstar.avatar) > 0
    assert isinstance(pornstar.cover, str) and len(pornstar.cover) > 0
    assert pornstar.is_verified is True
    assert isinstance(pornstar.subscribers, str) and len(pornstar.subscribers) > 0
    assert isinstance(pornstar.video_views, str) and len(pornstar.video_views) > 0
    assert isinstance(pornstar.ranks, dict) and len(pornstar.ranks) > 0
    assert isinstance(pornstar.social_links, dict)

    idx = 0
    async for result in pornstar.get_videos():
        video = result.unwrap()
        assert isinstance(video.title, str) and len(video.title) > 0
        idx += 1

        if idx >= 5:
            break

    idx = 0
    async for result in pornstar.get_uploads():
        video = result.unwrap()
        assert isinstance(video.title, str) and len(video.title) > 0
        idx += 1

        if idx >= 5:
            break

@pytest.mark.asyncio
async def test_model(client):
    pornstar = await client.get_model("https://www.pornhub.com/model/catalina-days")
    assert isinstance(pornstar.about, str) and len(pornstar.about) > 0
    assert isinstance(pornstar.info, dict) and len(pornstar.info) > 0
    assert isinstance(pornstar.name, str) and len(pornstar.name) > 0
    assert isinstance(pornstar.user_id, str) and len(pornstar.user_id) > 0
    assert isinstance(pornstar.avatar, str) and len(pornstar.avatar) > 0
    assert isinstance(pornstar.cover, str) and len(pornstar.cover) > 0
    assert pornstar.is_verified is True
    assert isinstance(pornstar.subscribers, str) and len(pornstar.subscribers) > 0
    assert isinstance(pornstar.video_views, str) and len(pornstar.video_views) > 0
    assert isinstance(pornstar.ranks, dict) and len(pornstar.ranks) > 0

    idx = 0
    async for result in pornstar.get_videos():
        video = result.unwrap()
        assert isinstance(video.title, str) and len(video.title) > 0
        idx += 1

        if idx >= 5:
            break

@pytest.mark.asyncio
async def test_user(client):
    user = await client.get_user("https://www.pornhub.com/users/chappy1918")
    assert isinstance(user.name, str) and len(user.name) > 0
    assert isinstance(user.user_id, str) and len(user.user_id) > 0
    assert isinstance(user.avatar, str) and len(user.avatar) > 0
    assert isinstance(user.cover, str) and len(user.cover) > 0
    assert isinstance(user.subscribers, str) and len(user.subscribers) > 0
    assert isinstance(user.videos_watched, str) and len(user.videos_watched) > 0


MODEL_HTML_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "model_hottiestwo.html")
PORNSTAR_HTML_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "pornstar_dannyd.html")
USER_HTML_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "user_chappy.html")


def test_user_extract_model_sample():
    if not os.path.exists(MODEL_HTML_PATH):
        pytest.skip("Sample HTML file not found")

    with open(MODEL_HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    data = UserHelper._extract_html(html, "https://www.pornhub.com/model/hottiestwo")
    assert data["name"] == "HottiesTwo"
    assert data["user_id"] == "1558626451"
    assert data["is_verified"] is True
    assert data["subscribers_count"] == 482588
    assert data["video_views_count"] == 388421253
    assert data["ranks"]["Model Rank"] == "23"
    assert "Hey hottie" in data["about"]
    assert data["social_links"]["Twitter"] == "https://x.com/hottiestwo?s=11"
    assert data["external_link"] == "https://www.fanhub.com/@hottiestwo"
    assert data["external_link_text"] == "More of Me"
    assert data["gender"] == "Couple"
    assert data["birth_place"] == "Russia"
    assert data["relationship_status"] == "Taken"


def test_user_extract_pornstar_sample():
    if not os.path.exists(PORNSTAR_HTML_PATH):
        pytest.skip("Sample HTML file not found")

    with open(PORNSTAR_HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    data = UserHelper._extract_html(html, "https://www.pornhub.com/pornstar/danny-d")
    assert data["name"] == "Danny D"
    assert data["user_id"] == "721433281"
    assert data["is_verified"] is True
    assert data["is_award_winner"] is True
    assert data["subscribers_count"] == 214611
    assert data["video_views_count"] == 1376652061
    assert data["profile_views_count"] == 54989746
    assert data["videos_watched_count"] == 136
    assert "chicks of porn valley" in data["bio"]
    assert "Onlyfans.com/DannyD" in data["about"]
    assert data["ranks"]["Model Rank"] == "80"
    assert data["social_links"]["Twitter"] == "https://www.twitter.com/@DannyDxxx"
    assert data["gender"] == "Male"
    assert data["birth_place"] == "Maidstone, United Kingdom"


def test_user_extract_user_sample():
    if not os.path.exists(USER_HTML_PATH):
        pytest.skip("Sample HTML file not found")

    with open(USER_HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    data = UserHelper._extract_html(html, "https://www.pornhub.com/users/chappy1918")
    assert data["name"] == "Chappy1918"
    assert data["user_id"] == "2176853831"
    assert data["is_verified"] is False
    assert data["subscribers_count"] == 28
    assert data["videos_watched_count"] == 2692
    assert data["bio"] is None
    assert data["about"] is None


def test_user_extract_fallbacks():
    empty_html = "<html><body><div>Empty</div></body></html>"
    data = UserHelper._extract_html(empty_html, "https://www.pornhub.com/pornstar/danny-d")
    assert data["name"] == "Danny D"
    assert data["user_id"] is None
    assert data["avatar"] is None
    assert data["cover"] is None
    assert data["is_verified"] is False
    assert data["bio"] is None
    assert data["about"] is None
    assert data["info"] == {}
    assert data["ranks"] == {}
    assert data["social_links"] == {}
    assert data["subscribers"] is None
    assert data["video_views"] is None


