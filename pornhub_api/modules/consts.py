import re
import json

from typing import Any
from selectolax.lexbor import LexborHTMLParser
import logging

logger = logging.getLogger(__name__)


INCREMENT = 30
KNOWN_PRIME_FACTORS = [2, 3, 5]
HEADERS = {
    'Accept': '*/*',
    'Accept-Language': 'en,en-US',
    'Connection': 'keep-alive',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/114.0',
    'Referer': 'https://www.pornhub.com/',
    'Origin': 'https://www.pornhub.com',
}

COOKIES = {
    'accessAgeDisclaimerPH': '1',
    'accessAgeDisclaimerUK': '1',
    'accessPH': '1',
    'age_verified': '1',
    'cookieBannerState': '1',
    'platform': 'pc'
}


HOST = "https://www.pornhub.com/"
LOGIN_PAYLOAD = {
    'from': 'pc_login_modal_:homepage_redesign',
}

# REGEX for Video extraction:
REGEX_VIDEO_FLASHVARS = re.compile(r"var\s+flashvars_\d+\s*=\s*(\{.*?\});", re.DOTALL)

# Regex for playlists and tokens
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
    if height <= 0:
        return 0

    return int(height * 9 / 16)


def get_m3u8_urls(media_definitions: dict) -> dict:
    quality_urls = {}
    raw_qualities = media_definitions

    for q in raw_qualities:
        if q.get('format') != 'hls' or not q.get('videoUrl'):
            continue

        try:
            width = int(q.get('width') or 0)
            height = int(q.get('height') or 0)
            url = q['videoUrl']

            if not height:
                height = parse_quality(q.get('quality')) or parse_quality_from_url(url)
            if not width and height:
                width = estimate_width(height)

            if not width and not height:
                continue

            quality_urls[(width, height)] = url

        except Exception as e:
            continue

    return quality_urls

def extractor_gifs(html_content: str) -> list:
    unique_urls = set()
    lexbor = LexborHTMLParser(html_content)
    # Try multiple possible containers for GIFs
    containers = [
        lexbor.css_first("div.gifsWrapperProfile"),
        lexbor.css_first("div.gifsWrapper.hideLastItemLarge"),
        lexbor.css_first("div.gifsWrapper"),
        lexbor.css_first("ul.gifs"),
        lexbor.css_first("div.gifSearchListing"),
    ]
    
    # Find the first not-None container
    main_div = next((c for c in containers if c is not None), lexbor)

    for a_tag in main_div.css("a[href]"):
        href = a_tag.attributes.get("href")
        # Ensure it's a GIF link (usually /gif/ followed by digits) and not a duplicate
        if isinstance(href, str) and href.startswith("/gif/") and any(char.isdigit() for char in href):
            full_url = f"https://www.pornhub.com{href}"
            unique_urls.add(full_url)

    links = [{"url": url} for url in unique_urls]
    return links


def extractor_model_uploads(html_content: str) -> list:
    results = []
    parser = LexborHTMLParser(html_content)

    # Target the specific container for profile/model uploads
    video_container = parser.css_first("div.profileVids")

    if not video_container:
        # Fallback to general list items if the specific container isn't found
        video_blocks = parser.css("li.pcVideoListItem, li.videoBox")
    else:
        video_blocks = video_container.css("li.pcVideoListItem, li.videoBox")

    for block in video_blocks:
        # 1. Extract URL
        a_tag = block.css_first("a[href*='view_video']")
        if not a_tag:
            continue

        href = a_tag.attributes.get("href")
        if not href:
            continue

        url = f"https://www.pornhub.com{href}"
        if any(r["url"] == url for r in results):
            continue

        # 2. Extract Video ID (viewkey)
        match = re.search(r"viewkey=([^&#]+)", url)
        video_id = match.group(1) if match else None

        # 3. Extract Title
        title = ""
        title_link = block.css_first("span.title a")
        if title_link:
            title = title_link.attributes.get("title") or title_link.text(strip=True)

        if not title:
            # Fallback to image alt text
            img_tag = block.css_first("img")
            if img_tag:
                title = img_tag.attributes.get("alt", "")

        # 4. Extract Duration
        duration_var = block.css_first("var.duration")
        duration = duration_var.text(strip=True) if duration_var else None

        # 5. Extract Thumbnail
        img_tag = block.css_first("img")
        thumbnail = None
        if img_tag:
            # Prioritize data-mediumthumb/data-src for lazy-loaded images, fallback to src
            thumbnail = (
                    img_tag.attributes.get("data-mediumthumb") or
                    img_tag.attributes.get("data-src") or
                    img_tag.attributes.get("src")
            )

        # 6. Extract Views
        views_var = block.css_first("span.views var")
        views = views_var.text(strip=True) if views_var else None

        # 7. Extract Publish Date
        added_var = block.css_first("var.added")
        publish_date = added_var.text(strip=True) if added_var else None

        # 8. Extract Author Details (works for both /channels/ and /pornstar/)
        author_link = None
        author_information = None
        author_tag = block.css_first("div.usernameWrap a")

        if author_tag:
            author_href = author_tag.attributes.get("href")
            if author_href:
                # Prepend domain if path is relative
                author_link = f"https://www.pornhub.com{author_href}" if author_href.startswith(
                    "/") else author_href

            author_name = author_tag.text(strip=True)
            if author_name:
                author_information = {"name": author_name}

        # Append structured payload mapped to Dataclass expectations
        results.append({
            "url": url,
            "video_id": video_id,
            "title": title,
            "duration": duration,
            "thumbnail": thumbnail,
            "views": views,
            "publish_date": publish_date,
            "author_link": author_link,
            "author_information": author_information
        })

    return results


