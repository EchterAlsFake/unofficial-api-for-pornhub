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
import chompjs
import logging
import asyncio
import argparse


from dataclasses import dataclass, fields
from curl_cffi import Response, AsyncSession
from selectolax.lexbor import LexborHTMLParser
from concurrent.futures import ProcessPoolExecutor
from typing import AsyncGenerator, Any, Literal
from base_api.modules.type_hints import DownloadReport
from base_api import BaseCore, Helper, DownloadConfigRAW, DownloadConfigHLS, ScrapeResult, BaseMedia
from base_api.modules.errors import InvalidProxy, UnknownError, NetworkRequestError, BotProtectionDetected, ResourceGone

from pornhub_api.modules.errors import (NetworkError, NotFound, ProxyError, LoginFailed, GifPendingReview, BotDetection,
                                        UnknownNetworkError, DownloadFailed, VideoDisabled, ClientAlreadyLogged)
from pornhub_api.modules.consts import (extractor_model, extractor_videos, extractor_gifs, extractor_videos_playlist,
                                        extractor_users, extractor_hubtraffic, extractor_videos_from_playlist_page, HOST,
                                        REGEX_VIDEO_FLASHVARS, REGEX_TOKEN, HEADERS, get_m3u8_urls, COOKIES, LOGIN_PAYLOAD)
from pornhub_api.modules.type_hints import on_error_hint

async def on_error(url: str, error: Exception, attempt: int) -> bool:
    print(f"URL: {url}, ERROR: {error}, Attempt: {attempt}")

    if isinstance(error, ResourceGone):
        return False

    return True

logger = logging.getLogger(__name__)

async def get_html_content(core: BaseCore, url: str) -> str | None | dict:
    # What should I do here?
    logger.debug(f"Fetching HTML content for {url}")
    try:
        content = await core.fetch(url)
        if isinstance(content, str):
            logger.debug(f"Successfully fetched HTML from {url} ({len(content)} bytes)")
            return content

        if isinstance(content, Response):
            if content.status_code == 404:
                logger.warning(f"Server returned 404 for: {url}")
                raise NotFound(f"Server returned 404 for: {url}")
            logger.debug(f"Successfully fetched Response from {url} (status: {content.status_code})")
            return content

    except NetworkRequestError as e:
        logger.error(f"NetworkRequestError for {url}: {e}")
        raise NetworkError(str(e)) from e

    except InvalidProxy as e:
        logger.error(f"InvalidProxy for {url}: {e}")
        raise ProxyError(str(e)) from e

    except BotProtectionDetected as e:
        logger.error(f"BotProtectionDetected for {url}: {e}")
        raise BotDetection(str(e)) from e

    except UnknownError as e:
        logger.error(f"UnknownError for {url}: {e}")
        raise UnknownNetworkError(str(e)) from e


@dataclass(kw_only=True, slots=True)
class UserHelper(BaseMedia):
    url: str
    core: BaseCore
    info: dict | None = None
    bio: str | None = None
    about: str | None = None

    async def _perform_load(self, api: bool, html: bool, anything_else: bool):
        if html:
            await asyncio.gather(self._fetch_html())

    async def _fetch_html(self):
        logger.debug(f"Fetching HTML for UserHelper at {self.url}")
        html_content = await get_html_content(core=self.core, url=self.url)
        assert isinstance(html_content, str)
        data: dict = await asyncio.to_thread(self._extract_html, html_content)
        self.info = data.get("info")
        self.bio = data.get("bio")
        self.about = data.get("about")

    @staticmethod
    def _extract_html(html_content: str) -> dict:
        logger.debug("Extracting info from User HTML...")
        lexbor = LexborHTMLParser(html_content)
        info = {}

        try:
            bio = lexbor.css_first("div.content.js-headerContent.js-highestChild").css_first("div[itemprop]").text(strip=True)
        except AttributeError:
            bio = None

        try:
            about = lexbor.css_first("section.aboutMeSection.sectionDimensions").css("div")[1].text(strip=True)

        except AttributeError:
            try:
                about = lexbor.css_first("p.aboutMeText").text(strip=True)
            except AttributeError:
                about = None

        container = lexbor.css_first("div.content-columns.inline.js-highestChild.js-headerContent")

        if not container:
            container = lexbor.css_first("div.content-columns.js-highestChild.columns-2")

        if container:
            stuff = container.css("div.infoPiece")

            for thing in stuff:
                info[thing.css_first("span").text(strip=True)] = thing.css("span")[1].text(strip=True)

        return {
            "bio": bio,
            "about": about,
            "info": info
        }

    async def get_videos(self, pages: int = 5, videos_concurrency: int | None = None, pages_concurrency: int | None = None,
                         on_video_error: on_error_hint = on_error,
                         keep_original_order: bool = False, load_html: bool = False, load_api: bool = True,
                         on_page_error: on_error_hint = None) -> AsyncGenerator[ScrapeResult, None]:
        helper = Helper(core=self.core, constructor=Video)
        page_urls = [f"{self.url}/videos?page={page}" for page in range(1, pages + 1)]
        logger.debug(f"Processing: {len(page_urls)} pages...")
        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        async for result in helper.iterator(target_page_urls=page_urls, max_video_concurrency=videos_concurrency,
                                         max_page_concurrency=pages_concurrency, video_link_extractor=extractor_model,
                                         on_video_error=on_video_error, on_page_error=on_page_error,
                                         fetch_html=load_html, fetch_api=load_api, keep_original_order=keep_original_order):
            yield result


class SubscriptionHelper(Helper):

    async def get_subscriptions(self, url: str, pages: int = 5, pages_concurrency: int | None = None,
                                videos_concurrency: int | None = None,
                                on_video_error: on_error_hint = on_error,
                                on_page_error: on_error_hint = None,
                                keep_original_order: bool = False
                                ) -> AsyncGenerator[User, None]:
        helper = Helper(core=self.core, constructor=User)
        page_urls = [f"{url}?page={page}" for page in range(1, pages + 1)]
        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        async for result in helper.iterator(target_page_urls=page_urls, video_link_extractor=extractor_users,
                                        max_page_concurrency=pages_concurrency,
                                        max_video_concurrency=videos_concurrency,
                                        on_video_error=on_video_error, on_page_error=on_page_error,
                                        keep_original_order=keep_original_order):
            yield result


