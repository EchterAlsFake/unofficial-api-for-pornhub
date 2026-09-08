"""
Copyright (C) 2026 Johannes Habel

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
from __future__ import annotations

import os
import re
import json
import copy
import chompjs
import logging
import asyncio
import argparse

from base_api.modules.logger import configure_app_logging

from contextlib import aclosing
from dataclasses import dataclass
from typing import AsyncGenerator, Any, ClassVar, Literal, TypeVar
from selectolax.lexbor import LexborHTMLParser

from base_api.modules.static_functions import strip_title
from base_api.modules.type_hints import DownloadReport
from base_api.modules.config import IteratorConfig
from base_api import (
    BaseCore,
    BaseMedia,
    DownloadConfigHLS,
    DownloadConfigRAW,
    ErrorMode,
    Helper,
    ScrapeResult,
    media_field,
    ScrapeStream,
    make_iterator_config,
    scrape_stream as _scrape_stream,
    str_to_bool,
)
from base_api.modules.errors import (
    DownloadCancelled,
    BotProtectionDetected,
    HTTPStatusError,
    InvalidProxy,
    NetworkRequestError,
    UnknownError,
)

from pornhub_api.modules.errors import (
    NetworkError,
    NotFound,
    ProxyError,
    LoginFailed,
    GifPendingReview,
    BotDetection,
    UnknownNetworkError,
    DownloadFailed,
    VideoDisabled,
    ClientAlreadyLogged,
)
from pornhub_api.modules.consts import (
    extractor_model_videos,
    extractor_videos,
    extractor_gifs,
    extractor_playlist,
    extractor_users,
    HOST,
    extractor_model_uploads,
    REGEX_VIDEO_FLASHVARS,
    REGEX_TOKEN,
    HEADERS,
    get_m3u8_urls,
    COOKIES,
    LOGIN_PAYLOAD,
)


logger = logging.getLogger("PornHub API")
logger.addHandler(logging.NullHandler())

MediaT = TypeVar("MediaT", bound=BaseMedia)


def _requested_sources(*, html: bool = False, api: bool = False) -> tuple[str, ...]:
    return tuple(source for source, enabled in (("api", api), ("html", html)) if enabled)


def build_m3u8_master(media_definitions: list[dict] | None) -> str:
    lines = ['#EXTM3U']
    for (width, height), uri in get_m3u8_urls(media_definitions).items():
        lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION={width}x{height}')
        lines.append(uri)
    return '\n'.join(lines)


async def _download_hls(media: BaseMedia, configuration: DownloadConfigHLS) -> bool | DownloadReport:
    try:
        await media.load_fields("title", "m3u8_base_url")
        logger.info(f"Downloading {type(media).__name__} {media.title} to {configuration.path}")
        config = copy.deepcopy(configuration)
        config.m3u8_base_url = media.m3u8_base_url
        if not config.no_title:
            config.path = os.path.join(config.path, f"{strip_title(media.title)}.mp4")

        return await media.core.download(configuration=config)
    except DownloadCancelled:
        raise
    except Exception as e:
        logger.exception("Download failed for %s: %s", media.url, e)
        raise DownloadFailed(f"Download failed for {media.url}: {e}") from e


async def get_html_content(core: BaseCore, url: str) -> str:
    logger.debug(f"Fetching HTML content for {url}")
    try:
        content = await core.fetch_text(url)
        logger.debug(f"Successfully fetched HTML from {url} ({len(content)} bytes)")
        return content
    except HTTPStatusError as e:
        logger.exception("Request failed for %s: %s", url, e)
        if e.status_code == 404:
            raise NotFound(f"Server returned 404 for: {url}") from e
        raise NetworkError(f"Request failed for {url}: {e}") from e
    except NetworkRequestError as e:
        logger.exception("Request failed for %s: %s", url, e)
        raise NetworkError(f"Request failed for {url}: {e}") from e
    except InvalidProxy as e:
        logger.exception("Request failed for %s: %s", url, e)
        raise ProxyError(f"Request failed for {url}: {e}") from e
    except BotProtectionDetected as e:
        logger.exception("Request failed for %s: %s", url, e)
        raise BotDetection(f"Request failed for {url}: {e}") from e
    except UnknownError as e:
        logger.exception("Request failed for %s: %s", url, e)
        raise UnknownNetworkError(f"Request failed for {url}: {e}") from e

    except Exception:
        logger.exception("Failed to fetch or decode response for %s", url)
        raise


@dataclass(kw_only=True, slots=True)
class UserHelper(BaseMedia):
    url: str
    core: BaseCore
    info: dict | None = media_field("html")
    bio: str | None = media_field("html")
    about: str | None = media_field("html")
    name: str | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}

    async def _load_html(self) -> dict[str, object]:
        logger.debug(f"Fetching HTML for UserHelper at {self.url}")
        html_content = await get_html_content(core=self.core, url=self.url)
        return await asyncio.to_thread(self._extract_html, html_content)

    @staticmethod
    def _extract_html(html_content: str) -> dict:
        logger.debug("Extracting info from User HTML...")
        lexbor = LexborHTMLParser(html_content)

        bio_node = lexbor.css_first("div.content.js-headerContent.js-highestChild div[itemprop]")
        bio = bio_node.text(strip=True) if bio_node else None

        about = None
        about_divs = lexbor.css("section.aboutMeSection.sectionDimensions div")
        if len(about_divs) > 1:
            about = about_divs[1].text(strip=True)
        elif (p := lexbor.css_first("p.aboutMeText")):
            about = p.text(strip=True)

        info = {}
        container = lexbor.css_first(
            "div.content-columns.inline.js-highestChild.js-headerContent, "
            "div.content-columns.js-highestChild.columns-2"
        )
        if container:
            for piece in container.css("div.infoPiece"):
                spans = piece.css("span")
                if len(spans) >= 2:
                    info[spans[0].text(strip=True)] = spans[1].text(strip=True)

        name_node = lexbor.css_first("div.name h1, div.profileUserName a")
        name = name_node.text(strip=True) if name_node else None

        return {
            "bio": bio,
            "about": about,
            "info": info,
            "name": name,
        }

    def get_videos(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        page_urls = [f"{self.url.rstrip('/')}/videos?page={page}" for page in range(1, pages + 1)]
        return _scrape_stream(
            core=self.core, constructor=Video, target_page_urls=page_urls,
            item_extractor=extractor_model_videos, iterator_config=iterator_config,
        )


@dataclass(kw_only=True, slots=True)
class Pornstar(UserHelper):
    def get_uploads(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        page_urls = [f"{self.url.rstrip('/')}/videos/upload?page={page}" for page in range(1, pages + 1)]
        return _scrape_stream(
            core=self.core, constructor=Video, target_page_urls=page_urls,
            item_extractor=extractor_model_uploads, iterator_config=iterator_config,
        )

    def get_gifs(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[GIF], None]:
        page_urls = [f"{self.url.rstrip('/')}/gifs/video?page={page}" for page in range(1, pages + 1)]
        return _scrape_stream(
            core=self.core, constructor=GIF, target_page_urls=page_urls,
            item_extractor=extractor_gifs, iterator_config=iterator_config,
        )


@dataclass(kw_only=True, slots=True)
class Model(UserHelper):
    pass


@dataclass(kw_only=True, slots=True)
class User(UserHelper):
    pass


@dataclass(kw_only=True, slots=True)
class Album(BaseMedia):
    url: str
    core: BaseCore
    rating_percentage: str | None = media_field("html")
    views: str | None = media_field("html")
    publish_date: str | None = media_field("html")
    tags: dict[str, str] | None = media_field("html")
    votes: str | None = media_field("html")
    author_link: str | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}

    async def _load_html(self) -> dict[str, object]:
        logger.debug(f"Fetching HTML for Album at {self.url}")
        html_content = await get_html_content(core=self.core, url=self.url)
        return await asyncio.to_thread(self._extract_html, html_content)

    @staticmethod
    def _extract_html(html_content: str) -> dict:
        logger.debug("Extracting info from Album HTML...")
        lexbor = LexborHTMLParser(html_content)

        rating_el = lexbor.css_first("div#ratingAlbumInfo span")
        votes_el = lexbor.css_first("div#ratingAlbumInfo > div")
        views_el = lexbor.css_first("div#viewsPhotAlbumCounter")
        time_block = lexbor.css_first("div#timeBlockContent")
        time_divs = time_block.css("div") if time_block else []
        publish_date = time_divs[4].text(strip=True) if len(time_divs) > 4 else None
        user_link_el = lexbor.css_first("span.usernameBadgesWrapper > a")
        author_link = f"https://www.pornhub.com{user_link_el.attributes.get('href')}" if user_link_el else None

        tag_container = lexbor.css_first("div.photoBoxContContainer")
        tags = {
            a.text(strip=True): f"https://www.pornhub.com{a.attributes.get('href')}"
            for a in tag_container.css("div.tagContainer")
        } if tag_container else {}

        return {
            "rating_percentage": rating_el.text(strip=True) if rating_el else None,
            "views": views_el.text(strip=True) if views_el else None,
            "publish_date": publish_date,
            "tags": tags,
            "votes": votes_el.text(strip=True) if votes_el else None,
            "author_link": author_link,
        }

    @property
    async def author(self, load_html: bool = True) -> Pornstar:
        author_link = await self.get_field("author_link")
        star = Pornstar(url=author_link, core=self.core)
        if load_html:
            await star.load_sources("html")
        return star

    @staticmethod
    def _parse_photos(html_content: str) -> list[dict[str, Any]]:
        tags = []
        lexbor = LexborHTMLParser(html_content)
        main_ul = lexbor.css_first("ul.photosAlbumsListing.albumViews.preloadImage")
        if not main_ul:
            return tags
        for li_tag in main_ul.css("div.js_lazy_bkg.photoAlbumListBlock"):
            a = li_tag.css_first("a")
            link = f"https://www.pornhub.com{a.attributes.get('href')}" if a else ""
            spans = li_tag.css("span")
            tags.append({
                "url": link,
                "download_url": li_tag.attributes.get("data-bkg"),
                "rating": spans[0].text(strip=True) if len(spans) > 0 else "",
                "views": spans[1].text(strip=True) if len(spans) > 1 else "",
            })
        return tags

    async def get_photos(self, pages: int = 1) -> AsyncGenerator[dict[str, Any], None]:
        logger.info(f"Fetching photos for Album at {self.url} (pages: {pages})")
        page_urls = [f"{self.url.rstrip('/')}?page={page}" for page in range(1, pages + 1)]
        html_contents = await asyncio.gather(*(get_html_content(core=self.core, url=url) for url in page_urls))
        for html in html_contents:
            for photo_data in self._parse_photos(html):
                yield photo_data

    async def download_photo(self, url: str, path: str) -> bool:
        logger.info(f"Downloading photo {url} to {path}")
        try:
            return await self.core.legacy_download(url=url, configuration=DownloadConfigRAW(path=path, quality="best"))
        except DownloadCancelled:
            raise
        except Exception as e:
            logger.exception("Photo download failed for %s (album=%s, output=%s)", url, self.url, path)
            raise DownloadFailed(f"Photo download failed for {url} (album={self.url}): {e}") from e


@dataclass(kw_only=True, slots=True)
class Short(BaseMedia):
    url: str
    core: BaseCore
    title: str | None = media_field("html")
    video_id: str | None = media_field("html")
    author_link: str | None = media_field("html")
    video_key: str | None = media_field("html")
    favorites: str | None = media_field("html")
    likes: str | None = media_field("html")
    dislikes: str | None = media_field("html")
    is_hd: bool | None = media_field("html")
    embed_url: str | None = media_field("html")
    thumbnail: str | None = media_field("html")
    media_definitions: dict | None = media_field("html")
    comment_count: str | None = media_field("html")
    avatar: str | None = media_field("html")
    author_name: str | None = media_field("html")
    video_url: str | None = media_field("html")
    m3u8_base_url: str | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}

    async def _load_html(self) -> dict[str, object]:
        logger.debug(f"Fetching HTML for Short at {self.url}")
        html_content = await get_html_content(core=self.core, url=self.url)
        return await asyncio.to_thread(self._extract_html, html_content)

    @staticmethod
    def _extract_html(html_content: str) -> dict:
        logger.debug("Extracting metadata from Short HTML...")
        parser = LexborHTMLParser(html_content)
        metadata = {}

        for script in parser.css("script"):
            text = script.text()
            if "JSON_SHORTIES" in text:
                match = re.search(r'JSON_SHORTIES = insertAfterNthPosition\((.*?), prerollObject', text, re.DOTALL)
                if match:
                    parsed = chompjs.parse_js_object(match.group(1))
                    if parsed:
                        metadata = parsed[0]
                break

        media_definitions = metadata.get("mediaDefinitions")
        return {
            "title": metadata.get("videoTitle"),
            "video_id": metadata.get("videoId"),
            "video_key": metadata.get("vkey"),
            "favorites": metadata.get("favoriteInfo"),
            "likes": metadata.get("likeNumber"),
            "dislikes": metadata.get("dislikeNumber"),
            "is_hd": metadata.get("isHD") == "True",
            "embed_url": metadata.get("embedUrl"),
            "thumbnail": metadata.get("imageUrl"),
            "media_definitions": media_definitions,
            "comment_count": metadata.get("commentCount"),
            "avatar": metadata.get("avatar"),
            "author_name": metadata.get("name"),
            "author_link": metadata.get("profileUrl"),
            "video_url": metadata.get("linkUrl"),
            "m3u8_base_url": build_m3u8_master(media_definitions),
        }

    async def get_author(self, load_html: bool = True) -> Pornstar:
        author_link = await self.get_field("author_link")
        star = Pornstar(url=author_link, core=self.core)
        if load_html:
            await star.load_sources("html")
        return star

    async def get_video(self, load_html: bool = False, load_api: bool = True) -> Video:
        video_url = await self.get_field("video_url")
        video = Video(url=video_url, core=self.core)
        await video.load_sources(*_requested_sources(html=load_html, api=load_api))
        return video

    async def download(self, configuration: DownloadConfigHLS) -> bool | DownloadReport:
        return await _download_hls(self, configuration)


@dataclass(kw_only=True, slots=True)
class GIF(BaseMedia):
    url: str
    core: BaseCore
    title: str | None = media_field("html")
    vote_count: str | None = media_field("html")
    vote_percentage: str | None = media_field("html")
    views: str | None = media_field("html")
    publish_date: str | None = media_field("html")
    thumbnail: str | None = media_field("html")
    content_url: str | None = media_field("html")
    source_video_url: str | None = media_field("html")
    tags: dict[str, str] | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}

    async def _load_html(self) -> dict[str, object]:
        logger.debug(f"Fetching HTML for GIF at {self.url}")
        html_content = await get_html_content(url=self.url, core=self.core)
        if "GIF is unavailable pending review." in html_content:
            raise GifPendingReview("The GIF is still pending a review and can't be downloaded yet...")
        if "This video has been disabled" in html_content:
            raise VideoDisabled("The Video has been disabled, I can not fetch any data from it.")
        return await asyncio.to_thread(self._extract_html, html_content)

    @staticmethod
    def _extract_html(html_content: str) -> dict:
        logger.debug("Extracting info from GIF HTML...")
        lexbor = LexborHTMLParser(html_content)
        script_node = lexbor.css_first('script[type="application/ld+json"]')
        script = json.loads(script_node.text()) if script_node else {}

        title = script.get("name", "")
        if (h1 := lexbor.css_first("div.gifTitle h1, h1")):
            title = h1.text(strip=True)

        vote_count_el = lexbor.css_first("div.voteCount span")
        vote_percentage_el = lexbor.css_first("div.votePercentage span")
        views_el = lexbor.css_first("li.float-right.gifViews")
        source_link_el = lexbor.css_first("div.bottomMargin a")

        tags = {}
        tag_list = lexbor.css_first("ul.tagList.clearfix")
        if tag_list:
            for a in tag_list.css("li a"):
                tags[a.text(strip=True)] = a.attributes.get("href")

        return {
            "title": title,
            "vote_count": vote_count_el.text(strip=True) if vote_count_el else None,
            "vote_percentage": vote_percentage_el.text(strip=True) if vote_percentage_el else None,
            "views": views_el.text(strip=True) if views_el else None,
            "publish_date": script.get("uploadDate"),
            "thumbnail": script.get("thumbnailUrl"),
            "source_video_url": f"https://www.pornhub.com{source_link_el.attributes.get('href')}" if source_link_el else None,
            "content_url": script.get("contentUrl"),
            "tags": tags,
        }

    async def download(self, configuration: DownloadConfigRAW) -> bool:
        try:
            await self.load_fields("title", "content_url")
            logger.info(f"Downloading GIF {self.title} to {configuration.path}")
            config = copy.deepcopy(configuration)
            if not config.no_title:
                config.path = os.path.join(config.path, f"{strip_title(self.title)}.mp4")

            return await self.core.legacy_download(url=self.content_url, configuration=config)
        except DownloadCancelled:
            raise
        except Exception as e:
            logger.exception("Download failed for %s: %s", self.url, e)
            raise DownloadFailed(f"Download failed for {self.url}: {e}") from e


@dataclass(kw_only=True, slots=True)
class Channel(BaseMedia):
    url: str
    core: BaseCore
    name: str | None = media_field("html")
    is_award_winner: bool | None = media_field("html")
    video_views: str | None = media_field("html")
    subscribers: str | None = media_field("html")
    total_videos: str | None = media_field("html")
    rank: str | None = media_field("html")
    description: str | None = media_field("html")
    join_date: str | None = media_field("html")
    website: str | None = media_field("html")
    user_link: str | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}

    async def _load_html(self) -> dict[str, object]:
        logger.debug(f"Fetching HTML for Channel at {self.url}")
        html_content = await get_html_content(url=self.url, core=self.core)
        return await asyncio.to_thread(self._extract_html, html_content)

    @staticmethod
    def _extract_html(html_content: str) -> dict:
        logger.debug("Extracting info from Channel HTML...")
        lexbor = LexborHTMLParser(html_content)

        name_el = lexbor.css_first("div.title.floatLeft > h1")
        name = name_el.text(strip=True) if name_el else None
        is_award_winner = bool(lexbor.css_first("i.trophyChannel.bg-trophy-channel.tooltipTrig"))

        meta = lexbor.css("div.info.floatRight")
        video_views = meta[0].text(strip=True) if len(meta) > 0 else None
        subscribers = meta[1].text(strip=True) if len(meta) > 1 else None
        total_videos = meta[2].text(strip=True) if len(meta) > 2 else None
        rank = meta[3].text(strip=True).replace("RANK", "") if len(meta) > 3 else None

        meta_2 = lexbor.css("p.joined")
        description = meta_2[0].text(strip=True) if len(meta_2) > 0 else None
        join_date = meta[1].text(strip=True) if len(meta) > 1 else None
        website = meta[2].text(strip=True) if len(meta) > 2 else None

        user_link = None
        if len(meta_2) > 3 and (a := meta_2[3].css_first("a")):
            user_link = f"https://www.pornhub.com{a.attributes.get('href')}"

        return {
            "name": name,
            "is_award_winner": is_award_winner,
            "video_views": video_views,
            "subscribers": subscribers,
            "total_videos": total_videos,
            "rank": rank,
            "description": description,
            "join_date": join_date,
            "website": website,
            "user_link": user_link,
        }

    def get_videos(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        page_urls = [f"{self.url.rstrip('/')}/videos?page={page}" for page in range(1, pages + 1)]
        return _scrape_stream(
            core=self.core, constructor=Video, target_page_urls=page_urls,
            item_extractor=extractor_videos, iterator_config=iterator_config,
        )

    async def get_user(self, load_html: bool = True) -> User:
        user_link = await self.get_field("user_link")
        user = User(core=self.core, url=user_link)
        if load_html:
            await user.load_sources("html")
        return user


@dataclass(kw_only=True, slots=True)
class Playlist(BaseMedia):
    url: str
    core: BaseCore
    token: str | None = media_field("html")
    playlist_id: str | None = media_field("html")
    title: str | None = media_field("html")
    views: str | None = media_field("html")
    rating_percent: str | None = media_field("html")
    likes: str | None = media_field("html")
    dislikes: str | None = media_field("html")
    author_link: str | None = media_field("html")
    video_count: str | None = media_field("html")
    description: str | None = media_field("html")
    unavailable_videos: int | None = media_field("html")
    tags: dict[str, str] | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}

    async def _load_html(self) -> dict[str, object]:
        logger.debug(f"Fetching HTML for Playlist at {self.url}")
        html_content = await get_html_content(url=self.url, core=self.core)
        return await asyncio.to_thread(self._extract_html, html_content)

    def _extract_html(self, html_content: str) -> dict:
        logger.debug("Extracting info from Playlist HTML...")
        lexbor = LexborHTMLParser(html_content)

        token_match = REGEX_TOKEN.search(html_content)
        token = token_match.group(1) if token_match else None

        id_match = re.search(r'(\d+)/?$', self.url)
        playlist_id = id_match.group(1) if id_match else None

        title_el = lexbor.css_first("h1.playlistTitle")
        views_el = lexbor.css_first("div.views > span")
        votes_spans = lexbor.css("div.votes-count-container span")

        user_a = lexbor.css_first("div.usernameWrap.clearfix > a")
        author_link = f"https://www.pornhub.com{user_a.attributes.get('href')}" if user_a else None

        about_tab = lexbor.css_first("div#js-aboutPlaylistTabView > div")
        count_match = re.search(r'(\d+)\s*', about_tab.text(strip=True)) if about_tab else None
        video_count = count_match.group(1) if count_match else None

        desc_el = lexbor.css_first("p.description.js-playlistDescription > span")
        description = desc_el.text(strip=True) if desc_el else None

        unavail_match = re.search(r'unavailable videos that are hidden:\s+(\d+)', html_content)
        unavailable_videos_count = int(unavail_match.group(1)) if unavail_match else 0

        tags = {}
        tag_container = lexbor.css_first("div.tagsWrap.js-tagsWrap")
        if tag_container:
            for tag in tag_container.css("a"):
                name = tag.attributes.get("data-label")
                if name:
                    tags[str(name)] = f"https://www.pornhub.com{tag.attributes.get('href')}"

        return {
            "token": token,
            "playlist_id": playlist_id,
            "title": title_el.text(strip=True) if title_el else None,
            "views": views_el.text(strip=True) if views_el else None,
            "rating_percent": votes_spans[0].text(strip=True) if len(votes_spans) > 0 else None,
            "likes": votes_spans[1].text(strip=True) if len(votes_spans) > 1 else None,
            "dislikes": votes_spans[2].text(strip=True) if len(votes_spans) > 2 else None,
            "author_link": author_link,
            "video_count": video_count,
            "description": description,
            "unavailable_videos": unavailable_videos_count,
            "tags": tags,
        }

    async def get_videos(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        await self.load_fields("playlist_id", "token")
        page_urls = [
            f'https://www.pornhub.com/playlist/viewChunked?id={self.playlist_id}&token={self.token}&page={page}'
            for page in range(1, pages + 1)
        ]
        async for result in _scrape_stream(
            core=self.core, constructor=Video, target_page_urls=page_urls,
            item_extractor=extractor_playlist, iterator_config=iterator_config,
        ):
            yield result

    async def get_author(self, load_html: bool = True) -> User:
        author_link = await self.get_field("author_link")
        user = User(url=author_link, core=self.core)
        if load_html:
            await user.load_sources("html")
        return user


@dataclass(kw_only=True, slots=True)
class Video(BaseMedia):
    url: str
    core: BaseCore
    video_id: str | None = None

    # Flashvars / Configuration fields
    is_vr: bool | None = media_field("html")
    is_video_unavailable: bool | None = media_field("html")
    is_hd: bool | None = media_field("html")
    duration: int | None = media_field("api", "html")
    title: str | None = media_field("api", "html")
    thumbnail: str | None = media_field("api", "html")
    available_qualities: list[int] | None = media_field("html")
    is_vertical: bool | None = media_field("html")
    is_video_unavailable_in_your_country: bool | None = media_field("html")

    # HTML Scraped fields
    views: str | None = media_field("api", "html")
    publish_date: str | None = media_field("api", "html")
    likes: int | str | None = media_field("api", "html")

    # Playlist URL
    m3u8_base_url: str | None = media_field("html")

    # Categorization maps
    categories: list[str] | None = media_field("api", "html")
    tags: list[str] | None = media_field("api", "html")
    rating_percent: str | float | None = media_field("api")

    # Author details
    author_thumbnail: str | None = media_field("html")
    author_link: str | None = media_field("html")
    author_information: dict[str, Any] | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {
        "api": "_load_api",
        "html": "_load_html",
    }

    def __post_init__(self) -> None:
        if self.video_id is None:
            match = re.search(r"viewkey=([^&#]+)", self.url)
            self.video_id = match.group(1) if match else None

    async def _load_html(self) -> dict[str, object]:
        logger.debug(f"Fetching HTML for Video at {self.url}")
        html_content = await get_html_content(core=self.core, url=self.url)
        return await asyncio.to_thread(self._extract_html, html_content)

    async def _load_api(self) -> dict[str, object]:
        logger.debug(f"Fetching API data for Video {self.video_id}")
        stuff = await get_html_content(url=f"https://www.pornhub.com/webmasters/video_by_id?id={self.video_id}", core=self.core)
        return await asyncio.to_thread(self._extract_api, stuff)

    @staticmethod
    def _extract_html(html_content: str) -> dict:
        logger.debug("Extracting info from Video HTML...")
        parser = LexborHTMLParser(html_content)
        match = REGEX_VIDEO_FLASHVARS.search(html_content)
        flashvars = json.loads(match.group(1), strict=False) if match else {}

        qualities = flashvars.get("defaultQuality", [])
        available_qualities = sorted(qualities) if isinstance(qualities, list) else []

        views_el = parser.css_first("div.video-actions-menu.ctasActionMenu div.views > span")
        date_el = parser.css_first("div.video-actions-menu.ctasActionMenu div.videoInfo")
        likes_el = parser.css_first("span.votesUp")
        author_thumb_el = parser.css_first("div.userAvatar img")

        categories_el = parser.css_first("div.categoriesWrapper")
        categories = [a.text(strip=True) for a in categories_el.css("a.gtm-event-video-underplayer.item")] if categories_el else []

        tags_el = parser.css_first("div.tagsWrapper")
        tags = [a.text(strip=True) for a in tags_el.css("a.video_underplayer")] if tags_el else []

        avatar_a = parser.css_first("div.userAvatar > a")
        author_link = f"https://www.pornhub.com{avatar_a.attributes.get('href')}" if avatar_a else None

        user_info = parser.css_first("div.userInfo")
        author_name = None
        video_amount = None
        subscriber_amount = None
        if user_info:
            a_tag = user_info.css_first("span.usernameBadgesWrapper a, div.usernameWrap a")
            if a_tag:
                author_name = a_tag.text(strip=True)
            spans = user_info.css("span")
            if len(spans) > 1:
                video_amount = spans[1].text(strip=True)
            if len(spans) > 2:
                subscriber_amount = spans[2].text(strip=True)

        author_information = {
            "name": author_name,
            "link": author_link,
            "video_amount": video_amount,
            "subscriber_amount": subscriber_amount,
        }

        return {
            "is_vr": bool(flashvars.get("isVR")),
            "is_video_unavailable": flashvars.get("video_unavailable") != "false",
            "is_hd": flashvars.get("isHD") != "false",
            "duration": int(flashvars["video_duration"]) if "video_duration" in flashvars else None,
            "title": flashvars.get("video_title"),
            "thumbnail": flashvars.get("image_url"),
            "available_qualities": available_qualities,
            "is_vertical": flashvars.get("isVertical") == "true",
            "is_video_unavailable_in_your_country": flashvars.get("video_unavailable_country") == "true",
            "views": views_el.text(strip=True) if views_el else None,
            "publish_date": date_el.text(strip=True) if date_el else None,
            "likes": likes_el.text(strip=True) if likes_el else None,
            "author_thumbnail": author_thumb_el.attributes.get("src") if author_thumb_el else None,
            "m3u8_base_url": build_m3u8_master(flashvars.get("mediaDefinitions")),
            "categories": categories,
            "tags": tags,
            "author_link": author_link,
            "author_information": author_information,
        }

    @staticmethod
    def _extract_api(json_data: str) -> dict:
        logger.debug("Extracting API data for Video...")
        raw = json.loads(json_data, strict=False)
        video = raw.get("video", {})

        dur = video.get("duration")
        if isinstance(dur, str) and ":" in dur:
            parts = [int(p) for p in dur.split(":")]
            duration = sum(p * 60**i for i, p in enumerate(reversed(parts)))
        else:
            duration = dur

        categories = [t["category"] for t in video.get("categories") or [] if isinstance(t, dict) and "category" in t]
        tags = [t["tag_name"] for t in video.get("tags") or [] if isinstance(t, dict) and "tag_name" in t]

        return {
            "thumbnail": video.get("default_thumb") or video.get("thumb"),
            "duration": duration,
            "title": video.get("title"),
            "views": video.get("views", "0"),
            "publish_date": video.get("publish_date", ""),
            "rating_percent": video.get("rating"),
            "likes": video.get("ratings", "0"),
            "categories": categories,
            "tags": tags,
        }

    @property
    async def author(self, load_html: bool = True) -> Pornstar | Channel | Model | None:
        author_link = await self.get_field("author_link")
        if not author_link:
            return None
        if "pornstar" in author_link:
            author = Pornstar(core=self.core, url=author_link)
        elif "model" in author_link:
            author = Model(core=self.core, url=author_link)
        elif "channel" in author_link:
            author = Channel(core=self.core, url=author_link)
        else:
            return None

        if load_html:
            await author.load_sources("html")
        return author

    async def download(self, configuration: DownloadConfigHLS) -> bool | DownloadReport:
        return await _download_hls(self, configuration)


class Account:
    def __init__(self, client: Client):
        self.client = client
        self.name: str | None = None
        self.avatar: str | None = None
        self.is_premium: bool = False
        self.user: User | None = None

    def connect(self, data: dict):
        self.name = data.get('username')
        self.avatar = data.get("avatar_url")
        self.is_premium = data.get('premium_redirect_cookie') != '0'
        logger.info(f"Account connected: {self.name} (Premium: {self.is_premium})")

        if self.name:
            url = f"https://www.pornhub.com/users/{self.name}"
            self.user = User(url=url, core=self.client.core)

    def get_recommended(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        return self.client.get_recommended(pages=pages, iterator_config=iterator_config)

    def get_history(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        return self.client.get_history(pages=pages, iterator_config=iterator_config)

    def get_favorites(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        return self.client.get_favorites(pages=pages, iterator_config=iterator_config)

    def get_feed(
        self,
        section: str = "videos",
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        return self.client.get_feed(section=section, pages=pages, iterator_config=iterator_config)

    def get_subscriptions(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[User], None]:
        return self.client.get_subscriptions(pages=pages, iterator_config=iterator_config)

    def __repr__(self) -> str:
        status = 'logged-out' if self.name is None else f'name={self.name}'
        return f'Account({status})'


class Client:
    def __init__(self, core: BaseCore | None = None, email: str | None = None, password: str | None = None):
        self.core = core if core is not None else BaseCore()
        self.core.initialize_session()
        self.core.session.headers.update(HEADERS)
        self.core.session.cookies.update(COOKIES)

        self.credentials = {"email": email, "password": password}
        self.logged = False
        self.account = Account(self)

    @classmethod
    async def create(cls, core: BaseCore | None = None, email: str | None = None, password: str | None = None, login: bool = False) -> Client:
        client = cls(core=core, email=email, password=password)
        if login and email and password:
            await client.login()
        return client

    async def login(self, force: bool = False, throw: bool = True) -> bool:
        logger.info("Attempting login")

        if not force and self.logged:
            if throw:
                raise ClientAlreadyLogged()
            return True

        if not self.credentials["email"] or not self.credentials["password"]:
            if throw:
                raise LoginFailed("Email and password are required")
            return False

        page_content = await get_html_content(url=HOST, core=self.core)
        match = REGEX_TOKEN.search(page_content)
        if not match:
            if throw:
                raise LoginFailed("Could not find login token")
            return False

        token = match.group(1)
        payload = LOGIN_PAYLOAD | self.credentials | {"token": token}
        url = f"{HOST}front/authenticate"
        try:
            response = await self.core.request(url, method="POST", data=payload)
            data = response.json()
        except Exception as e:
            logger.exception("Login request failed for %s", url)
            if throw:
                raise LoginFailed(f"Login request failed for {url}: {e}") from e
            return False

        if not int(data.get("success", 0)):
            if throw:
                raise LoginFailed(data.get("message", "Unknown error"))
            return False

        self.account.connect(data)
        self.logged = True
        return True

    async def fix_recommendations(self) -> bool:
        if not self.logged:
            return False

        logger.info("Fixing account recommendations")
        page_content = await get_html_content(url=HOST, core=self.core)
        match = REGEX_TOKEN.search(page_content)
        if not match:
            return False

        params = {'token': match.group(1), 'cookie_selection': 3, 'site_id': 1}
        try:
            response = await self.core.request(f"{HOST}user/log_user_cookie_consent", params=params)
            return response.json().get("success", False)
        except Exception:
            logger.exception("Failed to update recommendations via %suser/log_user_cookie_consent", HOST)
            return False

    async def get_recommended(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        await self.fix_recommendations()
        page_urls = [f"{HOST}recommended?page={page}" for page in range(1, pages + 1)]
        async for result in _scrape_stream(
            core=self.core, constructor=Video, target_page_urls=page_urls,
            item_extractor=extractor_videos, iterator_config=iterator_config,
        ):
            yield result

    def get_history(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        if not self.logged:
            raise LoginFailed("Must be logged in to access history")
        page_urls = [f"{HOST}users/{self.account.name}/videos/recent?page={page}" for page in range(1, pages + 1)]
        return _scrape_stream(
            core=self.core, constructor=Video, target_page_urls=page_urls,
            item_extractor=extractor_videos, iterator_config=iterator_config,
        )

    def get_favorites(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        if not self.logged:
            raise LoginFailed("Must be logged in to access favorites")
        page_urls = [f"{HOST}users/{self.account.name}/videos/favorites?page={page}" for page in range(1, pages + 1)]
        return _scrape_stream(
            core=self.core, constructor=Video, target_page_urls=page_urls,
            item_extractor=extractor_videos, iterator_config=iterator_config,
        )

    def get_feed(
        self,
        section: str = "videos",
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        if not self.logged:
            raise LoginFailed("Must be logged in to access feed")
        page_urls = [f"{HOST}feeds?section={section}&page={page}" for page in range(1, pages + 1)]
        return _scrape_stream(
            core=self.core, constructor=Video, target_page_urls=page_urls,
            item_extractor=extractor_videos, iterator_config=iterator_config,
        )

    def get_subscriptions(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[User], None]:
        if not self.logged:
            raise LoginFailed("Must be logged in to access subscriptions")
        page_urls = [f"{HOST}users/{self.account.name}/subscriptions?page={page}" for page in range(1, pages + 1)]
        return _scrape_stream(
            core=self.core, constructor=User, target_page_urls=page_urls,
            item_extractor=extractor_users, iterator_config=iterator_config,
        )

    async def _get_media(self, cls: type[MediaT], url: str, load_html: bool = True) -> MediaT:
        media = cls(url=url, core=self.core)
        if load_html:
            await media.load_sources("html")
        return media

    async def get_video(self, url: str, load_html: bool = False, load_api: bool = True) -> Video:
        logger.debug(f"Client instantiating Video {url}")
        video = Video(url=url, core=self.core)
        await video.load_sources(*_requested_sources(html=load_html, api=load_api))
        return video

    async def get_pornstar(self, url: str, load_html: bool = True) -> Pornstar:
        return await self._get_media(Pornstar, url, load_html)

    async def get_gif(self, url: str, load_html: bool = True) -> GIF:
        return await self._get_media(GIF, url, load_html)

    async def get_album(self, url: str, load_html: bool = True) -> Album:
        return await self._get_media(Album, url, load_html)

    async def get_short(self, url: str, load_html: bool = True) -> Short:
        return await self._get_media(Short, url, load_html)

    async def get_model(self, url: str, load_html: bool = True) -> Model:
        return await self._get_media(Model, url, load_html)

    async def get_user(self, url: str, load_html: bool = True) -> User:
        return await self._get_media(User, url, load_html)

    async def get_playlist(self, url: str, load_html: bool = True) -> Playlist:
        return await self._get_media(Playlist, url, load_html)

    async def get_channel(self, url: str, load_html: bool = True) -> Channel:
        return await self._get_media(Channel, url, load_html)

    def search_gifs(
        self,
        query: str,
        category: Literal["gay", "transgender"] | None = None,
        search_filter: Literal["mr", "mv", "tr"] | None = None,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[GIF], None]:
        base_url = f"{HOST}{category + '/' if category else ''}gifs/search?search={query}"
        if search_filter:
            base_url += f"&o={search_filter}"
        page_urls = [f"{base_url}&page={page}" for page in range(1, pages + 1)]
        return _scrape_stream(
            core=self.core, constructor=GIF, target_page_urls=page_urls,
            item_extractor=extractor_gifs, iterator_config=iterator_config,
        )

    def search_videos(
        self,
        query: str,
        production_type: Literal["professional", "homemade"] | None = None,
        sort_by: Literal["mr", "mv", "tr"] | None = None,
        duration_min: Literal["10", "20", "30"] | None = None,
        duration_max: Literal["10", "20", "30"] | None = None,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        base_url = f"{HOST}video/search?search={query}"
        if production_type:
            base_url += f"&p={production_type}"
        if sort_by:
            base_url += f"&o={sort_by}"
        if duration_min:
            base_url += f"&duration_min={duration_min}"
        if duration_max:
            base_url += f"&duration_max={duration_max}"

        page_urls = [f"{base_url}&page={page}" for page in range(1, pages + 1)]
        return _scrape_stream(
            core=self.core, constructor=Video, target_page_urls=page_urls,
            item_extractor=extractor_videos, iterator_config=iterator_config,
        )

    def search_hubtraffic(
        self,
        query: str,
        category: str | None = None,
        sort_by: Literal["newest", "mostviewed", "rating"] | None = None,
        period: Literal["weekly", "monthly", "alltime"] | None = None,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        base_url = f"{HOST}webmasters/search?search={query}"
        if category:
            base_url += f"&category={category}"
        if sort_by:
            base_url += f"&ordering={sort_by}"
        if period:
            base_url += f"&period={period}"

        page_urls = [f"{base_url}&page={page}" for page in range(1, pages + 1)]
        if iterator_config is None:
            iterator_config = make_iterator_config(max_item_concurrency=20, max_page_concurrency=5)

        return _scrape_stream(
            core=self.core, constructor=Video, target_page_urls=page_urls,
            item_extractor=extractor_videos, iterator_config=iterator_config,
        )


def can_download(state: dict) -> bool:
    return state["limit"] is None or state["downloaded"] < state["limit"]


def _resolve_hls_config(media: Any, args: argparse.Namespace, no_title: bool) -> DownloadConfigHLS:
    if getattr(args, "id_as_title", False) and hasattr(media, "video_id"):
        return DownloadConfigHLS(quality=args.quality, path=os.path.join(args.output, f"{media.video_id}.mp4"), no_title=True)
    return DownloadConfigHLS(quality=args.quality, path=args.output, no_title=no_title)


async def _cli_download_video_generator(generator: AsyncGenerator, args: argparse.Namespace, no_title: bool, state: dict):
    async with aclosing(generator):
        async for result in generator:
            if not can_download(state):
                break
            if not result.succeeded:
                logger.error(
                    "Skipping failed scrape result for %s: %s", result.url, result.error,
                    exc_info=(type(result.error), result.error, result.error.__traceback__),
                )
                continue
            video = result.unwrap()
            await video.load_sources("html")
            await video.download(_resolve_hls_config(video, args, no_title))
            state["downloaded"] += 1


async def _cli_process_url(client: Client, url: str, args: argparse.Namespace, no_title: bool, state: dict):
    try:
        if "view_video.php" in url:
            if not can_download(state):
                return
            video = await client.get_video(url, load_html=True)
            await video.download(_resolve_hls_config(video, args, no_title))
            state["downloaded"] += 1

        elif "/short/" in url:
            if not can_download(state):
                return
            short = await client.get_short(url)
            await short.download(_resolve_hls_config(short, args, no_title))
            state["downloaded"] += 1

        elif "/gif/" in url:
            if not can_download(state):
                return
            gif = await client.get_gif(url)
            await gif.download(DownloadConfigRAW(quality=args.quality, path=args.output))
            state["downloaded"] += 1

        elif "/album/" in url:
            album = await client.get_album(url)
            async for photo in album.get_photos(pages=args.pages):
                if not can_download(state):
                    break
                await album.download_photo(photo["download_url"], path=args.output)
                state["downloaded"] += 1

        else:
            resolvers = {
                "/pornstar/": client.get_pornstar,
                "/model/": client.get_model,
                "/users/": client.get_user,
                "/channels/": client.get_channel,
                "/playlists/": client.get_playlist,
            }
            handler = next((fn for prefix, fn in resolvers.items() if prefix in url), None)
            if not handler:
                print(f"Unsupported or unrecognized URL format: {url}")
                return
            obj = await handler(url)
            await _cli_download_video_generator(obj.get_videos(pages=args.pages), args, no_title, state)

    except Exception as e:
        logger.exception("CLI failed while processing %s", url)
        print(f"Error processing {url}: {e}")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PornHub API Command Line Interface")
    parser.add_argument("--download", metavar="URL (str)", type=str, help="URL to download from")
    parser.add_argument("--quality", metavar="best,half,worst", type=str, default="best", help="The video quality (best,half,worst)")
    parser.add_argument("--file", metavar="Source to .txt file", type=str, help="(Optional) Specify a file with URLs (separated with new lines)")
    parser.add_argument("--output", metavar="Output directory", type=str, help="The output path (with filename or directory)", required=True)
    parser.add_argument("--no-title", metavar="True,False", type=str, nargs="?", const="True", default="False",
                        help="Whether to apply video title automatically to output path or not")
    parser.add_argument("--pages", metavar="Pages (int)", type=int, default=1, help="Number of pages to fetch for iterables (Default: 1)")
    parser.add_argument("--email", type=str, help="Account email for login", default=None)
    parser.add_argument("--password", type=str, help="Account password for login", default=None)
    parser.add_argument("--id-as-title", action="store_true", help="Use the video ID as the output title")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of videos to download")
    parser.add_argument("--liked", action="store_true", help="Download liked/favorite videos (requires login)")
    parser.add_argument("--recommended", action="store_true", help="Download recommended videos (requires login)")
    parser.add_argument("--watched", action="store_true", help="Download watched/history videos (requires login)")
    return parser


async def run_main(args_list: list[str] | None = None):
    parser = create_parser()
    args = parser.parse_args(args_list)
    no_title = str_to_bool(args.no_title) if isinstance(args.no_title, str) else bool(args.no_title)

    login = False
    client = Client(email=args.email, password=args.password)
    if args.email and args.password:
        login = True
        await client.login()

    urls = []
    if args.download:
        urls.append(args.download)

    if args.file:
        with open(args.file, "r") as file:
            urls.extend(file.read().splitlines())

    if not urls and not (login and (args.liked or args.recommended or args.watched)):
        parser.print_help()
        return

    state = {"downloaded": 0, "limit": args.limit}

    for url in urls:
        await _cli_process_url(client, url, args, no_title, state)
        if not can_download(state):
            break

    if login:
        if args.liked:
            await _cli_download_video_generator(client.get_favorites(pages=args.pages), args, no_title, state)
        if args.recommended:
            await _cli_download_video_generator(client.get_recommended(pages=args.pages), args, no_title, state)
        if args.watched:
            await _cli_download_video_generator(client.get_history(pages=args.pages), args, no_title, state)
    elif args.liked or args.recommended or args.watched:
        print("Warning: --liked, --recommended, and --watched require --email and --password to work. Skipping.")


def cli():
    try:
        asyncio.run(run_main())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")


def main():
    configure_app_logging(level=logging.INFO)
    cli()


if __name__ == "__main__":
    main()
