import unittest

from pornhub_api.api import Short


class MetadataExportTests(unittest.TestCase):
    def test_explicit_short_cannot_use_an_unrelated_item(self):
        content = '<script>var JSON_SHORTIES = [{"vkey":"other", "videoTitle":"Other scene"}];</script>'
        with self.assertRaises(ValueError):
            Short._extract_html(content, url="https://www.pornhub.com/shorties/requested")