@dataclass(kw_only=True, slots=True)
class Pornstar(UserHelper):

    async def get_uploads(self, pages: int = 5, videos_concurrency: int | None = None, pages_concurrency: int | None = None,
                          on_video_error: on_error_hint = on_error, on_page_error: on_error_hint = None,
                          keep_original_order: bool = False,
                          load_html: bool = False, load_api: bool = True) -> AsyncGenerator[ScrapeResult, None]:
        helper = Helper(core=self.core, constructor=Video)
        page_urls = [f"{self.url}/videos/upload?page={page}" for page in range(1, pages + 1)]
        logger.debug(f"Processing: {len(page_urls)} pages...")
        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        async for result in helper.iterator(target_page_urls=page_urls, max_video_concurrency=videos_concurrency,
                                        max_page_concurrency=pages_concurrency, video_link_extractor=extractor_videos,
                                        on_video_error=on_video_error, on_page_error=on_page_error,
                                        keep_original_order=keep_original_order):
            yield result


    async def get_gifs(self, pages: int = 5, videos_concurrency: int | None = None, pages_concurrency: int | None = None,
                       on_video_error: on_error_hint = on_error, on_page_error: on_error_hint = None,
                       keep_original_order: bool = False,
                       load_html: bool = False, load_api: bool = True) -> AsyncGenerator[ScrapeResult, None]:
        helper = Helper(core=self.core, constructor=GIF)
        page_urls = [f"{self.url}/gifs/video?page={page}" for page in range(1, pages + 1)]
        logger.debug(f"Processing: {len(page_urls)} pages...")
        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        async for scrape_result in helper.iterator(target_page_urls=page_urls, max_video_concurrency=videos_concurrency,
                                           max_page_concurrency=pages_concurrency, video_link_extractor=extractor_gifs,
                                           on_video_error=on_video_error, on_page_error=on_page_error,
                                           keep_original_order=keep_original_order,
                                           fetch_api=load_api, fetch_html=load_html):
            yield scrape_result


@dataclass(kw_only=True, slots=True)
class Model(UserHelper):
    ...


@dataclass(kw_only=True, slots=True)
class User(UserHelper):
    ...


@dataclass(kw_only=True, slots=True)
class Album(BaseMedia):
    url: str
    core: BaseCore
    rating_percentage: str | None = None
    views: str | None = None
    publish_date: str | None = None
    tags: list[str] | None = None
    votes: str | None = None
    author_link: str | None = None

    async def _perform_load(self, api: bool, html: bool, anything_else: bool):
        if html:
            await asyncio.gather(self._fetch_html())

    async def _fetch_html(self):
        logger.debug(f"Fetching HTML for Album at {self.url}")
        html_content = await get_html_content(core=self.core, url=self.url)
        assert isinstance(html_content, str)
        data: dict = await asyncio.to_thread(self._extract_html, html_content)

        allowed_fields = {f.name for f in fields(self)}
        for key, value in data.items():
            if key in allowed_fields:
                setattr(self, key, value)

    @staticmethod
    def _extract_html(html_content: str) -> dict:
        logger.debug(f"Extracting info from Album HTML...")
        lexbor = LexborHTMLParser(html_content)

        rating_percentage = lexbor.css_first("div#ratingAlbumInfo").css_first("span").text(strip=True)
        votes = lexbor.css_first("div#ratingAlbumInfo > div").text(strip=True)
        views = lexbor.css_first("div#viewsPhotAlbumCounter").text(strip=True)
        publish_date = lexbor.css_first("div#timeBlockContent").css("div")[4].text(strip=True)
        _link = lexbor.css_first("span.usernameBadgesWrapper > a").attributes.get("href")
        author_link = f"https://www.pornhub.com{_link}"
        tags = {}

        stuff = lexbor.css_first("div.photoBoxContContainer").css("div.tagContainer")
        for a in stuff:
            text = a.text(strip=True)
            link = a.attributes.get("href")
            tags.update({
                text: f"https://www.pornhub.com{link}"
            })

        return {
            "rating_percentage": rating_percentage,
            "views": views,
            "publish_date": publish_date,
            "tags": tags,
            "votes": votes,
            "author_link": author_link
        }


    @property
    async def author(self, load_html: bool = True) -> Pornstar:
        star = Pornstar(url=self.author_link, core=self.core)
        return await star.load(html=load_html)

    @staticmethod
    def _parse_photos(html_content: str):
        tags = []
        lexbor = LexborHTMLParser(html_content)
        main_ul = lexbor.css_first("ul.photosAlbumsListing.albumViews.preloadImage")
        li_tags = main_ul.css("div.js_lazy_bkg.photoAlbumListBlock")
        for li_tag in li_tags:
            link = f"https://www.pornhub.com{li_tag.css_first("a").attributes.get('href')}"
            spans = li_tag.css("span")
            rating = spans[0].text(strip=True)
            views = spans[1].text(strip=True)
            download_url = li_tag.attributes.get("data-bkg")

            thing = {
                "url": link,
                "download_url": download_url,
                "rating": rating,
                "views": views,
            }
            tags.append(thing)

        return tags

    async def get_photos(self, pages: int ) -> AsyncGenerator[dict, None]:
        logger.info(f"Fetching photos for Album at {self.url} (pages: {pages})")
        page_urls = [f"{self.url}?page={page}" for page in range(1, pages + 1)]
        html_contents = [asyncio.create_task(get_html_content(core=self.core, url=url)) for url in page_urls]
        html_contents = await asyncio.gather(*html_contents)

        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor() as pool:
            parse_tasks = [loop.run_in_executor(pool, self._parse_photos, html) for html in html_contents]
            parsed_pages = await asyncio.gather(*parse_tasks) # Goes brrrrrrrrrrrrr


        for page_results in parsed_pages:
            for photo_data in page_results:
                yield photo_data

    async def download_photo(self, url: str, path: str) -> bool:
        logger.info(f"Downloading photo {url} to {path}")
        config = DownloadConfigRAW(path=path, quality="best") # yeahh
        return await self.core.legacy_download(url=url, configuration=config)


