import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pornhub_api.api import create_parser, run_main


class TestCLI(unittest.IsolatedAsyncioTestCase):
    def test_parser_defaults(self):
        parser = create_parser()
        args = parser.parse_args(["--output", "/tmp/out"])
        self.assertEqual(args.quality, "best")
        self.assertEqual(args.no_title, "False")
        self.assertEqual(args.output, "/tmp/out")
        self.assertIsNone(args.download)
        self.assertIsNone(args.file)

    def test_parser_custom_args(self):
        parser = create_parser()
        args = parser.parse_args([
            "--download", "https://www.pornhub.com/view_video.php?viewkey=ph12345",
            "--output", "/tmp/out",
            "--quality", "worst",
            "--no-title", "True"
        ])
        self.assertEqual(args.download, "https://www.pornhub.com/view_video.php?viewkey=ph12345")
        self.assertEqual(args.output, "/tmp/out")
        self.assertEqual(args.quality, "worst")
        self.assertEqual(args.no_title, "True")

    def test_parser_no_title_flag(self):
        parser = create_parser()
        args = parser.parse_args(["--output", "/tmp/out", "--no-title"])
        self.assertEqual(args.no_title, "True")

    async def test_run_main_download(self):
        mock_video = MagicMock()
        mock_video.title = "Sample Video"
        mock_video.download = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_video = AsyncMock(return_value=mock_video)

        with patch("pornhub_api.api.Client", return_value=mock_client),              patch("pornhub_api.api._cli_process_url", new_callable=AsyncMock) as mock_process:
            await run_main(["--download", "https://www.pornhub.com/view_video.php?viewkey=ph12345", "--output", "/tmp/out"])

        mock_process.assert_awaited_once()

    async def test_run_main_file(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            f.write("https://pornhub.com/v1\nhttps://pornhub.com/v2\n")
            f.flush()
            temp_name = f.name

        try:
            with patch("pornhub_api.api.Client"),                  patch("pornhub_api.api._cli_process_url", new_callable=AsyncMock) as mock_process:
                await run_main(["--file", temp_name, "--output", "/tmp/out"])

            self.assertEqual(mock_process.await_count, 2)
        finally:
            if os.path.exists(temp_name):
                os.remove(temp_name)

    async def test_real_download(self):
        """Integration test: verifies real video download via CLI."""
        url = "https://www.pornhub.com/view_video.php?viewkey=ph618ed49fbee04"
        with tempfile.TemporaryDirectory() as tmp_dir:
            await run_main(["--download", url, "--output", tmp_dir, "--quality", "worst"])
            files = [f for f in os.listdir(tmp_dir) if not f.endswith(".tmp")]
            self.assertTrue(len(files) > 0, "Expected downloaded video file in output directory")


if __name__ == "__main__":
    unittest.main()
