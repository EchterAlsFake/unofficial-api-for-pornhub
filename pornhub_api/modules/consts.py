from __future__ import annotations
from urllib.parse import urljoin

import re
from base_api.modules.logger import get_logger
from typing import Any
from selectolax.lexbor import LexborHTMLParser

logger = get_logger(__name__)

HEADERS = {
    'Accept': '*/*',
    'Connection': 'keep-alive',
    'Referer': 'https://www.pornhub.com/',
    'Origin': 'https://www.pornhub.com',
}

COOKIES = {
    'cookieBannerState': '1',
    'platform': 'pc',
}

HOST = "https://www.pornhub.com/"
LOGIN_PAYLOAD = {
    'from': 'pc_login_modal_:homepage_redesign',
}

REGEX_VIDEO_FLASHVARS = re.compile(r"var\s+flashvars_\d+\s*=\s*(\{.*?\});", re.DOTALL)
REGEX_TOKEN = re.compile(r'token\s*=\s*"([^"]+)"')


def parse_quality(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        digits = ''.join(ch for ch in value if ch.isdigit())
        if digits:
            return int(digits)
    return 0


def parse_quality_from_url(url: str) -> int:
    for part in url.split('/'):
        if 'P' in part or 'p' in part:
            m = re.search(r'(\d+)[pP]', part)
            if m:
                return int(m.group(1))
            prefix = part.split('P_', 1)[0].split('p_', 1)[0]
            val = parse_quality(prefix)
            if val:
                return val
    return 0


def estimate_width(height: int) -> int:
    return int(height * 16 / 9) if height > 0 else 0


def get_m3u8_urls(media_definitions: list[dict] | None) -> dict[tuple[int, int], str]:
    if not media_definitions:
        return {}
    quality_urls = {}
    for q in media_definitions:
        if not isinstance(q, dict) or q.get('format') != 'hls' or not q.get('videoUrl'):
            continue
        try:
            url = q['videoUrl']
            height = parse_quality(q.get('height')) or parse_quality(q.get('quality')) or parse_quality_from_url(url)
            width = parse_quality(q.get('width'))

            if not width and height:
                width = estimate_width(height)

            if width or height:
                quality_urls[(width, height)] = url
        except Exception:
            logger.warning("Skipping invalid HLS media definition for %s", q.get('videoUrl'), exc_info=True)
            continue
    return quality_urls


def _extract_video_blocks(blocks: list, base_url: str = "https://www.pornhub.com") -> list[dict[str, Any]]:
    results = []
    seen = set()
    for block in blocks:
        a_tag = block.css_first("a[href*='view_video']")
        if not a_tag:
            continue
        href = a_tag.attributes.get("href")
        if not href:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)

        match = re.search(r"viewkey=([^&#]+)", url)
        video_id = match.group(1) if match else None

        title = ""
        title_link = block.css_first("span.title a")
        if title_link:
            title = title_link.attributes.get("title") or title_link.text(strip=True)
        if not title:
            img_tag = block.css_first("img")
            if img_tag:
                title = img_tag.attributes.get("alt", "")

        duration_var = block.css_first("var.duration")
        duration = duration_var.text(strip=True) if duration_var else None

        img_tag = block.css_first("img")
        thumbnail = None
        if img_tag:
            thumbnail = (
                img_tag.attributes.get("data-mediumthumb")
                or img_tag.attributes.get("data-src")
                or img_tag.attributes.get("src")
            )

        views_var = block.css_first("span.views var")
        views = views_var.text(strip=True) if views_var else None

        added_var = block.css_first("var.added")
        publish_date = added_var.text(strip=True) if added_var else None

        author_link = None
        author_information = None
        author_tag = block.css_first("div.usernameWrap a")
        if author_tag:
            author_href = author_tag.attributes.get("href")
            if author_href:
                author_link = urljoin(base_url, author_href)
            author_name = author_tag.text(strip=True)
            if author_name:
                author_information = {"name": author_name}

        results.append({
            "url": url,
            "video_id": video_id,
            "title": title,
            "duration": duration,
            "thumbnail": thumbnail,
            "views": views,
            "publish_date": publish_date,
            "author_link": author_link,
            "author_information": author_information,
        })
    return results


def extractor_gifs(html_content: str, base_url: str = "https://www.pornhub.com") -> list[dict[str, str]]:
    parser = LexborHTMLParser(html_content)
    container = parser.css_first(
        "div.gifsWrapperProfile, div.gifsWrapper.hideLastItemLarge, "
        "div.gifsWrapper, ul.gifs, div.gifSearchListing"
    ) or parser

    unique_urls = {}
    for a_tag in container.css("a[href]"):
        href = a_tag.attributes.get("href")
        if isinstance(href, str) and href.startswith("/gif/") and any(c.isdigit() for c in href):
            unique_urls.setdefault(urljoin(base_url, href), None)

    return [{"url": url} for url in unique_urls]