@dataclass(kw_only=True, slots=True)
class Short(BaseMedia):
    url: str
    core: BaseCore
    title: str | None = None
    video_id: str | None = None
    author_link: str | None = None
    video_key: str | None = None
    favorites: str | None = None
    likes: str | None = None
    dislikes: str | None = None
    is_hd: bool | None = None
    embed_url: str | None = None
    thumbnail: str | None = None
    media_definitions: dict | None = None
    comment_count: str | None = None
    avatar: str | None = None
    author_name: str | None = None
    video_url: str | None = None
    m3u8_base_url: str | None = None

    async def _perform_load(self, api: bool, html: bool, anything_else: bool):
        if html:
            await asyncio.gather(self._fetch_html())

    async def _fetch_html(self):
        logger.debug(f"Fetching HTML for Short at {self.url}")
        html_content = await get_html_content(core=self.core, url=self.url)
        assert isinstance(html_content, str)
        data: dict = await asyncio.to_thread(self._extract_html, html_content)

        allowed_fields = {f.name for f in fields(self)}
        for key, value in data.items():
            if key in allowed_fields:
                setattr(self, key, value)

    @staticmethod
    def _extract_html(html_content: str) -> dict:
        logger.debug("Extracting metadata from Short HTML...")
        parser = LexborHTMLParser(html_content)

        scripts = parser.css("script")
        metadata = {}

        for script in scripts:
            if "JSON_SHORTIES" in script.text():
                stuff = re.search(r'JSON_SHORTIES = insertAfterNthPosition\((.*?), prerollObject', script.text(), re.DOTALL).group(1)
                assert isinstance(stuff, str)
                script = chompjs.parse_js_object(stuff)
                metadata = script[0]

        title = metadata.get("videoTitle")
        video_id = metadata.get("videoId")
        video_key = metadata.get("vkey")
        favorites = metadata.get("favoriteInfo")
        likes = metadata.get("likeNumber")
        dislikes = metadata.get("dislikeNumber")
        is_hd = True if metadata.get("isHD") == "True" else False
        embed_url = metadata.get("embedUrl")
        thumbnail = metadata.get("imageUrl")
        media_definitions = metadata.get("mediaDefinitions")
        comment_count = metadata.get("commentCount")
        avatar = metadata.get("avatar")
        author_name = metadata.get("name")
        author_link = metadata.get("profileUrl")
        video_url = metadata.get("linkUrl")
        playlist_lines = ['#EXTM3U']
        for (width, height), uri in get_m3u8_urls(media_definitions).items():
            playlist_lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION={width}x{height}')
            playlist_lines.append(uri)

        m3u8_base_url = '\n'.join(playlist_lines)

        return {
            "title": title,
            "video_id": video_id,
            "video_key": video_key,
            "favorites": favorites,
            "likes": likes,
            "dislikes": dislikes,
            "is_hd": is_hd,
            "embed_url": embed_url,
            "thumbnail": thumbnail,
            "media_definitions": media_definitions,
            "comment_count": comment_count,
            "avatar": avatar,
            "author_name": author_name,
            "author_link": author_link,
            "video_url": video_url,
            "m3u8_base_url": m3u8_base_url
        }

    async def get_author(self, load_html: bool = True) -> Pornstar:
        star = Pornstar(url=self.author_link, core=self.core)
        return await star.load(html=load_html)

    @property
    async def get_video(self, load_html: bool = False, load_api: bool = True) -> Video:
        video = Video(url=self.video_url, core=self.core)
        return video.load(html=load_html, api=load_api)

    async def download(self, configuration: DownloadConfigHLS) -> bool | DownloadReport:
        """
        :param configuration:
        :return:
        """
        logger.info(f"Downloading Short {self.title} to {configuration.path}")
        config = configuration
        config.m3u8_base_url = self.m3u8_base_url
        if not config.no_title:
            config.path = os.path.join(config.path, f"{self.title}.mp4")

        try:
            return await self.core.download(configuration=config)

        except Exception as e:
            raise DownloadFailed(str(e))

@dataclass(kw_only=True, slots=True)
class GIF(BaseMedia):
    url: str
    core: BaseCore
    title: str | None = None
    vote_count: str | None = None
    vote_percentage: str | None = None
    views: str | None = None
    publish_date: str | None = None
    thumbnail: str | None = None
    content_url: str | None = None
    source_video_url: str | None = None
    tags: list[str] | None = None

    async def _perform_load(self, api: bool, html: bool, anything_else: bool):
        if html:
            await asyncio.gather(self._fetch_html())

    async def _fetch_html(self):
        logger.debug(f"Fetching HTML for GIF at {self.url}")
        html_content = await get_html_content(url=self.url, core=self.core)
        assert isinstance(html_content, str)

        if "GIF is unavailable pending review." in html_content:
            raise GifPendingReview("The GIF is still pending a review and can't be downloaded yet...")

        if "This video has been disabled" in html_content:
            raise VideoDisabled("The Video has been disabled, I can not fetch any data from it.")

        data: dict = await asyncio.to_thread(self._extract_html, html_content)
        allowed_fields = [f.name for f in fields(self)]

        for key, value in data.items():
            if key in allowed_fields:
                setattr(self, key, value)

    @staticmethod
    def _extract_html(html_content: str) -> dict:
        logger.debug(f"Extracting info from GIF HTML...")
        lexbor = LexborHTMLParser(html_content)
        script = json.loads(lexbor.css('script[type="application/ld+json"]')[0].text().replace(
            '<script type="application/ld+json">', ""))


        if "name" in script:
            title = script["name"]

        title_div = lexbor.css_first("div.gifTitle")
        if title_div and title_div.css_first("h1"):
            title = title_div.css_first("h1").text(strip=True)

        h1 = lexbor.css_first("h1")
        if h1:
            title = h1.text(strip=True)

        vote_count = lexbor.css_first("div.voteCount").css_first("span").text(strip=True)
        vote_percentage = lexbor.css_first("div.votePercentage").css_first("span").text(strip=True)
        views = lexbor.css_first("li.float-right.gifViews").text(strip=True)
        publish_date = script["uploadDate"]
        thumbnail = script["thumbnailUrl"]
        content_url = script["contentUrl"]
        _source_video_url = lexbor.css_first("div.bottomMargin").css_first("a").attributes.get("href")
        source_video_url = f"https://www.pornhub.com{_source_video_url}"
        tags = {}

        stuff = lexbor.css_first("ul.tagList.clearfix").css("li")
        for thing in stuff:
            link = thing.css_first("a")

            if link:
                first = thing.css_first("a").text(strip=True)
                href = thing.css_first("a").attributes.get("href")
                tags[first] = href

        return {
            "title": title,
            "vote_count": vote_count,
            "vote_percentage": vote_percentage,
            "views": views,
            "publish_date": publish_date,
            "thumbnail": thumbnail,
            "source_video_url": source_video_url,
            "content_url": content_url,
            "tags": tags
        }

    async def download(self, configuration: DownloadConfigRAW) -> bool:
        """
        :param configuration:
        :return:
        """
        logger.info(f"Downloading GIF {self.title} to {configuration.path}")
        config = configuration
        if not config.no_title:
            config.path = os.path.join(config.path, f"{self.title}.mp4")

        try:
            return await self.core.legacy_download(url=self.content_url, configuration=config)

        except Exception as e:
            raise DownloadFailed(str(e))