def extractor_model_videos(html_content: str) -> list:
    results = []
    parser = LexborHTMLParser(html_content)

    # Target the specific container for model/channel videos
    video_container = parser.css_first("#mostRecentVideosSection")

    # Fallback if the specific container isn't found
    if not video_container:
        video_blocks = parser.css("li.pcVideoListItem, li.videoBox")
    else:
        video_blocks = video_container.css("li.pcVideoListItem, li.videoBox")

    for block in video_blocks:

        # 1. Extract URL
        a_tag = block.css_first("a[href*='view_video']")
        if not a_tag:
            continue

        href = a_tag.attributes.get("href")
        if not href:
            continue

        url = f"https://www.pornhub.com{href}"
        if any(r["url"] == url for r in results):
            continue

        # 2. Extract Video ID (viewkey)
        match = re.search(r"viewkey=([^&#]+)", url)
        video_id = match.group(1) if match else None

        # 3. Extract Title
        title = ""
        title_link = block.css_first("span.title a")
        if title_link:
            title = title_link.attributes.get("title") or title_link.text(strip=True)

        if not title:
            img_tag = block.css_first("img")
            if img_tag:
                title = img_tag.attributes.get("alt", "")

        # 4. Extract Duration
        duration_var = block.css_first("var.duration")
        duration = duration_var.text(strip=True) if duration_var else None

        # 5. Extract Thumbnail
        img_tag = block.css_first("img")
        thumbnail = None
        if img_tag:
            # Prioritize high-quality lazy-loaded sources, fallback to standard src
            thumbnail = (
                    img_tag.attributes.get("data-mediumthumb") or
                    img_tag.attributes.get("data-src") or
                    img_tag.attributes.get("src")
            )

        # 6. Extract Views
        views_var = block.css_first("span.views var")
        views = views_var.text(strip=True) if views_var else None

        # 7. Extract Publish Date
        added_var = block.css_first("var.added")
        publish_date = added_var.text(strip=True) if added_var else None

        # 8. Extract Author Details
        author_link = None
        author_information = None
        author_tag = block.css_first("div.usernameWrap a")

        if author_tag:
            author_href = author_tag.attributes.get("href")
            if author_href:
                author_link = f"https://www.pornhub.com{author_href}" if author_href.startswith(
                    "/") else author_href

            author_name = author_tag.text(strip=True)
            if author_name:
                author_information = {"name": author_name}

        # Append structured payload
        results.append({
            "url": url,
            "video_id": video_id,
            "title": title,
            "duration": duration,
            "thumbnail": thumbnail,
            "views": views,
            "publish_date": publish_date,
            "author_link": author_link,
            "author_information": author_information
        })
    print(f"Results: {results}")
    return results


