import os
import pytest
from base_api import DownloadConfigHLS, DownloadConfigRAW
from pornhub_api import Client, Video, Short, GIF, Album, Channel, User, Model, Pornstar
from pornhub_api.modules.consts import extractor_hubtraffic, extractor_model_uploads, extractor_model_videos


@pytest.fixture
def client():
    return Client()


@pytest.mark.asyncio
async def test_video_url_formats(client):
    # 1. Bare viewkey
    v1 = Video(url="ph618ed49fbee04", core=client.core)
    assert v1.video_id == "ph618ed49fbee04"
    assert v1.url == "https://www.pornhub.com/view_video.php?viewkey=ph618ed49fbee04"

    # 2. Relative url
    v2 = Video(url="/view_video.php?viewkey=ph618ed49fbee04", core=client.core)
    assert v2.video_id == "ph618ed49fbee04"
    assert v2.url == "https://www.pornhub.com/view_video.php?viewkey=ph618ed49fbee04"

    # 3. Embed url
    v3 = Video(url="https://www.pornhub.com/embed/ph618ed49fbee04", core=client.core)
    assert v3.video_id == "ph618ed49fbee04"
    assert "viewkey=ph618ed49fbee04" in v3.url

    # 4. Regional url
    v4 = Video(url="https://de.pornhub.com/view_video.php?viewkey=ph618ed49fbee04", core=client.core)
    assert v4.video_id == "ph618ed49fbee04"
    assert v4.url == "https://de.pornhub.com/view_video.php?viewkey=ph618ed49fbee04"


@pytest.mark.asyncio
async def test_video_author_resolution(client):
    video = await client.get_video("ph618ed49fbee04", load_html=True, load_api=True)
    assert video.author_name is not None
    assert video.author_link is not None

    author_from_method = await video.get_author(load_html=True)
    assert author_from_method is not None
    assert author_from_method.url == video.author_link

    author_from_prop = await video.author
    assert author_from_prop is not None
    assert author_from_prop.url == video.author_link


@pytest.mark.asyncio
async def test_short_get_video_and_author(client):
    short = await client.get_short("6a346596ea4ff", load_html=True)
    assert short.short_id == "6a346596ea4ff"
    assert short.video_key == "6a346596ea4ff"
    assert short.video_id == "486701795"

    full_video = await short.get_video(load_html=True)
    assert full_video is not None
    assert full_video.video_id == "6a346596ea4ff"
    assert full_video.title is not None

    author = await short.get_author(load_html=False)
    assert author is not None
    assert author.url == short.author_link

    author_prop = await short.author
    assert author_prop is not None
    assert author_prop.url == short.author_link


@pytest.mark.asyncio
async def test_gif_get_source_video_and_author(client):
    gif = await client.get_gif("55318721", load_html=True)
    assert gif.gif_id == "55318721"

    source_video = await gif.get_source_video(load_html=True)
    assert source_video is not None
    assert source_video.url == gif.source_video_url

    author = await gif.get_author(load_html=False)
    assert author is not None
    assert author.url == gif.author_link

    author_prop = await gif.author
    assert author_prop is not None
    assert author_prop.url == gif.author_link


@pytest.mark.asyncio
async def test_album_photos_and_download(client, tmp_path):
    album = await client.get_album("80426065", load_html=True)
    assert album.album_id == "80426065"

    photos = []
    async for p in album.get_photos(pages=1):
        photos.append(p)
        if len(photos) >= 3:
            break
    assert len(photos) >= 1
    sample = photos[0]
    assert sample["photo_id"] is not None
    assert sample["download_url"] is not None

    # Test file path download
    file_dest = str(tmp_path / "custom_photo_name.jpg")
    success1 = await album.download_photo(sample["download_url"], file_dest)
    assert success1 is True
    assert os.path.exists(file_dest)
    assert os.path.getsize(file_dest) > 0

    # Test directory path download
    dir_dest = str(tmp_path / "album_photos")
    os.makedirs(dir_dest, exist_ok=True)
    success2 = await album.download_photo(sample["download_url"], dir_dest)
    assert success2 is True
    downloaded_files = os.listdir(dir_dest)
    assert len(downloaded_files) == 1
    assert downloaded_files[0].endswith((".jpg", ".png", ".webp", ".jpeg"))

    # Author
    author = await album.get_author(load_html=False)
    assert author is not None
    assert author.url == album.author_link

    author_prop = await album.author
    assert author_prop is not None
    assert author_prop.url == album.author_link


