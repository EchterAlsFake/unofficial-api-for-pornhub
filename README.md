<h1 align="center">PornHub API</h1>
<p align="center"><em>An asynchronous Python API wrapper and scraper for pornhub.com</em></p>

<div align="center">
    <a href="https://pepy.tech/project/phub"><img src="https://static.pepy.tech/badge/phub" alt="Downloads"></a> +
    <a href="https://pepy.tech/project/unofficial-api-for-pornhub"><img src="https://static.pepy.tech/badge/unofficial-api-for-pornhub" alt="Downloads"></a>
    <a href="https://badge.fury.io/py/phub"><img src="https://badge.fury.io/py/phub.svg" alt="PyPI version" height="18"></a>
    <a href="https://echteralsfake.me/ci/PHUB/badge.svg"><img src="https://echteralsfake.me/ci/PHUB/badge.svg" alt="API Tests"/></a>
    </div>

# Disclaimer
> [!IMPORTANT]
> This is an unofficial and unaffiliated project. Please read the full disclaimer before use:
> **[DISCLAIMER.md](https://github.com/EchterAlsFake/API_Docs/blob/master/Disclaimer.md)**
>
> By using this project you agree to comply with the target site's rules, copyright/licensing requirements,
> and applicable laws. Do not use it to bypass access controls or scrape at disruptive rates.

---

# Features

| Category | Details |
|---|---|
| **Video/Short/GIF/Album Fetching** | Fetch videos, shorts, GIFs, or albums with rich metadata and configurations |
| **Pornstar/User Profiles** | Fetch channel/user/model profiles including uploads, GIFs, and subscription lists |
| **Photo Albums** | Fetch album details, list photos, and download photos page-by-page |
| **Playlists & Channels** | Fetch playlist/channel videos and details with concurrency control |
| **Video Search** | Search videos with advanced filters (production type, sorting, duration limits) |
| **HubTraffic Search** | Scrape hubtraffic videos with sorting and period filters |
| **User Accounts & Login** | Access personalized history, recommendations, favorites, feed, and subscriptions via login credentials |
| **Async-First** | Fully asynchronous (`async` / `await`) built on top of `asyncio` |
| **Built-in Caching** | Automatic response caching with configurable limits to reduce redundant network requests |
| **CLI Support** | Command-line interface for quick downloads — run `phub -h` for options |
| **Type Safety** | Comprehensive type hinting and `dataclass`-based models throughout |

#### Networking Features

The networking layer is provided by the [`eaf_base_api`](https://github.com/EchterAlsFake/eaf_base_api) package and is fully configurable through `RuntimeConfig`:

| Feature | Description                                                                     |
|---|---------------------------------------------------------------------------------|
| **HTTP/1.1, HTTP/2, HTTP/3** | Configurable HTTP version (`v1`, `v2`, `v3` — defaults to HTTP/2)               |
| **Browser Impersonation** | Built-in browser fingerprint impersonation via `curl_cffi` (defaults to Chrome) |
| **Custom JA3 Fingerprint** | Override the TLS fingerprint with a custom JA3 string for advanced use cases    |
| **Proxy Support** | All proxy types supported (HTTP, HTTPS, SOCKS4, SOCKS5)                         |
| **Proxy Authentication** | Username/password authentication for proxies                                    |
| **Bandwidth Limiting** | Set a maximum download speed in MB/s (e.g., `2.0`, `3.5`)                       |
| **DNS over HTTPS** | Route DNS queries over HTTPS for privacy and bypassing DNS-level blocks         |
| **SSL Verification** | Toggle SSL certificate verification on or off                                   |
| **Request Delay** | Configurable delay between requests to respect rate limits                      |
| **Concurrency Control** | Tune video and page concurrency independently for optimal throughput            |

---

# Supported Platforms
This API has been tested and confirmed working on:

- Windows 11 (x64) 
- macOS Sequoia (x86_64)
- Linux (Arch) (x86_64)
- Android 16 (aarch64)

---

# Installation

> [!WARNING]
> The installation from Git is **temporary**. The package will be migrated to PyPI within the next week.

```bash
pip install unofficial-api-for-pornhub
```

---

# Quickstart

### Have a look at the [Documentation](https://github.com/EchterAlsFake/API_Docs/blob/master/Porn_APIs/PHUB.md) for more details

> [!NOTE]
> PornHub API can also be used from the command line. Do: pornhub_api -h to see the options

```python
import asyncio
from pornhub_api import Client, DownloadConfigHLS

async def main():
    # Initialize a Client object
    client = Client()
    
    # Fetch a video
    video_object = await client.get_video("<insert_url_here>")
    
    # Information from Video objects
    print(video_object.title)

    # Download the video
    config = DownloadConfigHLS(quality="best", path="./") # More options in the documentation
    await video_object.download(config)

if __name__ == "__main__":
    asyncio.run(main())
```

---
# Support the Project ❤️

I develop all my projects entirely for free because I enjoy it and want to keep them accessible.
If you find my work useful, please consider supporting me with a small donation — even 1 € makes a big difference and keeps me motivated!

### ☕ Ko-fi
<a href="https://ko-fi.com/EchterAlsFake">https://ko-fi.com/EchterAlsFake</a>

### 💳 PayPal
<a href="https://paypal.me/EchterAlsFake">https://paypal.me/EchterAlsFake</a>

### 🪙 Crypto (350+ currencies supported)
<a href="https://nowpayments.io/donation?api_key=65b1acaf-735d-4d4b-b3d6-c2237c0b57e3" target="_blank" rel="noreferrer noopener">
   <img src="https://nowpayments.io/images/embeds/donation-button-black.svg" alt="Crypto donation button by NOWPayments">
</a>

---

# Contribution
Do you see any issues or having some feature requests? Simply open an Issue or talk
in the discussions.

Pull requests are also welcome.

# License
PHUB uses LGPLv3. See the `LICENSE` file.

This repository was initiated and maintained by [Egsagon](https://github.com/Egsagon)
He doesn't have any time to maintain this and transferred me the ownership.
I'll do my best to maintain this repository functional.