@dataclass(kw_only=True, slots=True)
class Channel(BaseMedia):
    url: str
    core: BaseCore
    name: str | None = None
    is_award_winner: bool | None = None
    video_views: str | None = None
    subscribers: str | None = None
    total_videos: str | None = None
    rank: str | None = None
    description: str | None = None
    join_date: str | None = None
    website: str | None = None
    user_link: str | None = None

    async def _perform_load(self, api: bool, html: bool, anything_else: bool):
        if html:
            await asyncio.gather(self._fetch_html())

    async def _fetch_html(self):
        logger.debug(f"Fetching HTML for Channel at {self.url}")
        html_content = await get_html_content(url=self.url, core=self.core)
        assert isinstance(html_content, str)
        data: dict = await asyncio.to_thread(self._extract_html, html_content)

        allowed_fields = {f.name for f in fields(self)}
        for key, value in data.items():
            if key in allowed_fields:
                setattr(self, key, value)

    @staticmethod
    def _extract_html(html_content: str) -> dict:
        logger.debug("Extracting info from Channel HTML...")
        lexbor = LexborHTMLParser(html_content)

        name = lexbor.css_first("div.title.floatLeft > h1").text(strip=True)
        is_award_winner = True if lexbor.css_first("i.trophyChannel.bg-trophy-channel.tooltipTrig") else False

        _meta = lexbor.css("div.info.floatRight")
        video_views = _meta[0].text(strip=True)
        subscribers = _meta[1].text(strip=True)
        total_videos = _meta[2].text(strip=True)
        rank = _meta[3].text(strip=True).replace("RANK", "")

        _meta_2 = lexbor.css("p.joined")
        description = _meta_2[0].text(strip=True)
        join_date = _meta[1].text(strip=True)
        website = _meta[2].text(strip=True)
        link = _meta_2[3].css_first("a").attributes.get("href")
        user_link = f"https://www.pornhub.com{link}"

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

    async def get_videos(self, pages: int = 5, videos_concurrency: int | None = None, pages_concurrency: int | None = None,
                         on_video_error: on_error_hint = on_error, on_page_error: on_error_hint = None
                         ) -> AsyncGenerator[ScrapeResult, None]:
        page_urls = [f"{self.url}videos?page={page}" for page in range(1, pages + 1)]
        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        async for result in self.iterator(target_page_urls=page_urls, max_video_concurrency=videos_concurrency,
                                         max_page_concurrency=pages_concurrency, video_link_extractor=extractor_videos,
                                         on_video_error=on_video_error, on_page_error=on_page_error):
            yield result

    async def get_user(self, load_html: bool = True) -> User:
        user = User(core=self.core, url=self.user_link)
        return await user.load(html=load_html)


@dataclass(kw_only=True, slots=True)
class Playlist(BaseMedia):
    url: str
    core: BaseCore
    token: str | None = None
    playlist_id: str | None = None
    title: str | None = None
    views: str | None = None
    rating_percent: str | None = None
    likes: str | None = None
    dislikes: str | None = None
    author_link: str | None = None
    video_count: str | None = None
    description: str | None = None
    unavailable_videos: int | None = None
    tags: list[str] | None = None

    async def _perform_load(self, api: bool, html: bool, anything_else: bool):
        if html:
            await asyncio.gather(self._fetch_html())

    async def _fetch_html(self):
        logger.debug(f"Fetching HTML for Playlist at {self.url}")
        html_content = await get_html_content(url=self.url, core=self.core)
        assert isinstance(html_content, str)
        data: dict = await asyncio.to_thread(self._extract_html, html_content)
        allowed_fields = [field.name for field in fields(self)]
        for key, value in data.items():
            if key in allowed_fields:
                setattr(self, key, value)

    def _extract_html(self, html_content: str) -> dict:
        logger.debug(f"Extracting info from Playlist HTML...")
        lexbor = LexborHTMLParser(html_content)

        token = REGEX_TOKEN.search(html_content).group(1)
        playlist_id = re.search(r'(\d+)/?$', self.url).group(1)
        title = lexbor.css_first("h1.playlistTitle.watchPlaylistButton.js-watchPlaylistHeader.js-watchPlaylist").text(strip=True)
        views = lexbor.css_first("div.views > span").text(strip=True)
        rating_percent = lexbor.css_first("div.votes-count-container > span").text(strip=True)
        likes = lexbor.css_first("div.votes-count-container").css("span")[1].text(strip=True)
        dislikes = lexbor.css_first("div.votes-count-container").css("span")[2].text(strip=True)
        _link = lexbor.css_first("div.usernameWrap.clearfix > a").attributes.get("href")
        author_link = f"https://www.pornhub.com{_link}"
        stuff = lexbor.css_first("div#js-aboutPlaylistTabView > div").text(strip=True)
        video_count = re.search(r'(\d+)\s*videos', stuff).group(1)
        description = lexbor.css_first("p.description.js-playlistDescription > span").text(strip=True)
        stuff = re.search(r'unavailable videos that are hidden:\s+(\d+)', html_content)
        unavailable_videos_count = int(stuff.group(1))

        tags = {}

        container = lexbor.css_first("div.tagsWrap.js-tagsWrap")
        _tags = container.css("a")
        for tag in _tags:
            name = tag.attributes.get("data-label")
            link = f"https://www.pornhub.com{tag.attributes.get('href')}"
            tags[str(name)] = link

        return {
            "token": token,
            "playlist_id": playlist_id,
            "title": title,
            "views": views,
            "rating_percent": rating_percent,
            "likes": likes,
            "dislikes": dislikes,
            "author_link": author_link,
            "video_count": video_count,
            "description": description,
            "unavailable_videos": unavailable_videos_count,
            "tags": tags,
        }

    async def get_videos(self, pages: int = 5, videos_concurrency: int | None = None, pages_concurrency: int | None = None,
                         on_video_error: on_error_hint = on_error, on_page_error: on_error_hint = None) -> AsyncGenerator[ScrapeResult, None]:
        # I will not optimize this function because I am too lazy to handle this one edge case here
        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency

        chunked_page_urls = [
            f'https://www.pornhub.com/playlist/viewChunked?id={self.playlist_id}&token={self.token}&page={page}'
            for page in range(1, pages + 1)
        ]

        helper = Helper(core=self.core, constructor=Video)
        if chunked_page_urls:
            async for result in helper.iterator(target_page_urls=chunked_page_urls, max_video_concurrency=videos_concurrency,
                                             max_page_concurrency=pages_concurrency, video_link_extractor=extractor_videos_playlist,
                                             on_video_error=on_video_error, on_page_error=on_page_error):
                yield result

    async def get_author(self, load_html: bool = True):
        user = User(url=self.author_link, core=self.core)
        return await user.load(html=load_html)


