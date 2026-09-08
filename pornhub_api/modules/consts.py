from __future__ import annotations

import re
import logging
from typing import Any
from selectolax.lexbor import LexborHTMLParser

logger = logging.getLogger(__name__)

HEADERS = {
    'Accept': '*/*',
    'Accept-Language': 'en,en-US',
    'Connection': 'keep-alive',
    'Referer': 'https://www.pornhub.com/',
    'Origin': 'https://www.pornhub.com',
}

COOKIES = {
    'accessAgeDisclaimerPH': '1',
    'accessAgeDisclaimerUK': '1',
    'accessPH': '1',
    'age_verified': '1',
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
        if 'P_' in part or 'p_' in part:
            prefix = part.split('P_', 1)[0].split('p_', 1)[0]
            return parse_quality(prefix)
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
            width = int(q.get('width') or 0)
            height = int(q.get('height') or 0)
            url = q['videoUrl']

            if not height:
                height = parse_quality(q.get('quality')) or parse_quality_from_url(url)
            if not width and height:
                width = estimate_width(height)

            if width or height:
                quality_urls[(width, height)] = url
        except Exception:
            logger.warning("Skipping invalid HLS media definition for %s", q.get('videoUrl'), exc_info=True)
            continue
    return quality_urls


def _extract_video_blocks(blocks: list) -> list[dict[str, Any]]:
    results = []
    seen = set()
    for block in blocks:
        a_tag = block.css_first("a[href*='view_video']")
        if not a_tag:
            continue
        href = a_tag.attributes.get("href")
        if not href:
            continue
        url = f"https://www.pornhub.com{href}"
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
                author_link = f"https://www.pornhub.com{author_href}" if author_href.startswith("/") else author_href
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


def extractor_gifs(html_content: str) -> list[dict[str, str]]:
    parser = LexborHTMLParser(html_content)
    container = parser.css_first(
        "div.gifsWrapperProfile, div.gifsWrapper.hideLastItemLarge, "
        "div.gifsWrapper, ul.gifs, div.gifSearchListing"
    ) or parser

    unique_urls = set()
    for a_tag in container.css("a[href]"):
        href = a_tag.attributes.get("href")
        if isinstance(href, str) and href.startswith("/gif/") and any(c.isdigit() for c in href):
            unique_urls.add(f"https://www.pornhub.com{href}")

    return [{"url": url} for url in unique_urls]


def extractor_model_uploads(html_content: str) -> list[dict[str, Any]]:
    parser = LexborHTMLParser(html_content)
    container = parser.css_first("div.profileVids") or parser
    return _extract_video_blocks(container.css("li.pcVideoListItem, li.videoBox"))


def extractor_model_videos(html_content: str) -> list[dict[str, Any]]:
    parser = LexborHTMLParser(html_content)
    container = parser.css_first("#mostRecentVideosSection") or parser
    return _extract_video_blocks(container.css("li.pcVideoListItem, li.videoBox"))


def extractor_videos(html_content: str) -> list[dict[str, Any]]:
    results = []
    parser = LexborHTMLParser(html_content)
    video_blocks = parser.css("li > div.pcVideoListItem, li > div.videoBox")

    if not video_blocks:
        seen = set()
        for a_tag in parser.css('a[href^="/view_video.php?viewkey="]'):
            href = a_tag.attributes.get("href")
            if isinstance(href, str) and href:
                url = f"https://www.pornhub.com{href}"
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
        url = f"https://www.pornhub.com{href}"
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


def extractor_playlist(html_content: str) -> list[dict[str, Any]]:
    parser = LexborHTMLParser(html_content)
    container = parser.css_first(
        "div.videos.row-5-thumbs.search-video-thumbs.scrollLazyload.js-videoPlaylist.viewPlaylist"
    ) or parser
    return _extract_video_blocks(container.css("li.pcVideoListItem, li.videoBox"))


def extractor_users(html_content: str) -> list[dict[str, str]]:
    parser = LexborHTMLParser(html_content)
    urls = {
        f"https://www.pornhub.com{href}"
        for a_tag in parser.css("a.userLink[href]")
        if (href := a_tag.attributes.get("href")) and isinstance(href, str) and href.startswith("/")
    }
    return [{"url": url} for url in urls]