@pytest.mark.asyncio
async def test_user_and_model_listings(client):
    # Test User listings (chappy1918)
    user = await client.get_user("chappy1918")
    assert user.name == "Chappy1918"

    user_videos = []
    async for v in user.get_videos(pages=1):
        if v.succeeded:
            user_videos.append(v.unwrap())
            if len(user_videos) >= 2:
                break
    assert len(user_videos) > 0

    user_uploads = []
    async for v in user.get_uploads(pages=1):
        if v.succeeded:
            user_uploads.append(v.unwrap())
            if len(user_uploads) >= 2:
                break
    assert len(user_uploads) > 0

    user_gifs = []
    async for g in user.get_gifs(pages=1):
        if g.succeeded:
            user_gifs.append(g.unwrap())
            if len(user_gifs) >= 2:
                break
    assert len(user_gifs) > 0

    # Test Model listings (mia-maripolla)
    model = await client.get_model("mia-maripolla")
    model_uploads = []
    async for v in model.get_uploads(pages=1):
        if v.succeeded:
            model_uploads.append(v.unwrap())
            if len(model_uploads) >= 2:
                break
    assert len(model_uploads) > 0

    model_gifs = []
    async for g in model.get_gifs(pages=1):
        if g.succeeded:
            model_gifs.append(g.unwrap())
            if len(model_gifs) >= 2:
                break
    assert len(model_gifs) > 0


@pytest.mark.asyncio
async def test_search_features_and_empty_handling(client):
    # Filtered search
    filtered_results = []
    async for res in client.search_videos("blonde", production_type="professional", sort_by="mr", pages=1):
        if res.succeeded:
            filtered_results.append(res.unwrap())
            if len(filtered_results) >= 2:
                break
    assert len(filtered_results) > 0

    # Hubtraffic search
    ht_results = []
    async for res in client.search_hubtraffic("teen", sort_by="mostviewed", period="alltime", pages=1):
        if res.succeeded:
            ht_results.append(res.unwrap())
            if len(ht_results) >= 2:
                break
    assert len(ht_results) > 0

    # Non-existent query edge case (should complete gracefully with 0 results)
    empty_query = "zxqjkfdshfkjsdhfkjdsh987123"
    empty_ht = [r async for r in client.search_hubtraffic(empty_query, pages=1)]
    assert len(empty_ht) == 0

    empty_videos = [r async for r in client.search_videos(empty_query, pages=1)]
    assert len(empty_videos) == 0

    empty_gifs = [r async for r in client.search_gifs(empty_query, pages=1)]
    assert len(empty_gifs) == 0


@pytest.mark.asyncio
async def test_multilingual_endpoints(client):
    # German channel stats parsing
    c_de = await client.get_channel("https://de.pornhub.com/channels/brazzers")
    assert c_de.name == "Brazzers"
    assert c_de.subscribers is not None
    assert c_de.video_views is not None

    # Spanish user stats parsing
    u_es = await client.get_user("https://es.pornhub.com/users/chappy1918")
    assert u_es.name == "Chappy1918"
    assert u_es.subscribers is not None

    # French video
    v_fr = await client.get_video("https://fr.pornhub.com/view_video.php?viewkey=ph618ed49fbee04", load_html=True)
    assert v_fr.title is not None
    assert len(v_fr.available_qualities) > 0
    assert v_fr.author_name is not None