@dataclass(kw_only=True, slots=True)
class Video(BaseMedia):
    url: str
    core: BaseCore
    video_id: str | None = None

    # Flashvars / Configuration fields
    is_vr: bool | None = None
    is_video_unavailable: bool | None = None
    is_hd: bool | None = None
    duration: int | None = None
    title: str | None = None
    thumbnail: str | None = None
    available_qualities: list[int] | None = None
    is_vertical: bool | None = None
    is_video_unavailable_in_your_country: bool | None = None

    # HTML Scraped fields
    views: str | None = None
    publish_date: str | None = None
    likes: str | None = None

    # Playlist URL
    m3u8_base_url: str | None = None

    # Categorization maps
    categories: dict[str, str] | None = None
    tags: dict[str, str] | None = None

    # Author details
    author_thumbnail: str | None = None
    author_link: str | None = None
    author_information: dict[str, Any] | None = None

    async def _perform_load(self, api: bool, html: bool, anything_else: bool):
        if "/gif/" in self.url:
            return await GIF(url=self.url, core=self.core).load(html=html) # Please tell me this edge case never happens :skull:

        self.video_id = re.search(r"viewkey=([^&#]+)", self.url).group(1)

        tasks = []

        if html:
            tasks.append(asyncio.create_task(self._fetch_html()))

        if api:
            tasks.append(asyncio.create_task(self._fetch_api()))

        await asyncio.gather(*tasks)

    async def _fetch_html(self):
        logger.debug(f"Fetching HTML for Video at {self.url}")
        html_content = await get_html_content(core=self.core, url=self.url)
        assert isinstance(html_content, str)
        data: dict = await asyncio.to_thread(self._extract_html, html_content)
        allowed_fields = {f.name for f in fields(self)}
        for key, value in data.items():
            if key in allowed_fields:
                setattr(self, key, value)

    async def _fetch_api(self):
        logger.debug(f"Fetching API data for Video {self.video_id}")
        stuff = await get_html_content(url=f"https://www.pornhub.com/webmasters/video_by_id?id={self.video_id}", core=self.core)
        assert isinstance(stuff, str)
        data: dict = await asyncio.to_thread(self._extract_api, stuff)
        allowed_fields = {f.name for f in fields(self)}
        for key, value in data.items():
            if key in allowed_fields:
                setattr(self, key, value)

    @staticmethod
    def _extract_html(html_content: str) -> dict:
        logger.debug("Extracting info from Video HTML...")
        parser = LexborHTMLParser(html_content)

        match = REGEX_VIDEO_FLASHVARS.search(html_content).group(1)
        flashvars = json.loads(match, strict=False)
        is_vr = False if flashvars["isVR"] == 0 else True
        is_video_unavailable = False if flashvars["video_unavailable"] == "false" else True
        is_hd = False if flashvars["isHD"] == "false" else True
        duration = int(flashvars["video_duration"])
        title = flashvars["video_title"]
        thumbnail = flashvars["image_url"]
        available_qualities = sorted(flashvars["defaultQuality"])
        is_vertical = True if flashvars["isVertical"] == "true" else False
        is_video_unavailable_in_your_country = True if flashvars["video_unavailable_country"] == "true" else False
        views = parser.css_first("div.video-actions-menu.ctasActionMenu").css_first("div.views > span").text(strip=True)
        publish_date = parser.css_first("div.video-actions-menu.ctasActionMenu").css_first("div.videoInfo").text(strip=True)
        likes = parser.css_first("span.votesUp").text(strip=True)
        author_thumbnail = parser.css_first("div.userAvatar").css_first("img").attributes.get("src")

        """Builds a fake master.m3u8 playlist from quality-specific m3u8 URLs."""
        playlist_lines = ['#EXTM3U']
        definitions = flashvars["mediaDefinitions"]
        stuff = get_m3u8_urls(media_definitions=definitions)
        for (width, height), uri in stuff.items():
            playlist_lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION={width}x{height}')
            playlist_lines.append(uri)
        m3u8_base_url = '\n'.join(playlist_lines)

        categories = {}

        stuff = parser.css_first("div.categoriesWrapper").css("a.gtm-event-video-underplayer.item")
        for thing in stuff:
            first = thing.text(strip=True)
            href = thing.attributes.get("href")
            categories[first] = href

        tags = {}

        stuff = parser.css_first("div.tagsWrapper").css("a.video_underplayer")
        for thing in stuff:
            first = thing.text(strip=True)
            href = thing.attributes.get("href")
            tags[first] = href

        link = parser.css_first("div.userAvatar > a").attributes.get("href")
        author_link = f"https://www.pornhub.com{link}"

        stuff = parser.css_first("div.userInfo")
        a_tag = stuff.css_first("span.usernameBadgesWrapper a, div.usernameWrap a")

        name = a_tag.text(strip=True)
        link = a_tag.attributes.get("href")
        link = f"https://www.pornhub.com/{link}"

        video_amount = stuff.css("span")[1].text(strip=True)
        subscriber_amount = stuff.css("span")[2].text(strip=True)

        author_information = {
            "name": name,
            "link": {link},
            "video_amount": video_amount,
            "subscriber_amount": subscriber_amount
        }

        return {
            "is_vr": is_vr,
            "is_video_unavailable": is_video_unavailable,
            "is_hd": is_hd,
            "duration": duration,
            "title": title,
            "thumbnail": thumbnail,
            "available_qualities": available_qualities,
            "is_vertical": is_vertical,
            "is_video_unavailable_in_your_country": is_video_unavailable_in_your_country,
            "views": views,
            "publish_date": publish_date,
            "likes": likes,
            "author_thumbnail": author_thumbnail,
            "m3u8_base_url": m3u8_base_url,
            "categories": categories,
            "tags": tags,
            "author_link": author_link,
            "author_information": author_information
        }


    @staticmethod
    def _extract_api(json_data: str) -> dict:
        logger.debug("Extracting API data for Video...")
        raw = json.loads(json_data, strict=False)
        json_data = raw.get("video", {})
        dur = json_data.get("duration")
        if isinstance(dur, str) and ":" in dur:
            parts = dur.split(":")
            if len(parts) == 2:
                duration = int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

        else:
            duration = dur

        thumbnail = json_data.get("default_thumb") or json_data.get("thumb")
        title = json_data.get("title")
        views = json_data.get("views", "0")
        publish_date = json_data.get("publish_date", "")
        rating_percent = json_data.get("rating")
        likes = json_data.get("ratings", "0")

        categories = []
        tags = []

        _tags = json_data.get("categories")
        if _tags:
            for tag in _tags:
                categories.append(tag["category"])

        _tags = json_data.get("tags")
        if _tags:
            for tag in _tags:
                tags.append(tag["tag_name"])
        return {
            "thumbnail": thumbnail,
            "duration": duration,
            "title": title,
            "views": views,
            "publish_date": publish_date,
            "rating_percent": rating_percent,
            "likes": likes,
            "categories": categories,
            "tags": tags
        }

    @property
    async def author(self) -> Pornstar | Channel | Model | None:
        if "pornstar" in self.author_link:
            pornstar = Pornstar(core=self.core, url=self.author_link)
            return await pornstar.init()

        elif "model" in self.author_link:
            model = Model(core=self.core, url=self.author_link)
            return await model.init()

        elif "channel" in self.author_link:
            channel = Channel(core=self.core, url=self.author_link)
            return await channel.init()

        else:
            None

    async def download(self, configuration: DownloadConfigHLS) -> bool | DownloadReport:
        """
        :param configuration:
        :return:
        """

        logger.info(f"Downloading Video {self.title} to {configuration.path}")
        config = configuration
        config.m3u8_base_url = self.m3u8_base_url
        if not config.no_title:
            config.path = os.path.join(config.path, f"{self.title}.mp4")

        try:
            return await self.core.download(configuration=config)

        except Exception as e:
            raise DownloadFailed(str(e))


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

    async def get_recommended(self, pages: int = 5, load_html: bool = False, load_api: bool = True) -> AsyncGenerator[ScrapeResult, None]:
        async for result in self.client.get_recommended(pages=pages, load_html=load_html, load_api=load_api):
            yield result

    async def get_history(self, pages: int = 5, load_html: bool = False, load_api: bool = True) -> AsyncGenerator[ScrapeResult, None]:
        async for result in self.client.get_history(pages=pages, load_html=load_html, load_api=load_api):
            yield result

    async def get_favorites(self, pages: int = 5, load_html: bool = False, load_api: bool = True) -> AsyncGenerator[ScrapeResult, None]:
        async for result in self.client.get_favorites(pages=pages, load_html=load_html, load_api=load_api):
            yield result

    async def get_feed(self, section: str = 'videos', pages: int = 5, load_html: bool = False, load_api: bool = True) -> AsyncGenerator[ScrapeResult, None]:
        async for result in self.client.get_feed(section=section, pages=pages, load_html=load_html, load_api=load_api):
            yield result

    async def get_subscriptions(self, pages: int = 5) -> AsyncGenerator[User, None]:
        async for user in self.client.get_subscriptions(pages=pages):
            yield user

    def __repr__(self) -> str:
        status = 'logged-out' if self.name is None else f'name={self.name}'
        return f'Account({status})'


