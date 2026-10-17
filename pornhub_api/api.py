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
from functools import partial
import html
import json
import chompjs
import logging
import asyncio
import argparse

from pornhub_api.modules import errors as provider_errors
from base_api import append_url_path, profile_base_url, replace_url_path, update_url_query
from urllib.parse import urlsplit
from base_api.modules.provider import fetch_content, download_errors, prepare_download_config
from base_api.modules.logger import configure_app_logging, get_logger, log_context

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
from base_api.modules.errors import DownloadCancelled

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


logger = get_logger(__name__)

MediaT = TypeVar("MediaT", bound=BaseMedia)


def _requested_sources(*, html: bool = False, api: bool = False) -> tuple[str, ...]:
    return tuple(source for source, enabled in (("api", api), ("html", html)) if enabled)


def build_m3u8_master(media_definitions: list[dict] | None) -> str | None:
    urls = get_m3u8_urls(media_definitions)
    if not urls:
        return None
    lines = ['#EXTM3U']
    for (width, height), uri in urls.items():
        lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION={width}x{height}')
        lines.append(uri)
    return '\n'.join(lines)


@download_errors(DownloadFailed)
async def _download_hls(media: BaseMedia, configuration: DownloadConfigHLS) -> bool | DownloadReport:
    await media.load_fields("title", "m3u8_base_url")
    if not media.m3u8_base_url:
        raise DownloadFailed(f"No m3u8 playlist found for {media.url}")
    media_title = media.title or f"{type(media).__name__.lower()}_{getattr(media, 'video_key', None) or getattr(media, 'video_id', None) or 'unknown'}"
    logger.info(f"Downloading {type(media).__name__} {media_title} to {configuration.path}")
    config = prepare_download_config(configuration, strip_title(media_title))
    config.m3u8_base_url = media.m3u8_base_url

    return await media.core.download(configuration=config)


async def get_html_content(core: BaseCore, url: str, *, owner=None) -> str:
    return await fetch_content(core, url, logger=logger, owner=owner,
                               error_types=provider_errors)