def extractor_model_uploads(html_content: str, base_url: str = "https://www.pornhub.com") -> list[dict[str, Any]]:
    parser = LexborHTMLParser(html_content)
    container = parser.css_first("div.profileVids")
    if container is None:
        raise ValueError("The response did not expose the profile uploads section")
    blocks = container.css("li.pcVideoListItem, li.videoBox")
    return _extract_video_blocks(blocks, base_url)


def extractor_model_videos(html_content: str, base_url: str = "https://www.pornhub.com") -> list[dict[str, Any]]:
    parser = LexborHTMLParser(html_content)
    container = parser.css_first("#mostRecentVideosSection") or parser.css_first("div.profileVids")
    if container is None:
        raise ValueError("The response did not expose the profile videos section")
    blocks = container.css("li.pcVideoListItem, li.videoBox")
    return _extract_video_blocks(blocks, base_url)


def extractor_channel_videos(html_content: str, base_url: str) -> list[dict[str, Any]]:
    listing = LexborHTMLParser(html_content).css_first("#showAllChanelVideos")
    if listing is None:
        raise ValueError("The response did not expose the channel videos section")
    return _extract_video_blocks(listing.css("li[data-video-vkey]"), base_url)


def extractor_hubtraffic(html_content: str, base_url: str = "https://www.pornhub.com") -> list[dict[str, Any]]:
    import json
    data = json.loads(html_content)
    if not isinstance(data, dict) or not isinstance(data.get("videos"), list):
        raise ValueError("Invalid HubTraffic listing")
    results = []
    for video in data["videos"]:
        if not isinstance(video, dict) or not video.get("url"):
            raise ValueError("HubTraffic listing omitted a video URL")
        results.append({
            "url": video["url"],
            "video_id": video.get("video_id"),
            "title": video.get("title") or "",
            "duration": video.get("duration"),
            "thumbnail": video.get("default_thumb") or video.get("thumb"),
            "views": str(video.get("views", "0")),
            "publish_date": video.get("publish_date"),
        })
    return results


def extractor_videos(html_content: str, base_url: str = "https://www.pornhub.com") -> list[dict[str, Any]]:
    results = []
    parser = LexborHTMLParser(html_content)
    # Header carousels, feed sidebars and bottom recommendations are not rows
    # in the requested collection. Their presence must not hide an empty feed.
    for unrelated in parser.css("#hottestMenuSection, .feedRight, #bottomVideos"):
        unrelated.decompose()
    region = (
        parser.css_first(
            "#videoSearchResult, #moreData, .feedContent, #recommendations"
        )
        or parser
    )
    video_blocks = region.css(
        "li.pcVideoListItem, li.videoBox, li > div.pcVideoListItem, li > div.videoBox, "
        "div.pcVideoListItem, div.videoBox"
    )

    if not video_blocks:
        seen = set()
        for a_tag in region.css('a[href*="view_video.php?viewkey="], a[href*="viewkey="]'):
            href = a_tag.attributes.get("href")
            if isinstance(href, str) and href:
                url = urljoin(base_url, href)
                if url not in seen:
                    seen.add(url)
                    results.append({"url": url})
        return results

    seen = set()
    for block in video_blocks:
        a_tag = block.css_first("a[href*='view_video']")
        if not a_tag:
            continue
        href = a_tag.attributes.get("href")
        if not href:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)

        title = a_tag.attributes.get("title")
        if not title:
            img = block.css_first("img")
            title = (img.attributes.get("alt") if img else "") or ""
        if not title:
            title_el = block.css_first("a.title, span.title")
            title = title_el.text(strip=True) if title_el else ""

        dur = block.css_first("var.duration")
        img = block.css_first("img")
        thumb = (img.attributes.get("data-src") or img.attributes.get("src")) if img else None

        results.append({
            "url": url,
            "title": title,
            "duration": dur.text(strip=True) if dur else None,
            "thumbnail": thumb,
        })
    return results


def extractor_playlist(html_content: str, base_url: str = "https://www.pornhub.com") -> list[dict[str, Any]]:
    parser = LexborHTMLParser(html_content)
    container = parser.css_first(
        "div.videos.row-5-thumbs.search-video-thumbs.scrollLazyload.js-videoPlaylist.viewPlaylist"
    ) or parser
    return _extract_video_blocks(container.css("li.pcVideoListItem, li.videoBox"), base_url)


def extractor_users(html_content: str, base_url: str = "https://www.pornhub.com") -> list[dict[str, str]]:
    parser = LexborHTMLParser(html_content)
    urls = dict.fromkeys(
        urljoin(base_url, href)
        for a_tag in parser.css("a.userLink[href]")
        if (href := a_tag.attributes.get("href")) and isinstance(href, str) and href.startswith("/")
    )
    return [{"url": url} for url in urls]