class Client:
    def __init__(self, core: BaseCore = BaseCore(), email: str | None = None, password: str | None = None, login: bool = False):
        self.core = core or BaseCore()
        self.core.initialize_session()
        assert isinstance(self.core.session, AsyncSession)
        self.core.session.headers.update(HEADERS)
        self.core.session.cookies.update(COOKIES)
        self.helper = Helper(self.core, constructor=Video)

        self.credentials = {"email": email, "password": password}
        self.logged = False
        self.account = Account(self)

        if login and email and password:
            asyncio.create_task(self.login())

    async def login(self, force: bool = False, throw: bool = True) -> bool:
        """
        Attempt to log in asynchronously.
        """
        logger.info(f"Attempting login")

        if not force and self.logged:
            if throw:
                raise ClientAlreadyLogged()
            return True

        if not self.credentials["email"] or not self.credentials["password"]:
            if throw:
                raise LoginFailed("Email and password are required")
            return False

        # Get token from homepage
        page_content = await get_html_content(url=HOST, core=self.core)
        match = REGEX_TOKEN.search(page_content)
        if not match:
            if throw:
                raise LoginFailed("Could not find login token")
            return False

        token = match.group(1)

        # Send credentials
        payload = LOGIN_PAYLOAD | self.credentials | {"token": token}
        
        url = f"{HOST}front/authenticate"
        try:
            response = await self.core.fetch(url, method="POST", data=payload, get_response=True)
            assert isinstance(response, Response)
            data = response.json()
        except Exception as e:
            if throw:
                raise LoginFailed(f"Login request failed: {e}")
            return False

        success = int(data.get("success", 0))
        message = data.get("message", "Unknown error")

        if not success:
            if throw:
                raise LoginFailed(message)
            return False

        # Update account data
        self.account.connect(data)
        self.logged = True
        return True

    async def fix_recommendations(self) -> bool:
        """
        Allow recommendations cookies.
        """
        if not self.logged:
            return False

        logger.info("Fixing account recommendations")
        
        # Get token
        page_content = await get_html_content(url=HOST, core=self.core)
        match = REGEX_TOKEN.search(page_content)
        if not match:
            return False
        token = match.group(1)

        params = {
            'token': token,
            'cookie_selection': 3,
            'site_id': 1
        }
        url = f"{HOST}user/log_user_cookie_consent"
        try:
            response = await self.core.fetch(url, params=params, get_response=True)
            assert isinstance(response, Response)
            return response.json().get("success", False)
        except Exception:
            return False

    async def get_recommended(self, pages: int = 5, videos_concurrency: int | None = None, pages_concurrency: int | None = None,
                              on_video_error: on_error_hint = on_error, on_page_error: on_error_hint = None,
                              load_html: bool = False, load_api: bool = True,
                              keep_original_order: bool = False) -> AsyncGenerator[ScrapeResult, None]:
        """
        Get recommended videos for the logged-in account.
        """
        await self.fix_recommendations()

        base_url = f"{HOST}recommended"
        page_urls = [f"{base_url}?page={page}" for page in range(1, pages + 1)]

        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        async for result in self.helper.iterator(target_page_urls=page_urls, max_video_concurrency=videos_concurrency,
                                       max_page_concurrency=pages_concurrency, video_link_extractor=extractor_videos,
                                       on_video_error=on_video_error, on_page_error=on_page_error,
                                       fetch_api=load_api, fetch_html=load_html, keep_original_order=keep_original_order):
            yield result

    async def get_history(self, pages: int = 5, videos_concurrency: int | None = None, pages_concurrency: int | None = None,
                          on_video_error: on_error_hint = on_error, on_page_error: on_error_hint = None,
                          load_html: bool = False, load_api: bool = True,
                          keep_original_order: bool = False) -> AsyncGenerator[ScrapeResult, None]:
        """
        Get watch history for the logged-in account.
        """
        if not self.logged:
            raise LoginFailed("Must be logged in to access history")

        base_url = f"{HOST}users/{self.account.name}/videos/recent"
        page_urls = [f"{base_url}?page={page}" for page in range(1, pages + 1)]

        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        async for result in self.helper.iterator(target_page_urls=page_urls, max_video_concurrency=videos_concurrency,
                                        max_page_concurrency=pages_concurrency, video_link_extractor=extractor_videos,
                                        on_video_error=on_video_error, on_page_error=on_page_error,
                                        fetch_html=load_html, keep_original_order=keep_original_order, fetch_api=load_api):
            yield result

    async def get_favorites(self, pages: int = 5, videos_concurrency: int | None = None, pages_concurrency: int | None = None,
                            on_video_error: on_error_hint = on_error, on_page_error: on_error_hint = None,
                            load_html: bool = False, load_api: bool = True,
                            keep_original_order: bool = False) -> AsyncGenerator[ScrapeResult, None]:
        """
        Get favorite videos for the logged-in account.
        """
        if not self.logged:
            raise LoginFailed("Must be logged in to access favorites")

        base_url = f"{HOST}users/{self.account.name}/videos/favorites"
        page_urls = [f"{base_url}?page={page}" for page in range(1, pages + 1)]

        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency

        async for result in self.helper.iterator(target_page_urls=page_urls, max_video_concurrency=videos_concurrency,
                                        max_page_concurrency=pages_concurrency, video_link_extractor=extractor_videos,
                                        on_video_error=on_video_error, on_page_error=on_page_error,
                                        keep_original_order=keep_original_order, fetch_api=load_api, fetch_html=load_html):
            yield result

    async def get_feed(self, section: str = 'videos', pages: int = 5, videos_concurrency: int | None = None, pages_concurrency: int | None = None,
                       on_video_error: on_error_hint = on_error, on_page_error: on_error_hint = None,
                       load_html: bool = False, load_api: bool = True,
                       keep_original_order: bool = False) -> AsyncGenerator[ScrapeResult, None]:
        """
        Get the account feed.
        :param load_api:
        :param load_html:
        :param keep_original_order:
        :param pages_concurrency:
        :param videos_concurrency:
        :param section: Section to filter (videos, photos, posts, etc.)
        :param on_video_error:
        :param on_page_error:
        :param pages: Number of pages to fetch.
        """
        if not self.logged:
            raise LoginFailed("Must be logged in to access feed")

        base_url = f"{HOST}feeds?section={section}"
        page_urls = [f"{base_url}&page={page}" for page in range(1, pages + 1)]

        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency

        async for result in self.helper.iterator(target_page_urls=page_urls, max_video_concurrency=videos_concurrency,
                                        max_page_concurrency=pages_concurrency, video_link_extractor=extractor_videos,
                                        on_video_error=on_video_error, on_page_error=on_page_error,
                                        keep_original_order=keep_original_order, fetch_api=load_api, fetch_html=load_html):
            yield result

    async def get_subscriptions(self, pages: int = 5, pages_concurrency: int | None = None, videos_concurrency: int | None = None,
                                on_video_error: on_error_hint = on_error, on_page_error: on_error_hint = None) -> AsyncGenerator[User, None]:
        """
        Get the account subscriptions.
        """
        if not self.logged:
            raise LoginFailed("Must be logged in to access subscriptions")

        url = f"{HOST}users/{self.account.name}/subscriptions"
        helper = SubscriptionHelper(core=self.core, constructor=User)
        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        async for user in helper.get_subscriptions(url=url, pages=pages, pages_concurrency=pages_concurrency, videos_concurrency=videos_concurrency,
                                                   on_video_error=on_video_error, on_page_error=on_page_error):
            yield user


    async def get_video(self, url: str, load_html: bool = False, load_api: bool = True) -> Video:
        """
        :param url: (str) The video URL
        :param force_scraping: (bool) Whether to force web scraping instead of using the API
        :return: (Video) The video object
        """
        logger.debug(f"Client instantiating Video {url}")
        video = Video(url=url, core=self.core)
        return await video.load(html=load_html, api=load_api)

    async def get_pornstar(self, url: str, load_html: bool = True) -> Pornstar:
        """
        :param url: (str) The Pornstar URL
        :return: (Video) The Pornstar object
        """
        pornstar = Pornstar(url=url, core=self.core)
        return await pornstar.load(html=load_html)

    async def get_gif(self, url: str, load_html: bool = True) -> GIF:
        """
        param url: (str) The GIF URL
        :return: (GIF) The GIF object
        """
        gif = GIF(url=url, core=self.core)
        return await gif.load(html=load_html)

    async def get_album(self, url: str, load_html: bool = True) -> Album:
        """
        param url: (str) The Album URL:
        :param url:
        :return:
        """
        album = Album(url=url, core=self.core)
        return await album.load(html=load_html)

    async def get_short(self, url: str, load_html: bool = True) -> Short:
        """
        param url: (str) The Short URL:
        :param url:
        :return:
        """
        short = Short(url=url, core=self.core)
        return await short.load(html=load_html)

    async def get_model(self, url: str, load_html: bool = True) -> Model:
        """
        param url: (str) The Model URL:
        :param url:
        :return:
        """
        model = Model(url=url, core=self.core)
        return await model.load(html=load_html)

    async def get_user(self, url: str, load_html: bool = True) -> User:
        """
        param url: (str) The User URL:
        :param url:
        :return:
        """
        user = User(url=url, core=self.core)
        return await user.load(html=load_html)

    async def get_playlist(self, url: str, load_html: bool = True) -> Playlist:
        playlist = Playlist(url=url, core=self.core)
        return await playlist.load(html=load_html)

    async def get_channel(self, url: str, load_html: bool = True) -> Channel:
        channel = Channel(url=url, core=self.core)
        return await channel.load(html=load_html)

    async def search_gifs(self, query: str, category: Literal["gay", "transgender"] | None = None,
                          search_filter: Literal["mr", "mv", "tr"] | None = None,
                          pages: int = 5,
                          pages_concurrency: int | None = None, videos_concurrency: int | None = None,
                          on_video_error: on_error_hint = on_error, on_page_error: on_error_hint = None,
                          keep_original_order: bool = False, load_html: bool = True) -> AsyncGenerator[ScrapeResult, None]:
        """
        :param search_filter: [mr = Most Recent, mv = Most Viewed, tr = Top Rated] Default: Most relevant
        :param category: [gay, transgender] Default: Straight
        :param query:
        :param pages: Default: 5
        :param videos_concurrency:
        :param pages_concurrency:
        :return:
        """

        base_url = "https://www.pornhub.com/"

        if category:
            base_url += category + "/"

        base_url += f"gifs/search?search={query}"
        if search_filter:
            base_url += f"&o={search_filter}"

        page_urls = [f"{base_url}&page={page}" for page in range(1, pages + 1)]
        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        helper = Helper(core=self.core, constructor=GIF)
        async for result in helper.iterator(target_page_urls=page_urls, max_video_concurrency=videos_concurrency,
                                    max_page_concurrency=pages_concurrency, video_link_extractor=extractor_gifs,
                                    on_video_error=on_video_error, on_page_error=on_page_error,
                                    keep_original_order=keep_original_order, fetch_html=load_html):
            yield result

    async def search_videos(self, query: str, production_type: Literal["professional", "homemade"] | None = None,
                            sort_by: Literal["mr", "mv", "tr"] | None = None,
                            duration_min: Literal["10", "20", "30"] | None = None,
                            duration_max: Literal["10", "20", "30"] | None = None,
                            pages: int = 5,
                            videos_concurrency: int | None = None,
                            pages_concurrency: int | None = None,
                            on_video_error: on_error_hint = on_error,
                            on_page_error: on_error_hint = None,
                            keep_original_order: bool = False, load_html: bool = False, load_api: bool = True
                            ) -> AsyncGenerator[ScrapeResult, None]:
        base_url = f"https://www.pornhub.com/video/search?search={query}"
        if production_type:
            base_url += f"&p={production_type}"

        if sort_by:
            base_url += f"&o={sort_by}"

        if duration_min:
            base_url += f"&duration_min={duration_min}"

        if duration_max:
            base_url += f"&duration_max={duration_max}"

        page_urls = [f"{base_url}&page={page}" for page in range(1, pages + 1)]
        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        async for result in self.helper.iterator(target_page_urls=page_urls, max_video_concurrency=videos_concurrency,
                                max_page_concurrency=pages_concurrency, video_link_extractor=extractor_videos,
                                on_video_error=on_video_error, on_page_error=on_page_error,
                                keep_original_order=keep_original_order, fetch_html=load_html, fetch_api=load_api):
            yield result

    async def search_hubtraffic(self, query: str,
                                 category: str | None = None,
                                 sort_by: Literal["newest", "mostviewed", "rating"] | None = None,
                                 period: Literal["weekly", "monthly", "alltime"] | None = None,
                                 pages: int = 5, on_video_error: on_error_hint = on_error,
                                 on_page_error: on_error_hint = None, load_html: bool = False,
                                 load_api: bool = True, keep_original_order: bool = False,
                                 pages_concurrency: int = 5,
                                 videos_concurrency: int = 20,
                                 ) -> AsyncGenerator[ScrapeResult, None]:
        """
        Search for videos using the HubTraffic API (Webmaster API).
        This is faster and provides pre-parsed metadata.
        """
        base_url = f"https://www.pornhub.com/webmasters/search?search={query}"
        if category:
            base_url += f"&category={category}"
        if sort_by:
            base_url += f"&ordering={sort_by}"
        if period:
            base_url += f"&period={period}"

        page_urls = [f"{base_url}&page={page}" for page in range(1, pages + 1)]
        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        async for result in self.helper.iterator(target_page_urls=page_urls, max_video_concurrency=videos_concurrency,
                                max_page_concurrency=pages_concurrency, video_link_extractor=extractor_videos,
                                on_video_error=on_video_error, on_page_error=on_page_error,
                                keep_original_order=keep_original_order, fetch_html=load_html, fetch_api=load_api):
            yield result