@dataclass(kw_only=True, slots=True)
class UserHelper(BaseMedia):

    def __post_init__(self):
        profile_base_url(self.url, depth=2)

    url: str
    core: BaseCore
    user_id: str | None = media_field("html")
    name: str | None = media_field("html")
    avatar: str | None = media_field("html")
    cover: str | None = media_field("html")
    bio: str | None = media_field("html")
    about: str | None = media_field("html")
    info: dict[str, str] | None = media_field("html")
    ranks: dict[str, str] | None = media_field("html")
    is_verified: bool | None = media_field("html")
    is_premium: bool | None = media_field("html")
    is_award_winner: bool | None = media_field("html")
    subscribers: str | None = media_field("html")
    subscribers_count: int | None = media_field("html")
    video_views: str | None = media_field("html")
    video_views_count: int | None = media_field("html")
    profile_views: str | None = media_field("html")
    profile_views_count: int | None = media_field("html")
    videos_watched: str | None = media_field("html")
    videos_watched_count: int | None = media_field("html")
    social_links: dict[str, str] | None = media_field("html")
    external_link: str | None = media_field("html")
    external_link_text: str | None = media_field("html")
    token: str | None = media_field("html")

    gender: str | None = media_field("html")
    birth_place: str | None = media_field("html")
    relationship_status: str | None = media_field("html")
    interested_in: str | None = media_field("html")
    height: str | None = media_field("html")
    weight: str | None = media_field("html")
    ethnicity: str | None = media_field("html")
    hair_color: str | None = media_field("html")
    eye_color: str | None = media_field("html")
    measurements: str | None = media_field("html")
    fake_boobs: str | None = media_field("html")
    tattoos: str | None = media_field("html")
    piercings: str | None = media_field("html")
    career_status: str | None = media_field("html")
    career_start_end: str | None = media_field("html")
    star_sign: str | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}

    async def _load_html(self) -> dict[str, object]:
        logger.debug(f"Fetching HTML for UserHelper at {self.url}")
        html_content = await get_html_content(core=self.core, url=self.url, owner=self)
        return await asyncio.to_thread(self._extract_html, html_content, self.url)

    @staticmethod
    def _extract_html(html_content: str, url: str = "") -> dict[str, Any]:
        logger.debug("Extracting info from User HTML...")
        lexbor = LexborHTMLParser(html_content)

        # 1. Name
        name = None
        name_node = lexbor.css_first(
            "h1[itemprop='name'], .name h1, div.profileUserName a, div.profileUserName, "
            "#topProfileHeader h1, .titleWrapper h1, .coverImage h1"
        )
        if name_node:
            name = name_node.text(strip=True)
        if not name:
            title_tag = lexbor.css_first("title")
            if title_tag:
                t = title_tag.text(strip=True)
                for suffix in (" - Pornhub.com", " | Pornhub.com", " Official Pornhub Profile"):
                    if suffix in t:
                        t = t.replace(suffix, "").strip()
                name = t or None
        if not name and url:
            m = re.search(r"/(?:pornstar|model|users)/([^/?#]+)", url)
            if m:
                name = m.group(1).replace("-", " ").title()
        if not name:
            logger.warning(f"Failed to extract name for user at {url}")

        # 2. User ID
        user_id = None
        flag = lexbor.css_first("a.flagUserProfile[data-user-id]")
        if flag:
            user_id = flag.attributes.get("data-user-id")
        if not user_id:
            btn = lexbor.css_first("button[data-user-id]")
            if btn:
                user_id = btn.attributes.get("data-user-id")
        if not user_id:
            sub = lexbor.css_first("[data-subscribe-url]")
            if sub:
                sub_url = sub.attributes.get("data-subscribe-url") or ""
                m = re.search(r"[?&]id=(\d+)", sub_url)
                if m:
                    user_id = m.group(1)
        if not user_id:
            btn_id = lexbor.css_first("button[data-id], div[data-userid], a[data-userid]")
            if btn_id:
                user_id = btn_id.attributes.get("data-id") or btn_id.attributes.get("data-userid")
        if not user_id:
            m = re.search(r"user(?:Id|_id)[\"']?\s*[:=]\s*['\"]?(\d+)", html_content)
            if m:
                user_id = m.group(1)
        if not user_id:
            logger.warning(f"Failed to extract user_id for user at {url}")

        # 3. Token
        token = None
        sub = lexbor.css_first("[data-subscribe-url]")
        if sub:
            sub_url = sub.attributes.get("data-subscribe-url") or ""
            m = re.search(r"[?&]token=([^&]+)", sub_url)
            if m:
                token = m.group(1)
        if not token:
            tok_el = lexbor.css_first("input[name='token'], [data-token]")
            if tok_el:
                token = tok_el.attributes.get("value") or tok_el.attributes.get("data-token")
        if not token:
            m = re.search(r"['\"]?token['\"]?\s*[:=]\s*['\"]([^'\"]{20,})['\"]", html_content)
            if m:
                token = m.group(1)

        # 4. Avatar
        avatar = None
        avatar_el = lexbor.css_first(
            "img#getAvatar, #avatarPicture img, img.avatarTrigger, .profilePicBorder img, .avatar img, img.userAvatar"
        )
        if avatar_el:
            avatar = avatar_el.attributes.get("src") or avatar_el.attributes.get("data-src")
        if not avatar:
            logger.warning(f"Failed to extract avatar for user at {url}")

        # 5. Cover
        cover = None
        cover_el = lexbor.css_first(
            "img#coverPictureDefault, #coverPicture img, .coverImage img, .coverWrapper img, .coverDefault img"
        )
        if cover_el:
            cover = cover_el.attributes.get("src") or cover_el.attributes.get("data-src")

        # 6. Badges
        hc = lexbor.css_first(
            "section#topProfileHeader, div.coverImage, div.topProfileHeader, "
            "div.profileHeader, div.nameSubscribe, .titleWrapper"
        )
        is_verified = False
        is_premium = False
        is_award_winner = False
        if hc:
            is_verified = bool(hc.css_first(
                ".verifiedPornstar, .verified-icon, .verifiedIcon, .verified-user, [data-title*='Verified']"
            ))
            is_premium = bool(hc.css_first(
                ".premiumIcon, .ph-icon-badge-premium, [data-title*='Premium'], .badge-premium"
            ))
            is_award_winner = bool(hc.css_first(
                ".trophyChannel, .trophyPornStar, [data-title*='Award']"
            ))
        if not is_verified:
            is_verified = bool(lexbor.css_first(
                ".titleWrapper .verifiedPornstar, .titleWrapper .verifiedIcon, div.name .verifiedPornstar"
            ))

        # 7. Bio & About
        bio_node = lexbor.css_first(
            "div.content.js-headerContent.js-highestChild div[itemprop], "
            "div.biographyAbout div[itemprop='description'], div[itemprop='description']"
        )
        bio = bio_node.text(strip=True) if bio_node else None

        about = None
        about_sec = lexbor.css_first("section.aboutMeSection")
        if about_sec:
            divs = [d for d in about_sec.css("div") if "title" not in (d.attributes.get("class") or "")]
            if divs:
                about = divs[0].text(strip=True)
        if not about:
            p = lexbor.css_first("p.aboutMeText")
            if p:
                about = p.text(strip=True)

        # 8. Info Table
        info = {}
        for piece in lexbor.css("div.infoPiece"):
            spans = piece.css("span")
            if len(spans) >= 2:
                info[spans[0].text(strip=True)] = spans[1].text(strip=True)

        # 9. Ranks
        ranks = {}
        for box in lexbor.css(".rankingInfo .infoBox"):
            span_rank = box.css_first("span.big, span.ranking")
            title_el = box.css_first(".title")
            if span_rank and title_el:
                ranks[title_el.text(strip=True)] = span_rank.text(strip=True)

        # 10. Subscribers & Views
        subscribers = None
        subscribers_count = None
        sub_box = lexbor.css_first(".infoBox[data-title*='Subscribers'], .infoBox.subscribers, [data-title*='Subscribers']")
        if sub_box:
            dt = sub_box.attributes.get("data-title") or ""
            m_s = re.search(r"Subscribers:\s*([\d,]+)", dt)
            if m_s:
                subscribers = m_s.group(1)
                subscribers_count = int(subscribers.replace(",", ""))
        if not subscribers:
            for li in lexbor.css(".subViewsInfoContainer li"):
                txt = li.text(strip=True).lower()
                if "subscriber" in txt:
                    num_el = li.css_first("span.number")
                    if num_el:
                        subscribers = num_el.text(strip=True)
                        clean = re.sub(r"[^\d]", "", subscribers)
                        if clean:
                            subscribers_count = int(clean)
                        break
        if not subscribers and sub_box:
            big_el = sub_box.css_first("span.big")
            if big_el:
                subscribers = big_el.text(strip=True)

        # 11. Video Views
        video_views = None
        video_views_count = None
        vv_box = lexbor.css_first(".infoBox[data-title*='Video views'], .infoBox.videoViews, [data-title*='Video views']")
        if vv_box:
            dt = vv_box.attributes.get("data-title") or ""
            m_v = re.search(r"Video views:\s*([\d,]+)", dt)
            if m_v:
                video_views = m_v.group(1)
                video_views_count = int(video_views.replace(",", ""))
        if not video_views and "Video Views:" in info:
            video_views = info["Video Views:"]
            clean = re.sub(r"[^\d]", "", video_views)
            if clean:
                video_views_count = int(clean)
        if not video_views and vv_box:
            big_el = vv_box.css_first("span.big")
            if big_el:
                video_views = big_el.text(strip=True)

        # 12. Profile Views
        profile_views = None
        profile_views_count = None
        for pv_key in ("Profile Views:", "Pornstar Profile Views:", "Profile Views", "Pornstar Profile Views"):
            if pv_key in info:
                profile_views = info[pv_key]
                clean = re.sub(r"[^\d]", "", profile_views)
                if clean:
                    profile_views_count = int(clean)
                break

        # 13. Videos Watched
        videos_watched = None
        videos_watched_count = None
        for vw_key in ("Videos Watched:", "Videos Watched"):
            if vw_key in info:
                videos_watched = info[vw_key]
                clean = re.sub(r"[^\d]", "", videos_watched)
                if clean:
                    videos_watched_count = int(clean)
                break
        if not videos_watched:
            for li in lexbor.css(".subViewsInfoContainer li"):
                txt = li.text(strip=True).lower()
                if "watched" in txt:
                    num_el = li.css_first("span.number")
                    if num_el:
                        videos_watched = num_el.text(strip=True)
                        clean = re.sub(r"[^\d]", "", videos_watched)
                        if clean:
                            videos_watched_count = int(clean)
                        break

        # 14. Social Links
        social_links = {}
        for a_tag in lexbor.css("ul.socialList li a, .socialsColumn li a"):
            href = a_tag.attributes.get("href") or ""
            if not href or href.startswith("#"):
                continue
            st = a_tag.css_first(".socialText")
            platform = st.text(strip=True) if st else None
            if not platform:
                platform = a_tag.attributes.get("title") or a_tag.attributes.get("data-title")

            href_lower = href.lower()
            if "twitter.com" in href_lower or "x.com" in href_lower:
                platform = "Twitter"
            elif "instagram.com" in href_lower:
                platform = "Instagram"
            elif "tiktok.com" in href_lower:
                platform = "TikTok"
            elif "reddit.com" in href_lower:
                platform = "Reddit"
            elif "snapchat.com" in href_lower:
                platform = "Snapchat"
            elif "youtube.com" in href_lower:
                platform = "YouTube"
            elif "onlyfans.com" in href_lower:
                platform = "OnlyFans"
            elif not platform:
                icon = a_tag.css_first("i, svg")
                cls = (icon.attributes.get("class") if icon else "") or ""
                platform = cls.split()[-1] if cls else "Website"

            if platform not in social_links:
                social_links[platform] = href

        # 15. External Link
        external_link = None
        external_link_text = None
        ext_btn = lexbor.css_first(".moreOfMeWrapper a.externalLinkButton, a.externalLinkButton, a.moreOfMe, a.btnMoreOfMe")
        if ext_btn:
            external_link = ext_btn.attributes.get("href")
            txt_el = ext_btn.css_first(".text") or ext_btn
            external_link_text = txt_el.text(strip=True) if txt_el else None

        # 16. Info field helpers
        def _get_info(key_name: str) -> str | None:
            for k, v in info.items():
                if k.rstrip(":").strip().lower() == key_name.lower():
                    return v
            return None

        return {
            "name": name,
            "user_id": user_id,
            "token": token,
            "avatar": avatar,
            "cover": cover,
            "is_verified": is_verified,
            "is_premium": is_premium,
            "is_award_winner": is_award_winner,
            "bio": bio,
            "about": about,
            "info": info,
            "ranks": ranks,
            "subscribers": subscribers,
            "subscribers_count": subscribers_count,
            "video_views": video_views,
            "video_views_count": video_views_count,
            "profile_views": profile_views,
            "profile_views_count": profile_views_count,
            "videos_watched": videos_watched,
            "videos_watched_count": videos_watched_count,
            "social_links": social_links,
            "external_link": external_link,
            "external_link_text": external_link_text,
            "gender": _get_info("Gender"),
            "birth_place": _get_info("Birth Place"),
            "relationship_status": _get_info("Relationship status"),
            "interested_in": _get_info("Interested in"),
            "height": _get_info("Height"),
            "weight": _get_info("Weight"),
            "ethnicity": _get_info("Ethnicity"),
            "hair_color": _get_info("Hair Color"),
            "eye_color": _get_info("Eye Color"),
            "measurements": _get_info("Measurements"),
            "fake_boobs": _get_info("Fake Boobs"),
            "tattoos": _get_info("Tattoos"),
            "piercings": _get_info("Piercings"),
            "career_status": _get_info("Career Status"),
            "career_start_end": _get_info("Career Start and End"),
            "star_sign": _get_info("Star Sign"),
        }

    def get_videos(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        base_url = profile_base_url(self.resolved_url or self.url)
        page_urls = [update_url_query(append_url_path(base_url, "videos"), page=page) for page in range(1, pages + 1)]
        return _scrape_stream(
            core=self.core, constructor=Video, target_page_urls=page_urls,
            item_extractor=partial(extractor_model_videos, base_url=base_url), iterator_config=iterator_config,
        )

    def get_uploads(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        base_url = profile_base_url(self.resolved_url or self.url)
        page_urls = [update_url_query(append_url_path(base_url, "videos/upload"), page=page) for page in range(1, pages + 1)]
        return _scrape_stream(
            core=self.core, constructor=Video, target_page_urls=page_urls,
            item_extractor=partial(extractor_model_uploads, base_url=base_url), iterator_config=iterator_config,
        )

    def get_gifs(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[GIF], None]:
        base_url = profile_base_url(self.resolved_url or self.url)
        page_urls = [update_url_query(append_url_path(base_url, "gifs/video"), page=page) for page in range(1, pages + 1)]
        return _scrape_stream(
            core=self.core, constructor=GIF, target_page_urls=page_urls,
            item_extractor=partial(extractor_gifs, base_url=base_url), iterator_config=iterator_config,
        )


@dataclass(kw_only=True, slots=True)
class Pornstar(UserHelper):
    pass


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
    album_id: str | None = media_field("html")
    title: str | None = media_field("html")
    author_name: str | None = media_field("html")
    author_link: str | None = media_field("html")
    author_id: str | None = media_field("html")
    avatar: str | None = media_field("html")
    author_avatar: str | None = media_field("html")
    is_verified: bool | None = media_field("html")
    rating_percentage: str | None = media_field("html")
    votes: str | None = media_field("html")
    vote_count: int | None = media_field("html")
    views: str | None = media_field("html")
    views_count: int | None = media_field("html")
    publish_date: str | None = media_field("html")
    segment: str | None = media_field("html")
    tags: dict[str, str] | None = media_field("html")
    token: str | None = media_field("html")
    total_pages: int | None = media_field("html")
    photos_count: int | None = media_field("html")
    photos: list[dict[str, Any]] | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}

    async def _load_html(self) -> dict[str, object]:
        logger.debug(f"Fetching HTML for Album at {self.url}")
        html_content = await get_html_content(core=self.core, url=self.url, owner=self)
        return await asyncio.to_thread(self._extract_html, html_content, self.url)

    @staticmethod
    def _extract_html(html_content: str, url: str = "") -> dict[str, Any]:
        logger.debug("Extracting info from Album HTML...")
        lexbor = LexborHTMLParser(html_content)

        # 1. album_id
        album_id = None
        widget_match = re.search(r"var\s+WIDGET_ALBUM_UPPER_INFO\s*=\s*({.*?});", html_content, re.DOTALL)
        widget_data = {}
        if widget_match:
            try:
                widget_data = json.loads(widget_match.group(1))
                if "securityId" in widget_data:
                    album_id = str(widget_data["securityId"])
            except Exception:
                logger.warning("Failed to parse album widget metadata", exc_info=True)
        if not album_id:
            m = re.search(r"var\s+origin(?:Url|Part)\s*=\s*['\"][^'\"]*album/(\d+)", html_content)
            if m:
                album_id = m.group(1)
        if not album_id and url:
            m = re.search(r"/album/(\d+)", url)
            if m:
                album_id = m.group(1)
        if not album_id:
            m = re.search(r"/album/(\d+)", html_content)
            if m:
                album_id = m.group(1)
        if not album_id:
            m = re.search(r"photo_(\d+)_\d+", html_content)
            if m:
                album_id = m.group(1)
        if not album_id:
            logger.warning(f"Failed to extract album_id for Album at {url}")

        # 2. title
        title = None
        title_el = lexbor.css_first("h1.photoAlbumTitleV2, h1.photoAlbumTitle, h1")
        if title_el:
            span = title_el.css_first("span")
            span_text = span.text() if span else ""
            title = title_el.text().replace(span_text, "").strip()
        if not title:
            title_tag = lexbor.css_first("title")
            if title_tag:
                t = title_tag.text(strip=True)
                title = re.split(r"\s*-\s*|'s\s+Albums", t)[0].strip()
        if not title:
            og_title = lexbor.css_first('meta[property="og:title"], meta[name="twitter:title"]')
            if og_title:
                title = og_title.attributes.get("content")
        if not title:
            logger.warning(f"Failed to extract title for Album at {url}")

        # 3. token
        token = widget_data.get("token") if isinstance(widget_data, dict) else None
        if not token:
            top_body = re.search(r"var\s+TOP_BODY\s*=\s*({.*?});", html_content, re.DOTALL)
            if top_body:
                try:
                    top_data = json.loads(top_body.group(1))
                    token = top_data.get("token")
                except Exception:
                    logger.warning("Failed to parse album token metadata", exc_info=True)
        if not token:
            m = re.search(r"['\"]?token['\"]?\s*:\s*['\"]([^'\"]+)['\"]", html_content)
            if m:
                token = m.group(1)
        if not token:
            logger.warning(f"Failed to extract token for Album at {url}")

        # 4. author_name & author_link
        author_name = None
        author_link = None
        user_a = (
            lexbor.css_first("#profileBoxPhotoAlbum #userNameText a.usernameLink")
            or lexbor.css_first("#profileBoxPhotoAlbum #userNameText a")
            or lexbor.css_first("#profilePA_Info #userNameText a")
            or lexbor.css_first("#userNameText a.usernameLink")
            or lexbor.css_first("#userNameText a")
        )
        if user_a:
            author_name = user_a.text(strip=True)
            href = user_a.attributes.get("href")
            if href:
                author_link = f"https://www.pornhub.com{href}" if href.startswith("/") else href

        if not author_link:
            user_container = lexbor.css_first("#profileBoxPhotoAlbum a.userLink, .userLinkContainer a.userLink")
            if user_container and user_container.attributes.get("href"):
                href = user_container.attributes.get("href")
                author_link = f"https://www.pornhub.com{href}" if href.startswith("/") else href
                if not author_name:
                    author_name = user_container.attributes.get("data-title") or user_container.attributes.get("title")

        if not author_link:
            albums_link = lexbor.css_first("#usernameLinkText a")
            if albums_link and albums_link.attributes.get("href"):
                href = albums_link.attributes.get("href")
                href = re.sub(r"/photos/?$", "", href)
                author_link = f"https://www.pornhub.com{href}" if href.startswith("/") else href

        if not author_name:
            av = lexbor.css_first(".photoPfileContainer img.avatar, img.avatarTrigger")
            if av:
                author_name = av.attributes.get("data-title") or av.attributes.get("alt")
        if not author_name:
            title_tag = lexbor.css_first("title")
            if title_tag:
                m = re.search(r"-\s*(.+?)'s\s+Albums", title_tag.text(strip=True))
                if m:
                    author_name = m.group(1).strip()

        if not author_name:
            logger.warning(f"Failed to extract author_name for Album at {url}")
        if not author_link:
            logger.warning(f"Failed to extract author_link for Album at {url}")

        # 5. author_id
        author_id = None
        userid_el = (
            lexbor.css_first("#profileBoxPhotoAlbum [data-userid]")
            or lexbor.css_first("div.userLinkContainer [data-userid]")
            or lexbor.css_first("div.usernameWrap[data-userid]")
            or lexbor.css_first("[data-userid]")
        )
        if userid_el:
            author_id = userid_el.attributes.get("data-userid")
        if not author_id:
            m = re.search(r'data-userid=["\'](\d+)["\']', html_content)
            if m:
                author_id = m.group(1)
        if not author_id:
            logger.warning(f"Failed to extract author_id for Album at {url}")

        # 6. avatar / author_avatar
        avatar = None
        av_el = (
            lexbor.css_first("div.photoPfileContainer img.avatar")
            or lexbor.css_first("#profileBoxPhotoAlbum img.avatar")
            or lexbor.css_first("img.avatarTrigger")
            or lexbor.css_first("img.avatar")
        )
        if av_el:
            avatar = av_el.attributes.get("src") or av_el.attributes.get("data-src")
        if not avatar:
            logger.warning(f"Failed to extract avatar for Album at {url}")

        # 7. is_verified
        is_verified = bool(
            lexbor.css_first("#profilePA_Info .verified-icon")
            or lexbor.css_first("#profileBoxPhotoAlbum .verified-icon")
            or lexbor.css_first(".verified-user")
            or lexbor.css_first("span.verified-icon")
        )

        # 8. rating_percentage
        rating_percentage = None
        rating_el = lexbor.css_first("div#ratingAlbumInfo span")
        if rating_el:
            rating_percentage = rating_el.text(strip=True)
        if not rating_percentage:
            rating_span = lexbor.css_first(".album-rating")
            if rating_span:
                rating_percentage = rating_span.text(strip=True)
        if not rating_percentage:
            m = re.search(r"(\d+%)", html_content)
            if m:
                rating_percentage = m.group(1)
        if not rating_percentage:
            logger.warning(f"Failed to extract rating_percentage for Album at {url}")

        # 9. votes & vote_count
        votes_el = lexbor.css_first("div#ratingAlbumInfo > div")
        votes = votes_el.text(strip=True) if votes_el else None
        vote_count = None
        if votes:
            m = re.search(r"\(([\d,]+)\s*votes?\)", votes)
            if m:
                vote_count = int(m.group(1).replace(",", ""))
        if vote_count is None:
            m = re.search(r"\(([\d,]+)\s*votes?\)", html_content)
            if m:
                vote_count = int(m.group(1).replace(",", ""))
        if not votes:
            if vote_count is not None:
                votes = f"({vote_count} votes)"
            else:
                logger.warning(f"Failed to extract votes for Album at {url}")

        # 10. views & views_count
        views = None
        views_el = lexbor.css_first("div#viewsPhotAlbumCounter")
        if views_el:
            views = views_el.text(strip=True)
        if not views:
            m = re.search(r"([\d,]+\s*views)", html_content, re.IGNORECASE)
            if m:
                views = m.group(1)
        views_count = None
        if views:
            m = re.search(r"([\d,]+)\s*views", views, re.IGNORECASE)
            if m:
                views_count = int(m.group(1).replace(",", ""))
        if not views:
            logger.warning(f"Failed to extract views for Album at {url}")

        # 11. publish_date
        publish_date = None
        time_block = lexbor.css_first("div#timeBlockContent") or lexbor.css_first("div#photoAlbumTimeBox")
        if time_block:
            m = re.search(r"([0-9]+\s+[a-zA-Z]+\s+ago)", time_block.text(strip=True))
            if m:
                publish_date = m.group(1)
            elif time_divs := time_block.css("div"):
                for d in reversed(time_divs):
                    txt = d.text(strip=True)
                    if txt and txt != "Added" and "Added" not in txt:
                        publish_date = txt
                        break
        if not publish_date:
            m = re.search(r"Added\s*([0-9]+\s+[a-zA-Z]+\s+ago)", html_content)
            if m:
                publish_date = m.group(1)
        if not publish_date:
            logger.warning(f"Failed to extract publish_date for Album at {url}")

        # 12. segment
        segment = None
        seg_el = lexbor.css_first("div#segmentCont, div#photoSegmentBox")
        if seg_el:
            m = re.search(r"Segment:\s*([^\n\r<]+)", seg_el.text())
            if m:
                segment = m.group(1).replace("Add a Suggestion", "").strip()

        # 13. tags
        tags = {}
        tag_container = lexbor.css_first("#photoTagsBox") or lexbor.css_first("#tagsRowContainer")
        if not tag_container:
            tag_container = lexbor.css_first("div.photoBoxContContainer")
            if tag_container and not tag_container.css("div.tagContainer"):
                tag_container = None
        if tag_container:
            for a in tag_container.css("a"):
                name = a.text(strip=True)
                href = a.attributes.get("href", "")
                if href and not href.startswith("http"):
                    href = f"https://www.pornhub.com{href}"
                if name:
                    tags[name] = href
        if not tags:
            for a in lexbor.css("div.tagContainer a"):
                name = a.text(strip=True)
                href = a.attributes.get("href", "")
                if href and not href.startswith("http"):
                    href = f"https://www.pornhub.com{href}"
                if name:
                    tags[name] = href

        # 14. total_pages
        pages = [
            int(a.text(strip=True))
            for a in lexbor.css("div.pagination3 li.page_number a, div.pagination li a, .page_number a")
            if a.text(strip=True).isdigit()
        ]
        total_pages = max(pages) if pages else 1

        # 15. photos & photos_count
        photos = Album._parse_photos(html_content)
        photos_count = len(photos)

        return {
            "album_id": album_id,
            "title": title,
            "author_name": author_name,
            "author_link": author_link,
            "author_id": author_id,
            "avatar": avatar,
            "author_avatar": avatar,
            "is_verified": is_verified,
            "rating_percentage": rating_percentage,
            "votes": votes,
            "vote_count": vote_count,
            "views": views,
            "views_count": views_count,
            "publish_date": publish_date,
            "segment": segment,
            "tags": tags,
            "token": token,
            "total_pages": total_pages,
            "photos_count": photos_count,
            "photos": photos,
        }

    async def get_author(self, load_html: bool = True) -> User | Pornstar | Channel | Model | None:
        author_link = await self.get_field("author_link")
        if not author_link:
            logger.warning(f"No author_link found for Album at {self.url}")
            return None
        author_cls: type[User | Pornstar | Channel | Model] = User
        if "/channels/" in author_link:
            author_cls = Channel
        elif "/model/" in author_link:
            author_cls = Model
        elif "/pornstar/" in author_link:
            author_cls = Pornstar
        author_obj = author_cls(url=author_link, core=self.core)
        if load_html:
            await author_obj.load_sources("html")
        return author_obj

    @property
    async def author(self) -> User | Pornstar | Channel | Model | None:
        return await self.get_author(load_html=True)

    @staticmethod
    def _parse_photos(html_content: str) -> list[dict[str, Any]]:
        photos: list[dict[str, Any]] = []
        lexbor = LexborHTMLParser(html_content)
        main_ul = lexbor.css_first("ul.photosAlbumsListing")
        if not main_ul:
            return photos
        items = main_ul.css("li") or main_ul.css("div.photoAlbumListBlock")
        for li_tag in items:
            block = li_tag.css_first("div.photoAlbumListBlock") or li_tag
            a = li_tag.css_first("a")
            href = a.attributes.get("href") if a else ""
            link = f"https://www.pornhub.com{href}" if href and not href.startswith("http") else (href or "")

            li_id = li_tag.attributes.get("id", "")
            photo_id = None
            if li_id and "photo_" in li_id:
                photo_id = li_id.split("_")[-1]
            elif href:
                m = re.search(r"/photo/(\d+)", href)
                if m:
                    photo_id = m.group(1)

            download_url = block.attributes.get("data-bkg") or block.attributes.get("data-image")
            if not download_url:
                style = block.attributes.get("style", "")
                m = re.search(r'url\(["\']?(https?://[^"\')]+)', style)
                if m:
                    download_url = m.group(1)

            rating_span = li_tag.css_first("span.album-rating")
            views_span = li_tag.css_first("span.album-views")
            spans = li_tag.css("span")

            rating = rating_span.text(strip=True) if rating_span else (spans[0].text(strip=True) if len(spans) > 0 else "")
            views = views_span.text(strip=True) if views_span else (spans[1].text(strip=True) if len(spans) > 1 else "")

            photos.append({
                "photo_id": photo_id,
                "url": link,
                "download_url": download_url,
                "rating": rating,
                "views": views,
            })
        return photos

    async def get_photos(self, pages: int = 1) -> AsyncGenerator[dict[str, Any], None]:
        logger.info(f"Fetching photos for Album at {self.url} (pages: {pages})")
        base_url = self.resolved_url or self.url
        page_urls = [update_url_query(base_url, page=page) for page in range(1, pages + 1)]
        html_contents = await asyncio.gather(*(get_html_content(core=self.core, url=url, owner=self) for url in page_urls))
        for html in html_contents:
            for photo_data in self._parse_photos(html):
                yield photo_data

    async def download_photo(self, url: str, path: str) -> bool:
        with log_context(self, url):
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
    media_definitions: list[dict] | None = media_field("html")
    comment_count: str | None = media_field("html")
    avatar: str | None = media_field("html")
    author_name: str | None = media_field("html")
    video_url: str | None = media_field("html")
    m3u8_base_url: str | None = media_field("html")
    duration: int | None = media_field("html")
    categories: list[str] | None = media_field("html")
    tags: list[str] | None = media_field("html")
    pills_data: list[dict] | None = media_field("html")
    is_verified: bool | None = media_field("html")
    is_premium: bool | None = media_field("html")
    is_award: bool | None = media_field("html")
    like_count: int | None = media_field("html")
    dislike_count: int | None = media_field("html")
    like_info: str | None = media_field("html")
    favorite_info: str | None = media_field("html")
    token: str | None = media_field("html")
    author_id: str | None = media_field("html")
    author_type: str | None = media_field("html")
    external_link: str | None = media_field("html")
    external_link_text: str | None = media_field("html")
    large_preview_url: str | None = media_field("html")
    shortie_url: str | None = media_field("html")
    meta_title: str | None = media_field("html")
    meta_description: str | None = media_field("html")
    direct_video_url: str | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}

    async def _load_html(self) -> dict[str, object]:
        logger.debug(f"Fetching HTML for Short at {self.url}")
        html_content = await get_html_content(core=self.core, url=self.url, owner=self)
        return await asyncio.to_thread(self._extract_html, html_content)

    def _extract_html(self, html_content: str | None = None, url: str | None = None) -> dict:
        if isinstance(self, str):
            html_content = self
            url = url or "unknown"
        else:
            url = url or getattr(self, "url", "unknown")

        logger.debug(f"Extracting metadata from Short HTML at {url}...")
        html_str = html_content or ""
        parser = LexborHTMLParser(html_str)

        # 1. Parse JSON_SHORTIES from <script>
        parsed_shorties: list[dict] = []
        if "JSON_SHORTIES" in html_str:
            idx = html_str.find("JSON_SHORTIES")
            if idx != -1:
                b_idx = html_str.find("[", idx)
                if b_idx != -1:
                    try:
                        parsed = chompjs.parse_js_object(html_str[b_idx:])
                        if isinstance(parsed, list):
                            parsed_shorties = [s for s in parsed if isinstance(s, dict)]
                    except Exception as e:
                        logger.debug(f"Failed to parse JSON_SHORTIES directly from bracket: {e}", exc_info=True)
                        m = re.search(r'JSON_SHORTIES\s*=\s*insertAfterNthPosition\((.*?), prerollObject', html_str, re.DOTALL)
                        if m:
                            try:
                                parsed = chompjs.parse_js_object(m.group(1))
                                if isinstance(parsed, list):
                                    parsed_shorties = [s for s in parsed if isinstance(s, dict)]
                            except Exception:
                                logger.warning("Failed to parse short video metadata", exc_info=True)

        # 2. Match target vkey / videoId from URL if present
        requested_vkey = None
        if url and url != "unknown":
            if m := re.search(r'/shorties/([a-zA-Z0-9_]+)', url):
                requested_vkey = m.group(1)
            elif m := re.search(r'[?&]v(?:iew)?key=([a-zA-Z0-9_]+)', url):
                requested_vkey = m.group(1)

        metadata: dict = {}
        if parsed_shorties:
            if requested_vkey:
                for item in parsed_shorties:
                    if item.get("vkey") == requested_vkey or str(item.get("videoId")) == requested_vkey:
                        metadata = item
                        break
            if not metadata:
                for item in parsed_shorties:
                    if item.get("vkey") or item.get("videoTitle"):
                        metadata = item
                        break

        # 3. Locate corresponding slide in DOM for fallbacks
        target_slide = None
        if requested_vkey:
            target_slide = parser.css_first(f'.slideWrapper [data-vkey="{requested_vkey}"]')
            if target_slide:
                parent = target_slide.parent
                while parent and 'slideWrapper' not in parent.attributes.get('class', ''):
                    parent = parent.parent
                if parent:
                    target_slide = parent
        if not target_slide:
            target_slide = parser.css_first('.slideWrapper.active, .slideWrapper:first-child, .slideWrapper')

        # Title
        title = metadata.get("videoTitle")
        if not title and target_slide:
            if h2 := target_slide.css_first('h2.description, .description'):
                title = h2.text(strip=True)
            elif img := target_slide.css_first('.mgp_videoPoster img[alt]'):
                title = img.attributes.get('alt')
        if not title:
            if meta_t := parser.css_first('meta[property="og:title"], meta[name="twitter:title"]'):
                title = meta_t.attributes.get('content')
        if not title and url and url != "unknown":
            slug = url.rstrip("/").split("/")[-1]
            if "-" in slug and not slug.startswith("ph"):
                title = slug.replace("-", " ").title()
        if not title:
            logger.warning(f"Failed to extract title for Short at {url}")

        # Video Key & ID
        video_key = metadata.get("vkey")
        if not video_key and target_slide:
            if el := target_slide.css_first('[data-vkey]'):
                video_key = el.attributes.get('data-vkey')
            elif player := target_slide.css_first('.fullScreenVideoPlayer[id]'):
                parts = player.attributes.get('id', '').split('_')
                if len(parts) >= 2:
                    video_key = parts[1]
        if not video_key and requested_vkey:
            video_key = requested_vkey

        video_id = str(metadata["videoId"]) if metadata.get("videoId") is not None else None
        if not video_id and target_slide:
            if flag_btn := target_slide.css_first('[data-item-id]'):
                val = flag_btn.attributes.get('data-item-id')
                if val:
                    video_id = val

        # Author info
        author_name = metadata.get("name")
        if not author_name and target_slide:
            if u_el := target_slide.css_first('span.userTitle, .userTitle'):
                author_name = u_el.text(strip=True)
            elif u_img := target_slide.css_first('.userAvatar img[alt]'):
                author_name = u_img.attributes.get('alt')

        author_link = metadata.get("profileUrl")
        if not author_link and target_slide:
            if a_link := target_slide.css_first('a.videoHref, a[data-label="view_full_profile"]'):
                href = a_link.attributes.get('href')
                if href:
                    author_link = f"https://www.pornhub.com{href}" if href.startswith("/") else href

        author_id = str(metadata["entityId"]) if metadata.get("entityId") is not None else None
        if not author_id and target_slide:
            if u_btn := target_slide.css_first('[data-user-id]'):
                author_id = u_btn.attributes.get('data-user-id')

        author_type = metadata.get("entityType")

        avatar = metadata.get("avatar")
        if not avatar and target_slide:
            if av_img := target_slide.css_first('.userAvatar img[src]'):
                avatar = av_img.attributes.get('src')

        # Likes, Dislikes, Favorites
        like_count = metadata.get("likeNumber")
        dislike_count = metadata.get("dislikeNumber")
        like_info = metadata.get("likeInfo")
        favorite_info = metadata.get("favoriteInfo")

        if like_count is None and target_slide:
            if like_div := target_slide.css_first('div.like[data-number]'):
                raw = like_div.attributes.get('data-number')
                if raw and raw.isdigit():
                    like_count = int(raw)

        if dislike_count is None and target_slide:
            if unlike_div := target_slide.css_first('div.unlike[data-number]'):
                raw = unlike_div.attributes.get('data-number')
                if raw and raw.isdigit():
                    dislike_count = int(raw)

        if not like_info and target_slide:
            if li_el := target_slide.css_first('.likeInfo, .infoTextSubIcon'):
                like_info = li_el.text(strip=True)

        likes = str(like_count) if like_count is not None else like_info
        dislikes = str(dislike_count) if dislike_count is not None else None
        favorites = favorite_info

        # HD & Badges
        is_hd = bool(metadata.get("isHD"))
        badges = metadata.get("badges") or {}
        is_verified = badges.get("verified") if "verified" in badges else None
        if is_verified is None and target_slide:
            is_verified = bool(target_slide.css_first('span.verified-icon:not(.displayNone)'))
        is_premium = badges.get("premium") if "premium" in badges else None
        is_award = badges.get("award") if "award" in badges else None

        # Embed URL
        raw_embed = metadata.get("embedUrl")
        embed_url = html.unescape(raw_embed) if raw_embed else None
        if not embed_url and video_key:
            embed_url = f'<iframe src="https://www.pornhub.com/embed/{video_key}" frameborder="0" width="390" height="695" scrolling="no" allowfullscreen></iframe>'

        # Thumbnail & Large preview
        thumbnail = metadata.get("imageUrl")
        large_preview_url = metadata.get("largePreviewUrl")
        if not thumbnail and target_slide:
            if poster_img := target_slide.css_first('.mgp_videoPoster img[src]'):
                thumbnail = poster_img.attributes.get('src')
        if not thumbnail:
            if ns_img := parser.css_first('noscript #player img, noscript img'):
                thumbnail = ns_img.attributes.get('src')
        if not thumbnail and large_preview_url:
            thumbnail = large_preview_url

        # Media definitions & M3U8 & Direct video
        media_definitions = metadata.get("mediaDefinitions")
        m3u8_base_url = build_m3u8_master(media_definitions) if media_definitions else None

        direct_video_url = None
        if media_definitions:
            for md in media_definitions:
                if isinstance(md, dict) and md.get("format") == "mp4" and md.get("videoUrl"):
                    direct_video_url = md.get("videoUrl")
                    break

        # Comments
        comment_count = str(metadata["commentCount"]) if metadata.get("commentCount") is not None else None

        # Video URL & Shortie URL
        video_url = metadata.get("linkUrl")
        if not video_url and video_key:
            video_url = f"https://www.pornhub.com/view_video.php?viewkey={video_key}"

        shortie_url = metadata.get("shortieUrl") or metadata.get("uniqueUrl")
        if not shortie_url and video_key:
            shortie_url = f"https://www.pornhub.com/shorties/{video_key}"

        # Duration
        duration = metadata.get("trackingTimeWatched", {}).get("video_duration")
        if duration is None and target_slide:
            if dur_el := target_slide.css_first('span.mgp_duration, .mgp_duration'):
                txt = dur_el.text(strip=True)
                parts = txt.split(":")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    duration = int(parts[0]) * 60 + int(parts[1])

        # Categories & Tags from pillsData
        pills_data = metadata.get("pillsData")
        categories = None
        tags = None
        if pills_data and isinstance(pills_data, list):
            cats = [p["name"] for p in pills_data if isinstance(p, dict) and p.get("type") == "category" and p.get("name")]
            if cats:
                categories = cats
            tgs = [p["name"] for p in pills_data if isinstance(p, dict) and p.get("type") == "tag" and p.get("name")]
            if tgs:
                tags = tgs

        # External link
        external_link = None
        external_link_text = None
        if ext_dict := metadata.get("externalLink"):
            if isinstance(ext_dict, dict):
                external_link = ext_dict.get("url")
                external_link_text = ext_dict.get("text")
        if not external_link and target_slide:
            if ext_btn := target_slide.css_first('a.externalLinkButton[data-label]'):
                external_link = ext_btn.attributes.get('data-label')
                if txt_span := ext_btn.css_first('span.text'):
                    external_link_text = txt_span.text(strip=True)

        # Meta Title & Description
        meta_title = metadata.get("metaTitle")
        meta_description = metadata.get("metaDescription")

        # Token
        token = None
        if token_el := parser.css_first('div#shorties[data-token], div.profilePanel[data-token]'):
            token = token_el.attributes.get('data-token')
        if not token:
            if m := re.search(r'[?&]token=([a-zA-Z0-9_\-\.]+)', html_str):
                token = m.group(1)
        if not token:
            if m := REGEX_TOKEN.search(html_str):
                token = m.group(1)

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
            "m3u8_base_url": m3u8_base_url,
            "duration": duration,
            "categories": categories,
            "tags": tags,
            "pills_data": pills_data,
            "is_verified": is_verified,
            "is_premium": is_premium,
            "is_award": is_award,
            "like_count": like_count,
            "dislike_count": dislike_count,
            "like_info": like_info,
            "favorite_info": favorite_info,
            "token": token,
            "author_id": author_id,
            "author_type": author_type,
            "external_link": external_link,
            "external_link_text": external_link_text,
            "large_preview_url": large_preview_url,
            "shortie_url": shortie_url,
            "meta_title": meta_title,
            "meta_description": meta_description,
            "direct_video_url": direct_video_url,
        }

    async def get_author(self, load_html: bool = True) -> UserHelper | None:
        author_link = await self.get_field("author_link")
        if not author_link:
            logger.warning(f"No author_link found for Short at {self.url}")
            return None
        if "/model/" in author_link:
            star = Model(url=author_link, core=self.core)
        elif "/pornstar/" in author_link:
            star = Pornstar(url=author_link, core=self.core)
        else:
            star = User(url=author_link, core=self.core)
        if load_html:
            await star.load_sources("html")
        return star

    async def get_video(self, load_html: bool = False, load_api: bool = True) -> Video | None:
        video_url = await self.get_field("video_url")
        if not video_url:
            vkey = await self.get_field("video_key")
            if vkey:
                video_url = f"https://www.pornhub.com/view_video.php?viewkey={vkey}"
        if not video_url:
            logger.warning(f"No video_url found for Short at {self.url}")
            return None
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
    tag_names: list[str] | None = media_field("html")
    gif_id: str | None = media_field("html")
    token: str | None = media_field("html")
    mp4_url: str | None = media_field("html")
    webm_url: str | None = media_field("html")
    gif_url: str | None = media_field("html")
    votes_up: str | None = media_field("html")
    votes_down: str | None = media_field("html")
    source_video_title: str | None = media_field("html")
    source_video_timestamp: str | None = media_field("html")
    author_name: str | None = media_field("html")
    author_link: str | None = media_field("html")
    author_id: str | None = media_field("html")
    embed_url: str | None = media_field("html")
    description: str | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}

    async def _load_html(self) -> dict[str, object]:
        logger.debug(f"Fetching HTML for GIF at {self.url}")
        html_content = await get_html_content(core=self.core, url=self.url, owner=self)
        if "GIF is unavailable pending review." in html_content:
            raise GifPendingReview("The GIF is still pending a review and can't be downloaded yet...")
        if "This video has been disabled" in html_content:
            raise VideoDisabled("The Video has been disabled, I can not fetch any data from it.")
        return await asyncio.to_thread(self._extract_html, html_content)

    def _extract_html(self, html_content: str | None = None) -> dict:
        if isinstance(self, str):
            html_content = self
            url = "unknown"
        else:
            url = getattr(self, "url", "unknown")

        logger.debug(f"Extracting info from GIF HTML at {url}...")
        lexbor = LexborHTMLParser(html_content or "")

        # 1. Parse application/ld+json if present
        script: dict = {}
        script_node = lexbor.css_first('script[type="application/ld+json"]')
        if script_node and script_node.text():
            try:
                script = json.loads(script_node.text())
            except Exception:
                logger.warning("Failed to parse GIF JSON-LD metadata", exc_info=True)

        # Scope to main GIF containers to avoid matching header/footer navigation
        gif_wrap = lexbor.css_first("div#gifWrap")
        img_section = (gif_wrap.css_first("div#gifImageSection") if gif_wrap else None) or lexbor.css_first("div#gifImageSection")
        info_section = (gif_wrap.css_first("div#gifInfoSection") if gif_wrap else None) or lexbor.css_first("div#gifInfoSection")

        center_img = (
            (img_section.css_first("div#js-gifToWebm, .centerImage") if img_section else None)
            or lexbor.css_first("div#js-gifToWebm, .centerImage")
        )
        video_player = (
            (img_section.css_first("video#gifWebmPlayer, video") if img_section else None)
            or lexbor.css_first("video#gifWebmPlayer, video")
        )

        # 2. Media URLs (mp4_url, webm_url, gif_url, content_url)
        mp4_url = None
        if center_img:
            mp4_url = center_img.attributes.get("data-mp4") or center_img.attributes.get("data-fallback")
        if not mp4_url and video_player:
            source_mp4 = video_player.css_first('source[type*="mp4"], source.js-mp4')
            if source_mp4 and source_mp4.attributes.get("src"):
                mp4_url = source_mp4.attributes.get("src")
            elif video_player.attributes.get("data-mp4"):
                mp4_url = video_player.attributes.get("data-mp4")
        if not mp4_url:
            if meta_video := lexbor.css_first('meta[property="og:video"], meta[property="og:video:secure_url"]'):
                content = meta_video.attributes.get("content")
                if content and ".mp4" in content:
                    mp4_url = content

        webm_url = None
        if center_img:
            webm_url = center_img.attributes.get("data-webm")
        if not webm_url and video_player:
            source_webm = video_player.css_first('source[type*="webm"], source.js-webm')
            if source_webm and source_webm.attributes.get("src"):
                webm_url = source_webm.attributes.get("src")
            elif video_player.attributes.get("data-webm"):
                webm_url = video_player.attributes.get("data-webm")

        gif_url = center_img.attributes.get("data-gif") if center_img else None
        if not gif_url:
            if meta_tw_img := lexbor.css_first('meta[name="twitter:image"]'):
                content = meta_tw_img.attributes.get("content")
                if content and ".gif" in content:
                    gif_url = content

        content_url = script.get("contentUrl") or mp4_url or webm_url or gif_url
        if not content_url:
            logger.warning(f"Failed to extract content_url for GIF at {url}")

        # 3. Title
        title = None
        if h1 := (img_section.css_first("div.gifTitle h1, h1") if img_section else lexbor.css_first("div.gifTitle h1, h1")):
            title = h1.text(strip=True)
        if not title:
            title = script.get("name")
        if not title and center_img:
            title = center_img.attributes.get("data-gif-title")
        if not title:
            if meta_title := lexbor.css_first('meta[property="og:title"], meta[name="twitter:title"]'):
                title = meta_title.attributes.get("content")
        if not title:
            if title_tag := lexbor.css_first("title"):
                t = title_tag.text(strip=True).split(" - Pornhub")[0].split(" | Pornhub")[0].strip()
                if t:
                    title = t
        if not title and url and url != "unknown":
            slug = url.rstrip("/").split("/")[-1]
            title = slug.replace("-", " ").title()

        if not title:
            logger.warning(f"Failed to extract title for GIF at {url}")

        # 4. Thumbnail
        thumbnail = script.get("thumbnailUrl")
        if not thumbnail:
            if meta_og_img := lexbor.css_first('meta[property="og:image"]'):
                thumbnail = meta_og_img.attributes.get("content")
        if not thumbnail and video_player:
            poster = video_player.attributes.get("data-poster") or video_player.attributes.get("poster")
            if poster and not poster.startswith("data:image/gif;base64"):
                thumbnail = poster
        if not thumbnail:
            thumbnail = gif_url

        if not thumbnail:
            logger.warning(f"Failed to extract thumbnail for GIF at {url}")

        # 5. Publish date
        publish_date = script.get("uploadDate")
        if not publish_date and info_section:
            if added_el := info_section.css_first(".added"):
                publish_date = added_el.text(strip=True)
        if not publish_date:
            if added_el := lexbor.css_first("#gifInfoSection .added"):
                publish_date = added_el.text(strip=True)
        if not publish_date:
            if meta_date := lexbor.css_first('meta[property*="uploadDate"], meta[name*="uploadDate"]'):
                publish_date = meta_date.attributes.get("content")

        if not publish_date:
            logger.warning(f"Failed to extract publish_date for GIF at {url}")

        # 6. Views
        views = None
        views_container = img_section if img_section else lexbor
        views_el = views_container.css_first("li.gifViews strong, li.gifViews, .gifViews strong, .gifViews")
        if views_el:
            views = views_el.text(strip=True)

        # 7. Votes
        vote_container = img_section if img_section else lexbor
        vote_count_el = vote_container.css_first("div.voteCount span, span.voteCountNumber, div.voteCount")
        vote_count = (
            vote_count_el.text(strip=True).replace("(", "").replace(")", "").replace("votes", "").strip()
            if vote_count_el
            else None
        )

        vote_percentage_el = vote_container.css_first("div.votePercentage span, div.votePercentage")
        vote_percentage = (
            vote_percentage_el.text(strip=True).replace("%", "").strip()
            if vote_percentage_el
            else None
        )

        votes_up = None
        votes_down = None
        if input_up := vote_container.css_first("input#votesUp"):
            votes_up = input_up.attributes.get("value")
        if not votes_up:
            if btn_up := vote_container.css_first("button#voteUp[data-current]"):
                votes_up = btn_up.attributes.get("data-current")

        if input_down := vote_container.css_first("input#votesDown"):
            votes_down = input_down.attributes.get("value")
        if not votes_down:
            if btn_down := vote_container.css_first("button#voteDown[data-current]"):
                votes_down = btn_down.attributes.get("data-current")

        if vote_count is None and votes_up and votes_down and votes_up.isdigit() and votes_down.isdigit():
            vote_count = str(int(votes_up) + int(votes_down))

        # 8. Source Video
        source_video_url = None
        source_video_title = None
        source_video_timestamp = None

        if info_section:
            src_link_el = info_section.css_first('a[href*="view_video.php"], a[href*="viewkey="]')
            if src_link_el:
                href = src_link_el.attributes.get("href", "")
                source_video_url = f"https://www.pornhub.com{href}" if href.startswith("/") else href
                source_video_title = src_link_el.text(strip=True)

            if tstamp_el := info_section.css_first("a.tstamp, .tstamp"):
                source_video_timestamp = tstamp_el.text(strip=True)

        # 9. Tags
        tags: dict[str, str] = {}
        tag_list = (info_section.css_first("ul.tagList") if info_section else None) or lexbor.css_first("ul.tagList")
        if tag_list:
            for a in tag_list.css("li a"):
                txt = a.text(strip=True)
                if txt:
                    tags[txt] = a.attributes.get("href", "")
        tag_names = list(tags.keys())

        # 10. Author / Creator
        author_name = None
        author_link = None
        author_id = None

        if info_section:
            created_wrap = info_section.css_first(".usernameWrap")
            if created_wrap:
                author_id = created_wrap.attributes.get("data-userid")
                if a_author := created_wrap.css_first("a"):
                    author_name = a_author.text(strip=True) or a_author.attributes.get("title")
                    href = a_author.attributes.get("href", "")
                    if href:
                        author_link = f"https://www.pornhub.com{href}" if href.startswith("/") else href

        # 11. GIF ID and Token
        gif_id = None
        if gif_wrap and (gid := gif_wrap.attributes.get("data-gif-id")):
            gif_id = gid.removeprefix("gif")
        if not gif_id:
            if input_id := lexbor.css_first("input#currentId"):
                gif_id = input_id.attributes.get("value")
        if not gif_id and url:
            if m := re.search(r"/gif/(\d+)", url):
                gif_id = m.group(1)

        token = None
        if btn_vote := lexbor.css_first("button#voteUp[data-vote-url], button[data-vote-url]"):
            vote_url = btn_vote.attributes.get("data-vote-url", "")
            if m := re.search(r"[?&]token=([a-zA-Z0-9_\-\.]+)", vote_url):
                token = m.group(1)
        if not token:
            if form := lexbor.css_first('form#mainCommentForm[action*="token="]'):
                action = form.attributes.get("action", "")
                if m := re.search(r"[?&]token=([a-zA-Z0-9_\-\.]+)", action):
                    token = m.group(1)
        if not token and html_content:
            if m := REGEX_TOKEN.search(html_content):
                token = m.group(1)
        if not token and html_content:
            if m := re.search(r"[?&]token=([a-zA-Z0-9_\-\.]+)", html_content):
                token = m.group(1)

        # 12. Embed URL & Description
        embed_url = None
        if input_direct := lexbor.css_first("input#directlink"):
            embed_url = input_direct.attributes.get("value")
        if not embed_url and gif_id:
            embed_url = f"https://www.pornhub.com/embedgif/{gif_id}"

        description = script.get("description")
        if not description:
            if meta_desc := lexbor.css_first('meta[property="og:description"], meta[name="description"]'):
                description = meta_desc.attributes.get("content")
        if description:
            description = html.unescape(description)

        return {
            "title": title,
            "vote_count": vote_count,
            "vote_percentage": vote_percentage,
            "views": views,
            "publish_date": publish_date,
            "thumbnail": thumbnail,
            "content_url": content_url,
            "source_video_url": source_video_url,
            "tags": tags,
            "tag_names": tag_names,
            "gif_id": gif_id,
            "token": token,
            "mp4_url": mp4_url,
            "webm_url": webm_url,
            "gif_url": gif_url,
            "votes_up": votes_up,
            "votes_down": votes_down,
            "source_video_title": source_video_title,
            "source_video_timestamp": source_video_timestamp,
            "author_name": author_name,
            "author_link": author_link,
            "author_id": author_id,
            "embed_url": embed_url,
            "description": description,
        }

    async def get_author(self, load_html: bool = True) -> User | None:
        author_link = await self.get_field("author_link")
        if not author_link:
            logger.warning(f"No author_link found for GIF at {self.url}")
            return None
        user = User(core=self.core, url=author_link)
        if load_html:
            await user.load_sources("html")
        return user

    async def get_source_video(self, load_html: bool = True) -> Video | None:
        source_url = await self.get_field("source_video_url")
        if not source_url:
            logger.warning(f"No source_video_url found for GIF at {self.url}")
            return None
        video = Video(core=self.core, url=source_url)
        if load_html:
            await video.load_sources("html")
        return video

    @download_errors(DownloadFailed)
    async def download(self, configuration: DownloadConfigRAW) -> bool:
        await self.load_fields("title", "content_url")
        if not self.content_url:
            raise DownloadFailed(f"No content_url available for GIF at {self.url}")
        title = self.title or f"gif_{self.gif_id or 'unknown'}"
        logger.info(f"Downloading GIF {title} to {configuration.path}")
        config = prepare_download_config(configuration, strip_title(title))

        return await self.core.legacy_download(url=self.content_url, configuration=config)


