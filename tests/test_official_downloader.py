from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_maimaidx.libraries.chart_tags import official_downloader


class OfficialChartDownloaderProgressTest(unittest.TestCase):
    def test_download_reports_selected_count_as_total(self) -> None:
        payload = {
            "data": [
                {
                    "id": "10001",
                    "title": "Test Song",
                    "ds": [12.6],
                    "basic_info": {"title": "Test Song", "genre": "maimai"},
                }
            ]
        }
        listing = {"files": [{"name": "chart.txt", "url": "/chart/10001.txt"}]}
        chart_text = "\n".join(
            (
                "&title=Test Song",
                "&shortid=10001",
                "&wholebpm=120",
                "&lv_4=12.6",
                "&inote_4=1,",
            )
        )
        states: list[dict[str, object]] = []

        with tempfile.TemporaryDirectory(dir=official_downloader.Root) as temp_dir:
            downloader = official_downloader.OfficialChartDownloader(Path(temp_dir))
            with patch.object(
                official_downloader,
                "_json_request",
                side_effect=[payload, listing],
            ), patch.object(
                official_downloader,
                "_binary_request",
                return_value=chart_text.encode("utf-8"),
            ):
                result = downloader.download(progress=lambda state: states.append(state))

        self.assertEqual(result["selected"], 1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["processed"], 1)
        self.assertTrue(states)
        self.assertEqual(states[0]["total"], 1)
        self.assertEqual(states[-1]["total"], 1)


if __name__ == "__main__":
    unittest.main()
