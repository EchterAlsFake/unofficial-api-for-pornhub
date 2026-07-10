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
        if href.startswith("/gif/") and any(char.isdigit() for char in href):
            full_url = f"https://www.pornhub.com{href}"
            unique_urls.add(full_url)

    links = [{"url": url} for url in unique_urls]
    return links


def extractor_model(html_content: str) -> list:
    urls = []

    soup = LexborHTMLParser(html_content)
    soup1 = soup.css_first("div.profileContentLeft")
    video_keys = [key.attributes.get("data-video-vkey") for key in soup1.css("[data-video-vkey]")]

    for key in video_keys:
        urls.append({"url": f"https://www.pornhub.com/view_video.php?viewkey={key}"})

    logger.debug(f"extractor_model extracted {len(urls)} urls")
    return urls


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
            url = f"https://www.pornhub.com{href}"
            if any(r["url"] == url for r in results):
                continue
            results.append({"url": url})
        logger.debug(f"extractor_videos extracted {len(results)} videos (fallback)")
        return results

    for block in video_blocks:
        try:
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
                "thumb": thumb,
                "from_search": True
            })
        except Exception:
            continue

    logger.debug(f"extractor_videos extracted {len(results)} videos")
    return results


def extractor_hubtraffic(json_content: str) -> list:
    """
    Extractor for HubTraffic API (Webmaster API) which returns JSON.
    Returns a list of dictionaries containing video data.
    """
    try:
        data = json.loads(json_content)
        videos = data.get("videos")
        if not videos:
            return []

        logger.debug(f"extractor_hubtraffic extracted {len(videos)} videos")
        # We return the whole dict for each video so the iterator can pass it to Video object
        return videos
    except json.JSONDecodeError:
        return []


def extractor_videos_from_playlist_page(html_content: str) -> list:
    unique_urls = set()
    lexbor = LexborHTMLParser(html_content)
    
    # Search for all 'a' tags with an href containing "/view_video.php?viewkey="
    # Use re.compile for regex matching in find_all
    for a_tag in lexbor.css("a[href*='viewkey=']"):
        href = a_tag.attributes.get("href")
        if href:
            # Ensure the URL is absolute
            if not href.startswith("https://www.pornhub.com"):
                unique_urls.add(f"https://www.pornhub.com{href}")
            else:
                unique_urls.add(href)

    links = [{"url": url} for url in unique_urls]
    return links


def extractor_videos_playlist(content: str) -> list:
    unique_urls = set()
    html_to_parse = None

    try:
        # Attempt to parse as JSON first
        data = json.loads(content)
        html_to_parse = data.get("html")
    except json.JSONDecodeError:
        # If it's not JSON, assume it's raw HTML
        html_to_parse = content

    if html_to_parse:
        lexbor = LexborHTMLParser(html_to_parse)
        # Search for common patterns for video links in playlist chunks
        # These patterns are derived from inspecting typical Pornhub playlist HTML
        
        # Search for all 'a' tags with an href containing "/view_video.php?viewkey="
        for a_tag in lexbor.css("a[href*='viewkey=']"):
            href = a_tag.attributes.get("href")
            if href:
                if not href.startswith("https://www.pornhub.com"):
                    unique_urls.add(f"https://www.pornhub.com{href}")
                else:
                    unique_urls.add(href)

    links = [{"url": url} for url in unique_urls]
    return links


def extractor_users(html_content: str) -> list:
    """
    Extractor for users, models and pornstars.
    """
    lexbor = LexborHTMLParser(html_content)
    unique_urls = set()
    # Matches the user links in the subscriptions/followers pages
    for a_tag in lexbor.css("a.userLink[href]"):
        href = a_tag.attributes.get("href")
        if href.startswith("/"):
            url = f"https://www.pornhub.com{href}"
            unique_urls.add(url)

    links = [{"url": url} for url in unique_urls]
    return links