@dataclass(kw_only=True, slots=True)
class Channel(BaseMedia):

    def __post_init__(self):
        profile_base_url(self.url, depth=2)

    url: str
    core: BaseCore
    name: str | None = media_field("html")
    is_award_winner: bool | None = media_field("html")
    is_verified: bool | None = media_field("html")
    is_content_partner: bool | None = media_field("html")
    video_views: str | None = media_field("html")
    subscribers: str | None = media_field("html")
    total_videos: str | None = media_field("html")
    rank: str | None = media_field("html")
    description: str | None = media_field("html")
    join_date: str | None = media_field("html")
    website: str | None = media_field("html")
    website_name: str | None = media_field("html")
    user_link: str | None = media_field("html")
    channel_id: str | None = media_field("html")
    token: str | None = media_field("html")
    avatar_url: str | None = media_field("html")
    cover_url: str | None = media_field("html")
    owner_name: str | None = media_field("html")
    badges: list[str] | None = media_field("html")
    pornstars: list[str] | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}

    async def _load_html(self) -> dict[str, object]:
        logger.debug(f"Fetching HTML for Channel at {self.url}")
        html_content = await get_html_content(core=self.core, url=self.url, owner=self)
        return await asyncio.to_thread(self._extract_html, html_content)

    def _extract_html(self, html_content: str | None = None) -> dict:
        if isinstance(self, str):
            html_content = self
            url = "unknown"
        else:
            url = getattr(self, "url", "unknown")

        logger.debug(f"Extracting info from Channel HTML at {url}...")
        lexbor = LexborHTMLParser(html_content or "")

        # 1. Badges & status flags
        badges: list[str] = []
        for b in lexbor.css(
            "div.titleWrapper span.userBadges, "
            "div.titleWrapper span.bg-channel-badge, "
            "div.titleWrapper span.trophyChannel, "
            "section#channelsProfile .titleWrapper [data-title]"
        ):
            title = b.attributes.get("data-title") or b.attributes.get("title")
            if title and title not in badges:
                badges.append(title)

        is_award_winner = (
            "Pornhub Awards Winner" in badges
            or bool(lexbor.css_first(".trophyChannel, .award-icon, [data-title*='Award Winner'], [data-title*='Awards Winner']"))
        )
        is_verified = (
            "Verified" in " ".join(badges)
            or bool(lexbor.css_first("div.titleWrapper .verified-icon, div.titleWrapper [data-title*='Verified']"))
        )
        is_content_partner = (
            "Content Partner" in badges
            or bool(lexbor.css_first("div.titleWrapper .producer-icon, div.titleWrapper [data-title*='Content Partner']"))
        )

        # 2. Name
        name = None
        name_el = lexbor.css_first(
            "section#channelsProfile .titleWrapper h1, div.title.floatLeft > h1, div.titleWrapper h1, div.title h1, h1"
        )
        if name_el:
            for badge in name_el.css("span, i"):
                badge.decompose()
            name = name_el.text(strip=True) or None
        if not name:
            if meta_title := lexbor.css_first("meta[property='og:title'], meta[name='twitter:title']"):
                content = meta_title.attributes.get("content", "")
                content = content.split("'s Porn Videos")[0].split(" | Pornhub")[0].strip()
                if content:
                    name = content
        if not name:
            if title_tag := lexbor.css_first("title"):
                content = title_tag.text(strip=True).split("'s Videos")[0].split(" - Pornhub")[0].strip()
                if content:
                    name = content
        if not name and url and url != "unknown":
            name = url.rstrip("/").split("/")[-1].replace("-", " ").title()

        if not name:
            logger.warning(f"Failed to extract name for Channel at {url}")

        # 3. Stats (video_views, subscribers, total_videos, rank)
        video_views = None
        subscribers = None
        total_videos = None
        rank = None

        stats_container = lexbor.css_first("div#stats")
        info_elements = (
            stats_container.css("div.info")
            if stats_container
            else lexbor.css("div#stats div.info, div.info.floatRight")
        )

        for info in info_elements:
            span = info.css_first("span")
            if not span:
                continue
            label = span.text(strip=True).upper()
            val = info.text(strip=True).replace(span.text(strip=True), "").strip()
            if "VIDEO VIEWS" in label:
                video_views = val
            elif "SUBSCRIBER" in label:
                subscribers = val
            elif "VIDEO" in label:
                total_videos = val
            elif "RANK" in label:
                rank = val

        # Fallback to index-based if missing labels
        if video_views is None and len(info_elements) > 0:
            video_views = info_elements[0].text(strip=True)
        if subscribers is None and len(info_elements) > 1:
            subscribers = info_elements[1].text(strip=True)
        if total_videos is None and len(info_elements) > 2:
            total_videos = info_elements[2].text(strip=True)
        if rank is None and len(info_elements) > 3:
            rank = info_elements[3].text(strip=True).replace("RANK", "").strip()

        if not video_views:
            logger.warning(f"Failed to extract video_views for Channel at {url}")
        if not subscribers:
            logger.warning(f"Failed to extract subscribers for Channel at {url}")
        if not total_videos:
            logger.warning(f"Failed to extract total_videos for Channel at {url}")

        # 4. Description, join_date, website, website_name, user_link, owner_name
        description = None
        join_date = None
        website = None
        website_name = None
        user_link = None
        owner_name = None

        cdesc_paragraphs = lexbor.css("div.cdescriptions p.joined, div.cdescriptions p, p.joined")
        for p in cdesc_paragraphs:
            headline_el = p.css_first(".channelInfoHeadlines")
            if not headline_el:
                if not description:
                    text = p.text(strip=True)
                    if text:
                        description = text
                continue

            headline = headline_el.text(strip=True).upper()
            if "JOINED" in headline:
                spans = p.css("span")
                if len(spans) > 1:
                    join_date = spans[1].text(strip=True)
                else:
                    join_date = p.text(strip=True).replace(headline_el.text(strip=True), "").strip()
            elif "WEBSITE" in headline:
                a = p.css_first("a")
                if a:
                    website = a.attributes.get("href")
                    website_name = a.text(strip=True)
                else:
                    txt = p.text(strip=True).replace(headline_el.text(strip=True), "").strip()
                    website = txt
                    website_name = txt
            elif "BY" in headline:
                a = p.css_first("a")
                if a:
                    href = a.attributes.get("href", "")
                    user_link = f"https://www.pornhub.com{href}" if href.startswith("/") else href
                    owner_name = a.text(strip=True)
                else:
                    owner_name = p.text(strip=True).replace(headline_el.text(strip=True), "").strip()

        if not description:
            if meta_desc := lexbor.css_first("meta[name='description'], meta[property='og:description']"):
                description = meta_desc.attributes.get("content")

        if not description:
            logger.warning(f"Failed to extract description for Channel at {url}")

        # 5. Channel ID and Token
        channel_id = None
        token = None

        btn = lexbor.css_first(
            ".js-channelSubscribe button[data-id], #channel_flag[data-id], button.subscribeBtn[data-id], button[data-id]"
        )
        if btn:
            channel_id = btn.attributes.get("data-id")
            sub_url = btn.attributes.get("data-subscribe-url", "")
            if match := re.search(r"[?&]token=([a-zA-Z0-9_\-\.]+)", sub_url):
                token = match.group(1)

        if not channel_id:
            if flag := lexbor.css_first("#channel_flag[data-id]"):
                channel_id = flag.attributes.get("data-id")

        if not channel_id and html_content:
            if id_match := re.search(r'data-id="(\d+)"', html_content):
                channel_id = id_match.group(1)

        if not channel_id:
            logger.warning(f"Failed to extract channel_id for Channel at {url}")

        if not token:
            if token_var := re.search(r'var\s+token\s*=\s*["\']([^"\']+)["\']', html_content or ""):
                token = token_var.group(1)
        if not token and html_content:
            if token_match := REGEX_TOKEN.search(html_content):
                token = token_match.group(1)
        if not token and html_content:
            if token_url := re.search(r"[?&]token=([a-zA-Z0-9_\-\.]+)", html_content):
                token = token_url.group(1)

        # 6. Cover & Avatar URL
        cover_el = lexbor.css_first("img#coverPictureDefault, #coverPicture img, .cover img")
        cover_url = cover_el.attributes.get("src") or cover_el.attributes.get("data-src") if cover_el else None

        avatar_el = lexbor.css_first("img#getAvatar, #avatarPicture img, .avatar img, .previewAvatarPicture img")
        avatar_url = avatar_el.attributes.get("src") or avatar_el.attributes.get("data-src") if avatar_el else None
        if not avatar_url:
            if meta_img := lexbor.css_first("meta[property='og:image'], meta[name='twitter:image']"):
                avatar_url = meta_img.attributes.get("content")

        # 7. Pornstars featured
        pornstars: list[str] = []
        for li in lexbor.css("ul.channelPornstars li.performerCard"):
            img = li.css_first("img")
            star_name = img.attributes.get("alt") if img else None
            if not star_name:
                title_a = li.css_first("a.title")
                star_name = title_a.text(strip=True) if title_a else None
            if star_name and star_name not in pornstars:
                pornstars.append(star_name)

        return {
            "name": name,
            "is_award_winner": is_award_winner,
            "is_verified": is_verified,
            "is_content_partner": is_content_partner,
            "video_views": video_views,
            "subscribers": subscribers,
            "total_videos": total_videos,
            "rank": rank,
            "description": description,
            "join_date": join_date,
            "website": website,
            "website_name": website_name,
            "user_link": user_link,
            "channel_id": channel_id,
            "token": token,
            "avatar_url": avatar_url,
            "cover_url": cover_url,
            "owner_name": owner_name,
            "badges": badges,
            "pornstars": pornstars,
        }

    def get_videos(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        base_url = profile_base_url(self.resolved_url or self.url)
        page_urls = [update_url_query(append_url_path(base_url, "videos"), page=page) for page in range(1, pages + 1)]
        return _scrape_stream(
            core=self.core, constructor=Video, target_page_urls=page_urls,
            item_extractor=partial(extractor_videos, base_url=base_url), iterator_config=iterator_config,
        )

    async def get_user(self, load_html: bool = True) -> User | None:
        user_link = await self.get_field("user_link")
        if not user_link:
            logger.warning(f"No user_link found for Channel at {self.url}")
            return None
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
    author_name: str | None = media_field("html")
    author_id: str | None = media_field("html")
    video_count: str | None = media_field("html")
    description: str | None = media_field("html")
    unavailable_videos: int | None = media_field("html")
    tags: dict[str, str] | None = media_field("html")
    favorites: int | None = media_field("html")
    date_added: str | None = media_field("html")
    date_updated: str | None = media_field("html")
    status: str | None = media_field("html")
    thumbnail: str | None = media_field("html")
    first_video_url: str | None = media_field("html")

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}

    def __post_init__(self) -> None:
        if self.url.isdigit():
            object.__setattr__(self, "url", f"https://www.pornhub.com/playlist/{self.url}")
        elif self.url.startswith("/"):
            object.__setattr__(self, "url", f"https://www.pornhub.com{self.url}")

    async def _load_html(self) -> dict[str, object]:
        logger.debug(f"Fetching HTML for Playlist at {self.url}")
        html_content = await get_html_content(core=self.core, url=self.url, owner=self)
        return await asyncio.to_thread(self._extract_html, html_content)

    def _extract_html(self, html_content: str) -> dict:
        logger.debug("Extracting info from Playlist HTML...")
        lexbor = LexborHTMLParser(html_content)

        # 1. Parse PLAYLIST_VIEW JavaScript object if present
        playlist_view: dict[str, Any] = {}
        pv_match = re.search(r"PLAYLIST_VIEW\s*=\s*({.+?});", html_content)
        if pv_match:
            try:
                playlist_view = json.loads(pv_match.group(1))
            except Exception:
                logger.warning("Failed to parse playlist metadata", exc_info=True)

        # 2. Token
        token = None
        token_var = re.search(r'var\s+token\s*=\s*["\']([^"\']+)["\']', html_content)
        if token_var:
            token = token_var.group(1)
        if not token:
            token_match = REGEX_TOKEN.search(html_content)
            if token_match:
                token = token_match.group(1)
        if not token:
            token_json = re.search(r'["\']token["\']\s*:\s*["\']([^"\']+)["\']', html_content)
            if token_json:
                token = token_json.group(1)
        if not token:
            token_attr = lexbor.css_first("[data-token]")
            if token_attr:
                token = token_attr.attributes.get("data-token")
        if not token:
            token_url = re.search(r'[?&]token=([a-zA-Z0-9_\-\.]+)', html_content)
            if token_url:
                token = token_url.group(1)
        if not token:
            logger.warning(f"Failed to extract token for Playlist at {self.url}")

        # 3. Playlist ID
        id_match = re.search(r"(?:playlist/|[?&]id=|[?&]pkey=)(\d+)", self.url) or re.search(r"(\d+)/?$", self.url)
        playlist_id = id_match.group(1) if id_match else None
        if not playlist_id and playlist_view.get("id"):
            playlist_id = str(playlist_view["id"])
        if not playlist_id:
            id_input = lexbor.css_first("input#js-editPlaylistId")
            if id_input and id_input.attributes.get("value"):
                playlist_id = id_input.attributes.get("value")
        if not playlist_id:
            id_var = re.search(r'var\s+playlistId\s*=\s*["\'](\d+)["\']', html_content)
            if id_var:
                playlist_id = id_var.group(1)
        if not playlist_id:
            logger.warning(f"Failed to extract playlist_id for Playlist at {self.url}")

        # 4. Title
        title = playlist_view.get("title")
        if not title:
            title_el = lexbor.css_first("h1.playlistTitle") or lexbor.css_first("h1#watchPlaylist")
            if title_el:
                title = title_el.text(strip=True)
        if not title:
            title_input = lexbor.css_first("input#js-editPlaylistTitle")
            if title_input and title_input.attributes.get("value"):
                title = title_input.attributes.get("value").strip()
        if not title:
            meta_title = lexbor.css_first('meta[property="og:title"], meta[name="twitter:title"]')
            if meta_title and meta_title.attributes.get("content"):
                title = meta_title.attributes.get("content").strip()
        if not title:
            title_tag = lexbor.css_first("title")
            if title_tag:
                raw = title_tag.text(strip=True)
                title = re.sub(r"\s*-\s*Porn\s+Video\s+Playlist.*$", "", raw, flags=re.IGNORECASE).strip()
        if not title:
            logger.warning(f"Failed to extract title for Playlist at {self.url}")

        # 5. Views
        views = None
        views_el = (
            lexbor.css_first("#viewsRatings div.views span.count")
            or lexbor.css_first("#viewsRatings div.views")
            or lexbor.css_first("div.rating-info-container div.views")
        )
        if views_el:
            views = re.sub(r"(?i)\s*views?", "", views_el.text(strip=True)).strip()
        if not views:
            v_match = re.search(r'class=["\']views["\'][^>]*>\s*(?:<span[^>]*>)?([0-9,\.KMkm]+)', html_content)
            if v_match:
                views = v_match.group(1).strip()
        if not views:
            logger.warning(f"Failed to extract views for Playlist at {self.url}")

        # 6. Likes and Dislikes
        likes = None
        dislikes = None
        rating_data = re.search(r"var\s+ratingData\s*=\s*({[^}]+})", html_content)
        if rating_data:
            up_m = re.search(r'["\']?upVotes["\']?\s*:\s*(\d+)', rating_data.group(1))
            down_m = re.search(r'["\']?downVotes["\']?\s*:\s*(\d+)', rating_data.group(1))
            if up_m:
                likes = up_m.group(1)
            if down_m:
                dislikes = down_m.group(1)
        if not likes:
            likes_el = lexbor.css_first("div.votes-count-container span.votesUp")
            if likes_el:
                likes = likes_el.text(strip=True)
        if not dislikes:
            dislikes_el = lexbor.css_first("div.votes-count-container span.votesDown")
            if dislikes_el:
                dislikes = dislikes_el.text(strip=True)
        if not likes or not dislikes:
            votes_spans = lexbor.css("div.votes-count-container span")
            if len(votes_spans) > 1 and not likes:
                likes = votes_spans[1].text(strip=True)
            if len(votes_spans) > 2 and not dislikes:
                dislikes = votes_spans[2].text(strip=True)
        if not likes:
            logger.warning(f"Failed to extract likes for Playlist at {self.url}")

        # 7. Rating percent
        rating_percent = None
        percent_el = lexbor.css_first("div.votes-count-container span.percent") or lexbor.css_first("#playlistTopHeader span.rating")
        if percent_el:
            rating_percent = percent_el.text(strip=True)
        if not rating_percent and likes and dislikes:
            try:
                up_val = int(likes)
                down_val = int(dislikes)
                total = up_val + down_val
                if total > 0:
                    rating_percent = f"{round((up_val / total) * 100)}%"
            except ValueError:
                pass
        if not rating_percent:
            logger.warning(f"Failed to extract rating_percent for Playlist at {self.url}")

        # 8. Author
        author_link = None
        author_name = None
        author_id = None
        user_a = (
            lexbor.css_first("div#js-aboutPlaylistTabView div.usernameWrap a")
            or lexbor.css_first("div#playlistWrapper div.usernameWrap a")
            or lexbor.css_first("div.usernameWrap.clearfix > a")
        )
        if user_a:
            href = user_a.attributes.get("href")
            if href:
                author_link = f"https://www.pornhub.com{href}" if href.startswith("/") else href
            author_name = user_a.text(strip=True) or user_a.attributes.get("title")
        user_wrap = lexbor.css_first("div#js-aboutPlaylistTabView div.usernameWrap[data-userid]") or lexbor.css_first("div.usernameWrap[data-userid]")
        if user_wrap:
            author_id = user_wrap.attributes.get("data-userid")
        if not author_id and playlist_view.get("user_id"):
            author_id = str(playlist_view["user_id"])
        if not author_link:
            logger.warning(f"Failed to extract author_link for Playlist at {self.url}")

        # 9. Video count
        video_count = None
        if playlist_view.get("video_count") is not None:
            video_count = str(playlist_view["video_count"])
        if not video_count:
            items_match = re.search(r"var\s+itemsCount\s*=\s*(\d+)", html_content)
            if items_match:
                video_count = items_match.group(1)
        if not video_count:
            about_tab = lexbor.css_first("div#js-aboutPlaylistTabView")
            if about_tab:
                count_match = re.search(r"-\s*(\d+)\s*videos?", about_tab.text(strip=True), re.IGNORECASE)
                if count_match:
                    video_count = count_match.group(1)
        if not video_count:
            video_items = lexbor.css("ul#videoPlaylist > li.pcVideoListItem, ul#videoPlaylist > li.videoBox")
            if video_items:
                video_count = str(len(video_items))
        if not video_count:
            logger.warning(f"Failed to extract video_count for Playlist at {self.url}")

        # 10. Description
        description = playlist_view.get("description")
        if not description:
            desc_textarea = lexbor.css_first("textarea#js-editPlaylistDescription")
            if desc_textarea and desc_textarea.text(strip=True):
                description = desc_textarea.text(strip=True)
        if not description:
            desc_p = lexbor.css_first("p.description.js-playlistDescription")
            if desc_p:
                span = desc_p.css_first("span")
                if span:
                    span_text = span.text(strip=True)
                    full_text = desc_p.text(strip=True)
                    if full_text.startswith(span_text):
                        description = full_text[len(span_text):].strip()
                    else:
                        description = full_text
                else:
                    description = desc_p.text(strip=True)
        if not description:
            meta_desc = lexbor.css_first('meta[name="description"], meta[property="og:description"]')
            if meta_desc and meta_desc.attributes.get("content"):
                description = meta_desc.attributes.get("content").strip()

        # 11. Unavailable videos
        unavail_match = re.search(r"unavailable videos that are hidden:\s*(\d+)", html_content)
        unavailable_videos = int(unavail_match.group(1)) if unavail_match else 0

        # 12. Tags
        tags = {}
        tag_container = lexbor.css_first("div.tagsWrap.js-tagsWrap")
        if tag_container:
            for a in tag_container.css("a"):
                tag_name = a.text(strip=True)
                href = a.attributes.get("href")
                if tag_name and href:
                    tags[tag_name] = f"https://www.pornhub.com{href}" if href.startswith("/") else href
        if not tags:
            tag_input = lexbor.css_first("input#js-tagsList")
            if tag_input and tag_input.attributes.get("value"):
                raw_tags = tag_input.attributes.get("value").split("|")
                for raw in raw_tags:
                    name = raw.replace("+", " ").strip()
                    if name:
                        tags[name] = f"https://www.pornhub.com/video/search?search={raw}"
        if not tags:
            for btn in lexbor.css("ul#tagList li.tagButton span.tagText"):
                name = btn.text(strip=True)
                if name:
                    encoded = name.replace(" ", "+")
                    tags[name] = f"https://www.pornhub.com/video/search?search={encoded}"
        if not tags:
            logger.warning(f"Failed to extract tags for Playlist at {self.url}")

        # 13. Additional useful attributes
        favorites = playlist_view.get("favorite_count")
        if favorites is not None:
            try:
                favorites = int(favorites)
            except (ValueError, TypeError):
                pass
        date_added = playlist_view.get("date_added")
        date_updated = playlist_view.get("date_updated")

        status = playlist_view.get("status")
        if not status:
            status_input = lexbor.css_first("input#js-status")
            if status_input and status_input.attributes.get("value"):
                raw_val = status_input.attributes.get("value")
                status = "public" if "public" in raw_val else ("private" if "private" in raw_val else raw_val)
            elif lexbor.css_first("span.textFilter"):
                status = lexbor.css_first("span.textFilter").text(strip=True).lower()

        first_video_url = playlist_view.get("watchPlaylistUrl")
        if not first_video_url:
            watch_btn = lexbor.css_first("a#watchPlaylist[href], a.js-watchPlaylist[href]")
            if watch_btn and watch_btn.attributes.get("href"):
                first_video_url = watch_btn.attributes.get("href")
        if not first_video_url:
            first_v = lexbor.css_first("ul#videoPlaylist li.pcVideoListItem a.linkVideoThumb")
            if first_v and first_v.attributes.get("href"):
                first_video_url = first_v.attributes.get("href")
        if first_video_url and first_video_url.startswith("/"):
            first_video_url = f"https://www.pornhub.com{first_video_url}"

        thumbnail = None
        first_thumb = lexbor.css_first("ul#videoPlaylist li.pcVideoListItem img")
        if first_thumb:
            thumbnail = (
                first_thumb.attributes.get("data-image")
                or first_thumb.attributes.get("data-mediumthumb")
                or first_thumb.attributes.get("data-src")
                or first_thumb.attributes.get("src")
            )

        return {
            "token": token,
            "playlist_id": playlist_id,
            "title": title,
            "views": views,
            "rating_percent": rating_percent,
            "likes": likes,
            "dislikes": dislikes,
            "author_link": author_link,
            "author_name": author_name,
            "author_id": author_id,
            "video_count": video_count,
            "description": description,
            "unavailable_videos": unavailable_videos,
            "tags": tags,
            "favorites": favorites,
            "date_added": date_added,
            "date_updated": date_updated,
            "status": status,
            "thumbnail": thumbnail,
            "first_video_url": first_video_url,
        }

    @property
    def author(self) -> User | Pornstar | Channel | Model | None:
        if not self.author_link:
            return None
        if "/channels/" in self.author_link:
            return Channel(core=self.core, url=self.author_link)
        if "/model/" in self.author_link:
            return Model(core=self.core, url=self.author_link)
        if "/pornstar/" in self.author_link:
            return Pornstar(core=self.core, url=self.author_link)
        return User(core=self.core, url=self.author_link)

    @property
    def tag_names(self) -> list[str]:
        return list(self.tags.keys()) if self.tags else []

    async def get_videos(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        await self.load_fields("playlist_id", "token")
        if not self.playlist_id or not self.token:
            logger.warning(
                f"Cannot fetch videos for Playlist at {self.url}: missing playlist_id ({self.playlist_id}) or token ({self.token})"
            )
            return
        base_url = replace_url_path(self.resolved_url or self.url, "/playlist/viewChunked")
        page_urls = [
            update_url_query(base_url, id=self.playlist_id, token=self.token, page=page)
            for page in range(1, pages + 1)
        ]
        async for result in _scrape_stream(
            core=self.core, constructor=Video, target_page_urls=page_urls,
            item_extractor=partial(extractor_playlist, base_url=base_url), iterator_config=iterator_config,
        ):
            yield result

    async def get_author(self, load_html: bool = True) -> User | Pornstar | Channel | Model | None:
        author_link = await self.get_field("author_link")
        if not author_link:
            return None
        author_cls: type[User | Pornstar | Channel | Model] = User
        if "/channels/" in author_link:
            author_cls = Channel
        elif "/model/" in author_link:
            author_cls = Model
        elif "/pornstar/" in author_link:
            author_cls = Pornstar
        author_obj = author_cls(url=author_link, core=self.core)
        if load_html:
            await author_obj.load_sources("html")
        return author_obj


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
    is_premium: bool | None = media_field("html")

    # HTML Scraped fields
    views: str | None = media_field("api", "html")
    publish_date: str | None = media_field("api", "html")
    likes: int | str | None = media_field("api", "html")
    description: str | None = media_field("html")

    # Playlist URL
    m3u8_base_url: str | None = media_field("html")

    # Categorization maps
    categories: list[str] | None = media_field("api", "html")
    tags: list[str] | None = media_field("api", "html")
    pornstars: list[str] | None = media_field("api", "html")
    segment: str | None = media_field("api", "html")
    rating_percent: str | float | None = media_field("api", "html")

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
            match = re.search(r"viewkey=([^&#]+)", self.url) or re.search(r"embed/([^&#/?]+)", self.url)
            self.video_id = match.group(1) if match else None
        if urlsplit(self.url).path.startswith("/embed/") and self.video_id:
            self.url = update_url_query(replace_url_path(self.url, "/view_video.php"), viewkey=self.video_id)

    async def _load_html(self) -> dict[str, object]:
        logger.debug(f"Fetching HTML for Video at {self.url}")
        html_content = await get_html_content(core=self.core, url=self.url, owner=self)
        return await asyncio.to_thread(self._extract_html, html_content, self.url)

    async def _load_api(self) -> dict[str, object]:
        logger.debug(f"Fetching API data for Video {self.video_id}")
        stuff = await get_html_content(url=update_url_query(replace_url_path(self.url, "/webmasters/video_by_id"), id=self.video_id), core=self.core, owner=self)
        return await asyncio.to_thread(self._extract_api, stuff)

    @staticmethod
    def _extract_html(html_content: str, url: str | None = None) -> dict:
        logger.debug("Extracting info from Video HTML...")
        parser = LexborHTMLParser(html_content)

        # 1. Parse JSON objects from script tags safely
        flashvars: dict[str, Any] = {}
        match_fv = REGEX_VIDEO_FLASHVARS.search(html_content)
        if match_fv:
            raw_fv = match_fv.group(1)
            try:
                flashvars = json.loads(raw_fv, strict=False)
            except Exception:
                try:
                    flashvars = chompjs.parse_js_object(raw_fv)
                except Exception as e:
                    logger.warning("Failed to parse flashvars JSON for %s: %s", url, e, exc_info=True)

        video_show: dict[str, Any] = {}
        match_vs = re.search(r"var\s+VIDEO_SHOW\s*=\s*(\{.*?\});", html_content, re.DOTALL)
        if match_vs:
            raw_vs = match_vs.group(1)
            try:
                video_show = json.loads(raw_vs, strict=False)
            except Exception:
                try:
                    video_show = chompjs.parse_js_object(raw_vs)
                except Exception as e:
                    logger.warning("Failed to parse VIDEO_SHOW for %s: %s", url, e, exc_info=True)

        ratings_widget: dict[str, Any] = {}
        match_rw = re.search(r"var\s+WIDGET_RATINGS_LIKE_FAV\s*=\s*(\{.*?\});", html_content, re.DOTALL)
        if match_rw:
            raw_rw = match_rw.group(1)
            try:
                ratings_widget = json.loads(raw_rw, strict=False)
            except Exception:
                try:
                    ratings_widget = chompjs.parse_js_object(raw_rw)
                except Exception:
                    logger.warning("Failed to parse ratings metadata", exc_info=True)

        schema_json: dict[str, Any] = {}
        for s in parser.css('script[type="application/ld+json"]'):
            try:
                parsed_schema = json.loads(s.text())
                if isinstance(parsed_schema, dict) and parsed_schema.get("@type") == "VideoObject":
                    schema_json = parsed_schema
                    break
            except Exception:
                logger.warning("Failed to parse video JSON-LD metadata for %s", url, exc_info=True)
                continue

        meta_tags: dict[str, str] = {}
        for m in parser.css("meta"):
            key = m.attributes.get("property") or m.attributes.get("name")
            val = m.attributes.get("content")
            if key and val:
                meta_tags[key] = val

        # 2. Extract Title
        title = (
            flashvars.get("video_title")
            or video_show.get("videoTitle")
            or video_show.get("videoTitleOriginal")
            or schema_json.get("name")
            or meta_tags.get("og:title")
            or meta_tags.get("twitter:title")
        )
        if not title:
            h1 = parser.css_first("h1.title span.inlineFree, h1.title, h1")
            if h1:
                title = h1.text(strip=True)
        if not title:
            title_node = parser.css_first("title")
            if title_node:
                raw_title = title_node.text(strip=True)
                for suffix in (" | Pornhub", " - Pornhub"):
                    if raw_title.endswith(suffix):
                        raw_title = raw_title[:-len(suffix)].strip()
                title = raw_title
        if not title:
            logger.warning("Failed to extract title for Video at %s", url)

        # 3. Extract Thumbnail
        thumbnail = (
            flashvars.get("image_url")
            or video_show.get("placeholder")
            or schema_json.get("thumbnailUrl")
            or meta_tags.get("og:image")
            or meta_tags.get("twitter:image")
        )
        if not thumbnail:
            thumb_img = parser.css_first("div.phimage img[src], link[rel='image_src']")
            if thumb_img:
                thumbnail = thumb_img.attributes.get("src") or thumb_img.attributes.get("href")
        if not thumbnail:
            logger.warning("Failed to extract thumbnail for Video at %s", url)

        # 4. Extract Duration
        duration: int | None = None
        if "video_duration" in flashvars:
            val = flashvars["video_duration"]
            if isinstance(val, (int, float)):
                duration = int(val)
            elif isinstance(val, str) and val.isdigit():
                duration = int(val)
        if duration is None and "video:duration" in meta_tags:
            val = meta_tags["video:duration"]
            if val.isdigit():
                duration = int(val)
        if duration is None and "duration" in schema_json:
            dur_str = schema_json["duration"]
            if isinstance(dur_str, str):
                h_m = re.search(r"(\d+)H", dur_str)
                m_m = re.search(r"(\d+)M", dur_str)
                s_m = re.search(r"(\d+)S", dur_str)
                if h_m or m_m or s_m:
                    duration = (
                        (int(h_m.group(1)) * 3600 if h_m else 0)
                        + (int(m_m.group(1)) * 60 if m_m else 0)
                        + (int(s_m.group(1)) if s_m else 0)
                    )
                elif dur_str.isdigit():
                    duration = int(dur_str)
            elif isinstance(dur_str, (int, float)):
                duration = int(dur_str)
        if duration is None:
            logger.warning("Failed to extract duration for Video at %s", url)

        # 5. Extract Available Qualities
        qualities = flashvars.get("defaultQuality", [])
        if not qualities and "mediaDefinitions" in flashvars:
            qualities = []
            for md in flashvars.get("mediaDefinitions") or []:
                if isinstance(md, dict):
                    h = md.get("height")
                    if isinstance(h, int) and h > 0:
                        qualities.append(h)
        available_qualities = sorted(list(set(qualities))) if isinstance(qualities, list) else []

        # 6. Flags
        is_vr = bool(flashvars.get("isVR")) if "isVR" in flashvars else (
            bool(video_show.get("isVr")) or ("\"enabled\":1" in str(video_show.get("vr", "")))
        )
        is_video_unavailable = (
            str(flashvars["video_unavailable"]).lower() in ("true", "1")
            if "video_unavailable" in flashvars
            else bool(parser.css_first("div.video-has-been-removed, div.removed-video, div.unavailable"))
        )
        is_hd = (
            str(flashvars["isHD"]).lower() in ("true", "1")
            if "isHD" in flashvars
            else (any(q >= 720 for q in available_qualities) or bool(parser.css_first("span.hd-thumbnail, span.hd-video, i.ph-icon-hd")))
        )
        is_vertical = (
            str(flashvars["isVertical"]).lower() in ("true", "1")
            if "isVertical" in flashvars
            else bool(video_show.get("isVertical", False))
        )
        is_video_unavailable_in_your_country = (
            str(flashvars["video_unavailable_country"]).lower() in ("true", "1")
            if "video_unavailable_country" in flashvars
            else bool(schema_json.get("ineligibleRegion"))
        )
        is_premium = bool(video_show.get("isPremium", 0)) or bool(ratings_widget.get("isPremium", False))

        # 7. Views
        views_el = parser.css_first(
            "div.video-actions-menu div.views span.count, "
            "div.ratingInfo div.views span.count"
        ) or parser.css_first(
            "div.video-actions-menu div.views, "
            "div.ratingInfo div.views"
        )
        views = views_el.text(strip=True) if views_el else None
        if not views and schema_json.get("interactionStatistic"):
            for stat in schema_json["interactionStatistic"]:
                if isinstance(stat, dict) and "WatchAction" in stat.get("interactionType", ""):
                    views = stat.get("userInteractionCount")
                    break
        if not views:
            logger.warning("Failed to extract views for Video at %s", url)

        # 8. Publish Date
        date_el = parser.css_first("div.video-actions-menu div.videoInfo, div.videoInfo")
        publish_date = date_el.text(strip=True) if date_el else None
        if not publish_date:
            publish_date = schema_json.get("uploadDate") or meta_tags.get("video:release_date") or meta_tags.get("article:published_time")
        if not publish_date:
            logger.warning("Failed to extract publish_date for Video at %s", url)

        # 9. Likes
        likes: int | str | None = None
        likes_el = parser.css_first("span.votesUp")
        if likes_el:
            data_rating = likes_el.attributes.get("data-rating")
            if data_rating and data_rating.isdigit():
                likes = int(data_rating)
            else:
                likes = likes_el.text(strip=True)
        elif "currentUp" in ratings_widget:
            likes = ratings_widget["currentUp"]
        elif schema_json.get("interactionStatistic"):
            for stat in schema_json["interactionStatistic"]:
                if isinstance(stat, dict) and "LikeAction" in stat.get("interactionType", ""):
                    cnt = stat.get("userInteractionCount")
                    if isinstance(cnt, str) and cnt.replace(",", "").isdigit():
                        likes = int(cnt.replace(",", ""))
                    else:
                        likes = cnt
                    break
        if likes is None:
            logger.warning("Failed to extract likes for Video at %s", url)

        # 10. Rating percentage
        rating_percent: str | float | None = None
        up = ratings_widget.get("currentUp")
        down = ratings_widget.get("currentDown")
        if isinstance(up, (int, float)) and isinstance(down, (int, float)) and (up + down) > 0:
            rating_percent = round((up / (up + down)) * 100, 1)
        if rating_percent is None:
            rating_el = parser.css_first("span.rating, span.percent, span.ratingPercent")
            if rating_el:
                rating_percent = rating_el.text(strip=True)

        # 11. Categories, Tags, Pornstars
        categories_el = parser.css_first("div.categoriesWrapper")
        categories = [a.text(strip=True) for a in categories_el.css("a") if a.text(strip=True)] if categories_el else []

        tags_el = parser.css_first("div.tagsWrapper")
        tags = [a.text(strip=True) for a in tags_el.css("a") if a.text(strip=True)] if tags_el else []

        pornstars_el = parser.css_first("div.pornstarsWrapper")
        pornstars: list[str] = []
        if pornstars_el:
            for a in pornstars_el.css("a.pstar-list-btn, a[data-label='pornstar'], a[href*='/pornstar/'], a[href*='/model/']"):
                txt = a.text(strip=True)
                if txt and txt not in pornstars:
                    pornstars.append(txt)

        # 12. Segment & Description
        segment = video_show.get("segment") or flashvars.get("segment")
        if not segment:
            seg_el = parser.css_first("[data-segment]")
            if seg_el:
                segment = seg_el.attributes.get("data-segment")

        description = schema_json.get("description") or meta_tags.get("description") or meta_tags.get("og:description")
        if not description:
            desc_el = parser.css_first("div.video-description, p.description")
            if desc_el:
                description = desc_el.text(strip=True)
        if description:
            description = html.unescape(description)

        # 13. Author Details
        user_info = parser.css_first("div.userInfo")
        author_name = None
        author_link = None
        video_amount = None
        subscriber_amount = None
        if user_info:
            a_tag = user_info.css_first("div.usernameWrap a, span.usernameBadgesWrapper a, a.bolded")
            if a_tag:
                author_name = a_tag.text(strip=True)
                href = a_tag.attributes.get("href")
                if href:
                    author_link = f"https://www.pornhub.com{href}" if href.startswith("/") else href
            for span in user_info.css("span"):
                txt = span.text(strip=True)
                if not txt:
                    continue
                lower = txt.lower()
                if "video" in lower and not video_amount:
                    video_amount = txt
                elif "subscriber" in lower and not subscriber_amount:
                    subscriber_amount = txt

        avatar_a = parser.css_first("div.userAvatar > a")
        if not author_link and avatar_a:
            href = avatar_a.attributes.get("href")
            if href:
                author_link = f"https://www.pornhub.com{href}" if href.startswith("/") else href

        if not author_name and schema_json.get("author"):
            author_name = schema_json["author"]

        author_thumb_el = parser.css_first("div.userAvatar img, div.authorAvatar img, div.usernameWrap img")
        author_thumbnail = (
            author_thumb_el.attributes.get("src") or author_thumb_el.attributes.get("data-src")
            if author_thumb_el
            else None
        )
        if not author_link:
            logger.warning("Failed to extract author_link for Video at %s", url)

        author_information = {
            "name": author_name,
            "link": author_link,
            "video_amount": video_amount,
            "subscriber_amount": subscriber_amount,
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
            "is_premium": is_premium,
            "views": views,
            "publish_date": publish_date,
            "likes": likes,
            "m3u8_base_url": build_m3u8_master(flashvars.get("mediaDefinitions")),
            "categories": categories,
            "tags": tags,
            "pornstars": pornstars,
            "segment": segment,
            "rating_percent": rating_percent,
            "description": description,
            "author_thumbnail": author_thumbnail,
            "author_link": author_link,
            "author_information": author_information,
        }

    @staticmethod
    def _extract_api(json_data: str) -> dict:
        logger.debug("Extracting API data for Video...")
        raw = json.loads(json_data, strict=False)
        video = raw.get("video", {})

        dur = video.get("duration")
        if isinstance(dur, str):
            if ":" in dur:
                parts = [int(p) for p in dur.split(":") if p.isdigit()]
                duration = sum(p * 60**i for i, p in enumerate(reversed(parts)))
            elif dur.isdigit():
                duration = int(dur)
            else:
                duration = None
        elif isinstance(dur, (int, float)):
            duration = int(dur)
        else:
            duration = None

        categories = [t["category"] for t in video.get("categories") or [] if isinstance(t, dict) and "category" in t]
        tags = [t["tag_name"] for t in video.get("tags") or [] if isinstance(t, dict) and "tag_name" in t]
        pornstars = [t["pornstar_name"] for t in video.get("pornstars") or [] if isinstance(t, dict) and "pornstar_name" in t]

        ratings = video.get("ratings")
        if isinstance(ratings, str) and ratings.isdigit():
            likes = int(ratings)
        elif isinstance(ratings, int):
            likes = ratings
        else:
            likes = ratings or 0

        return {
            "thumbnail": video.get("default_thumb") or video.get("thumb"),
            "duration": duration,
            "title": video.get("title"),
            "views": video.get("views", "0"),
            "publish_date": video.get("publish_date", ""),
            "rating_percent": video.get("rating"),
            "likes": likes,
            "categories": categories,
            "tags": tags,
            "pornstars": pornstars,
            "segment": video.get("segment"),
        }

    @property
    async def author(self, load_html: bool = True) -> Pornstar | Channel | Model | User | None:
        author_link = await self.get_field("author_link")
        if not author_link:
            return None
        if "pornstar" in author_link:
            author = Pornstar(core=self.core, url=author_link)
        elif "model" in author_link:
            author = Model(core=self.core, url=author_link)
        elif "channel" in author_link:
            author = Channel(core=self.core, url=author_link)
        elif "users" in author_link or "user" in author_link:
            author = User(core=self.core, url=author_link)
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
        with log_context(self, f"{HOST}front/authenticate"):
            try:
                logger.info("Attempting login")

                if not force and self.logged:
                    if throw:
                        raise ClientAlreadyLogged()
                    return True

                if not self.credentials["email"] or not self.credentials["password"]:
                    if throw:
                        raise LoginFailed("Email and password are required")
                    return False

                page_content = await get_html_content(url=HOST, core=self.core, owner=self)
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
            except Exception:
                logger.exception("Login failed")
                raise

    async def fix_recommendations(self) -> bool:
        with log_context(self, f"{HOST}user/log_user_cookie_consent"):
            if not self.logged:
                return False

            logger.info("Fixing account recommendations")
            page_content = await get_html_content(url=HOST, core=self.core, owner=self)
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
        if not url.startswith(("http://", "https://")):
            if url.startswith("/"):
                url = f"https://www.pornhub.com{url}"
            elif cls is Pornstar:
                url = f"https://www.pornhub.com/pornstar/{url}"
            elif cls is Model:
                url = f"https://www.pornhub.com/model/{url}"
            elif cls is User:
                url = f"https://www.pornhub.com/users/{url}"
            elif cls is GIF:
                url = f"https://www.pornhub.com/gif/{url}"
            elif cls is Album:
                url = f"https://www.pornhub.com/album/{url}"
            elif cls is Channel:
                url = f"https://www.pornhub.com/channels/{url}"
            elif cls is Playlist:
                url = f"https://www.pornhub.com/playlist/{url}"
            elif cls is Short:
                url = f"https://www.pornhub.com/shorties/{url}"
            else:
                url = f"https://www.pornhub.com/{url}"
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