def str_to_bool(val: str) -> bool:
    return val.lower() in ('yes', 'true', 't', '1')

def can_download(state: dict) -> bool:
    if state["limit"] is None:
        return True
    return state["downloaded"] < state["limit"]

async def _cli_download_video_generator(generator, args, no_title: bool, state: dict):
    async for video in generator:
        if not can_download(state): break
        
        if getattr(args, "id_as_title", False) and hasattr(video, "video_id"):
            final_path = os.path.join(args.output, f"{video.video_id}.mp4")
            no_title_arg = True
        else:
            final_path = args.output
            no_title_arg = no_title
            
        await video.ensure_html()
        await video.download(quality=args.quality, path=final_path, no_title=no_title_arg)
        state["downloaded"] += 1

async def _cli_process_url(client: Client, url: str, args, no_title: bool, state: dict):
    try:
        if "view_video.php" in url:
            if not can_download(state): return
            video = await client.get_video(url, load_html=True)
            
            if getattr(args, "id_as_title", False) and hasattr(video, "video_id"):
                final_path = os.path.join(args.output, f"{video.video_id}.mp4")
                no_title_arg = True
            else:
                final_path = args.output
                no_title_arg = no_title

            config = DownloadConfigHLS(quality=args.quality, path=final_path, no_title=no_title_arg)
            await video.download(config)
            state["downloaded"] += 1
            
        elif "/short/" in url:
            if not can_download(state): return
            short = await client.get_short(url)
            
            if getattr(args, "id_as_title", False) and hasattr(short, "video_id"):
                final_path = os.path.join(args.output, f"{short.video_id}.mp4")
                no_title_arg = True
            else:
                final_path = args.output
                no_title_arg = no_title
                
            config = DownloadConfigHLS(quality=args.quality, path=final_path, no_title=no_title_arg)
            await short.download(config)
            state["downloaded"] += 1
            
        elif "/gif/" in url:
            if not can_download(state): return
            gif = await client.get_gif(url)
            config = DownloadConfigRAW(quality=args.quality, path=args.output)
            await gif.download(config)
            state["downloaded"] += 1
            
        elif "/album/" in url:
            album = await client.get_album(url)
            async for photo in album.get_photos(pages=args.pages):
                if not can_download(state): break
                await album.download_photo(photo["download_url"], path=args.output)
                state["downloaded"] += 1
                
        else:
            if "/pornstar/" in url:
                obj = await client.get_pornstar(url)
            elif "/model/" in url:
                obj = await client.get_model(url)
            elif "/users/" in url:
                obj = await client.get_user(url)
            elif "/channels/" in url:
                obj = await client.get_channel(url)
            elif "/playlists/" in url:
                obj = await client.get_playlist(url)
            else:
                print(f"Unsupported or unrecognized URL format: {url}")
                return

            await _cli_download_video_generator(obj.get_videos(pages=args.pages), args, no_title, state)
                
    except Exception as e:
        print(f"Error processing {url}: {e}")

