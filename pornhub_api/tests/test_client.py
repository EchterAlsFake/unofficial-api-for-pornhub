import pytest
from pornhub_api import Client, Video, Short, GIF, Album, Channel, User, Model, Pornstar, Playlist
from pornhub_api.modules.errors import LoginFailed


@pytest.fixture
def client():
    return Client()


@pytest.mark.asyncio
async def test_client_create_factory():
    client = await Client.create()
    assert client is not None
    assert client.logged is False


@pytest.mark.asyncio
async def test_client_media_resolvers(client):
    # Test slug / identifier resolution across all media types
    v = await client.get_video("ph618ed49fbee04", load_html=False, load_api=False)
    assert isinstance(v, Video)
    assert v.video_id == "ph618ed49fbee04"

    s = await client.get_short("6a346596ea4ff", load_html=False)
    assert isinstance(s, Short)
    assert s.url == "https://www.pornhub.com/shorties/6a346596ea4ff"

    g = await client.get_gif("55318721", load_html=False)
    assert isinstance(g, GIF)
    assert g.url == "https://www.pornhub.com/gif/55318721"

    a = await client.get_album("80426065", load_html=False)
    assert isinstance(a, Album)
    assert a.url == "https://www.pornhub.com/album/80426065"

    p = await client.get_playlist("119820351", load_html=False)
    assert isinstance(p, Playlist)
    assert p.url == "https://www.pornhub.com/playlist/119820351"

    c = await client.get_channel("brazzers", load_html=False)
    assert isinstance(c, Channel)
    assert c.url == "https://www.pornhub.com/channels/brazzers"

    m = await client.get_model("mia-maripolla", load_html=False)
    assert isinstance(m, Model)
    assert m.url == "https://www.pornhub.com/model/mia-maripolla"

    ps = await client.get_pornstar("danny-d", load_html=False)
    assert isinstance(ps, Pornstar)
    assert ps.url == "https://www.pornhub.com/pornstar/danny-d"

    u = await client.get_user("chappy1918", load_html=False)
    assert isinstance(u, User)
    assert u.url == "https://www.pornhub.com/users/chappy1918"


def test_client_unauthenticated_guards(client):
    with pytest.raises(LoginFailed):
        client.get_history()

    with pytest.raises(LoginFailed):
        client.get_favorites()

    with pytest.raises(LoginFailed):
        client.get_feed()

    with pytest.raises(LoginFailed):
        client.get_subscriptions()