def extractor_videos(html_content: str) -> list:
    results = []
    lexbor = LexborHTMLParser(html_content)
    
    # Try different sections
    video_blocks = lexbor.css("li > div.pcVideoListItem, li > div.videoBox")

    if not video_blocks:
        # Fallback to finding all link tags if blocks aren't found
        a_tags = lexbor.css('a[href^="/view_video.php?viewkey="]')
        for a_tag in a_tags:
            href = a_tag.attributes.get("href")
            if not isinstance(href, str) or not href:
                continue
            url = f"https://www.pornhub.com{href}"
            if any(r["url"] == url for r in results):
                continue
            results.append({"url": url})
        logger.debug(f"extractor_videos extracted {len(results)} videos (fallback)")
        return results

    for block in video_blocks:
        # Find the link tag which contains the URL and title
        a_tag = block.css_first("a[href*='view_video']")
        if not a_tag:
            continue

        href = a_tag.attributes.get("href")
        if not href:
            continue

        url = f"https://www.pornhub.com{href}"
        if any(r["url"] == url for r in results):
            continue

        title = a_tag.attributes.get("title") or (a_tag.css_first("img").attributes.get("alt") if a_tag.css_first("img") else "")
        if not title:
            # Try finding title in a separate link or span
            title_link = block.css_first("a.title") or block.css_first("span.title")
            title = title_link.text(strip=True) if title_link else ""

        # Extract duration if available
        duration_var = block.css_first("var.duration")
        duration = duration_var.text(strip=True) if duration_var else None

        # Extract thumbnail
        img_tag = block.css_first("img")
        thumb = img_tag.attributes.get("data-src") or img_tag.attributes.get("src") if img_tag else None

        results.append({
            "url": url,
            "title": title,
            "duration": duration,
            "thumbnail": thumb,
        })

    logger.debug(f"extractor_videos extracted {len(results)} videos")
    return results


def extractor_playlist(html_content: str) -> list:
    results = []
    parser = LexborHTMLParser(html_content)

    # Target the specific playlist container
    playlist_container = parser.css_first(
        "div.videos.row-5-thumbs.search-video-thumbs.scrollLazyload.js-videoPlaylist.viewPlaylist")

    if not playlist_container:
        # Fallback if the specific container isn't found
        video_blocks = parser.css("li.pcVideoListItem, li.videoBox")
    else:
        video_blocks = playlist_container.css("li.pcVideoListItem, li.videoBox")

    for block in video_blocks:
        # 1. Extract URL (and skip duplicates)
        a_tag = block.css_first("a[href*='view_video']")
        if not a_tag:
            continue

        href = a_tag.attributes.get("href")
        if not href:
            continue

        url = f"https://www.pornhub.com{href}"
        if any(r["url"] == url for r in results):
            continue

        # 2. Extract Video ID (viewkey)
        match = re.search(r"viewkey=([^&#]+)", url)
        video_id = match.group(1) if match else None

        # 3. Extract Title
        title = ""
        title_link = block.css_first("span.title a")
        if title_link:
            title = title_link.attributes.get("title") or title_link.text(strip=True)

        if not title:
            img_tag = block.css_first("img")
            if img_tag:
                title = img_tag.attributes.get("alt", "")

        # 4. Extract Duration
        duration_var = block.css_first("var.duration")
        duration = duration_var.text(strip=True) if duration_var else None

        # 5. Extract Thumbnail
        img_tag = block.css_first("img")
        thumbnail = None
        if img_tag:
            # Prefer data-mediumthumb/data-src for lazy-loaded images, fallback to src
            thumbnail = (
                    img_tag.attributes.get("data-mediumthumb") or
                    img_tag.attributes.get("data-src") or
                    img_tag.attributes.get("src")
            )

        # 6. Extract Views
        views_var = block.css_first("span.views var")
        views = views_var.text(strip=True) if views_var else None

        # 7. Extract Publish Date
        added_var = block.css_first("var.added")
        publish_date = added_var.text(strip=True) if added_var else None

        # 8. Extract Author Details
        author_link = None
        author_information = None
        author_tag = block.css_first("div.usernameWrap a")

        if author_tag:
            author_href = author_tag.attributes.get("href")
            if author_href:
                author_link = f"https://www.pornhub.com{author_href}" if author_href.startswith(
                    "/") else author_href

            author_name = author_tag.text(strip=True)
            if author_name:
                author_information = {"name": author_name}

        # Append structured payload mapping to Dataclass expectations
        results.append({
            "url": url,
            "video_id": video_id,
            "title": title,
            "duration": duration,
            "thumbnail": thumbnail,
            "views": views,
            "publish_date": publish_date,
            "author_link": author_link,
            "author_information": author_information
        })

    return results


def extractor_users(html_content: str) -> list:
    """
    Extractor for users, models and pornstars.
    """
    lexbor = LexborHTMLParser(html_content)
    unique_urls = set()
    # Matches the user links in the subscriptions/followers pages
    for a_tag in lexbor.css("a.userLink[href]"):
        href = a_tag.attributes.get("href")
        if isinstance(href, str) and href.startswith("/"):
            url = f"https://www.pornhub.com{href}"
            unique_urls.add(url)

    links = [{"url": url} for url in unique_urls]
    return links