async def run_main():
    parser = argparse.ArgumentParser(description="PornHub API Command Line Interface")
    parser.add_argument("--download", metavar="URL (str)", type=str, help="URL to download from")
    parser.add_argument("--quality", metavar="best,half,worst", type=str, help="The video quality (best,half,worst)", required=True)
    parser.add_argument("--file", metavar="Source to .txt file", type=str, help="(Optional) Specify a file with URLs (separated with new lines)")
    parser.add_argument("--output", metavar="Output directory", type=str, help="The output path (with filename)", required=True)
    parser.add_argument("--no-title", metavar="True,False", type=str, help="Whether to apply video title automatically to output path or not", required=True)
    parser.add_argument("--pages", metavar="Pages (int)", type=int, default=1, help="Number of pages to fetch for iterables (Default: 1)")
    parser.add_argument("--email", type=str, help="Account email for login", default=None)
    parser.add_argument("--password", type=str, help="Account password for login", default=None)
    parser.add_argument("--id-as-title", action="store_true", help="Use the video ID as the output title")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of videos to download")
    parser.add_argument("--liked", action="store_true", help="Download liked/favorite videos (requires login)")
    parser.add_argument("--recommended", action="store_true", help="Download recommended videos (requires login)")
    parser.add_argument("--watched", action="store_true", help="Download watched/history videos (requires login)")

    args = parser.parse_args()
    no_title = str_to_bool(args.no_title)

    login = False
    client = Client(email=args.email, password=args.password, login=False)
    if args.email and args.password:
        login = True
        await client.login()

    urls = []
    if args.download:
        urls.append(args.download)

    if args.file:
        with open(args.file, "r") as file:
            content = file.read().splitlines()
            urls.extend(content)

    state = {"downloaded": 0, "limit": args.limit}

    for url in urls:
        await _cli_process_url(client, url, args, no_title, state)
        if not can_download(state):
            break

    if login:
        if getattr(args, "liked", False):
            await _cli_download_video_generator(client.get_favorites(pages=args.pages), args, no_title, state)
        if getattr(args, "recommended", False):
            await _cli_download_video_generator(client.get_recommended(pages=args.pages), args, no_title, state)
        if getattr(args, "watched", False):
            await _cli_download_video_generator(client.get_history(pages=args.pages), args, no_title, state)
    else:
        if getattr(args, "liked", False) or getattr(args, "recommended", False) or getattr(args, "watched", False):
            print("Warning: --liked, --recommended, and --watched require --email and --password to work. Skipping.")

def cli():
    asyncio.run(run_main())


if __name__ == "__main__":
    asyncio.run(run_main())
