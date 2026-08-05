from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_maimaidx.libraries.chart_tags.auto_tagger import LocalChartCatalog
from astrbot_plugin_maimaidx.libraries.chart_tags.local.maidata_parser import (
    MaidataChart,
    NoteEvent,
    parse_maidata_metadata,
)
from astrbot_plugin_maimaidx.libraries.chart_tags.local.structure_tagger import (
    extract_features,
    extract_features_with_windows,
)


class ChartTagOptimizationTest(unittest.TestCase):
    def test_feature_bundle_matches_public_feature_output(self) -> None:
        chart = MaidataChart(
            diff_id=4,
            level_index=2,
            ds=12.6,
            bpm=120.0,
            events=[
                NoteEvent(time=index * 0.5, kind="tap", buttons=(str(index % 8 + 1),))
                for index in range(12)
            ],
        )
        features, windows = extract_features_with_windows(chart)
        self.assertEqual(features, extract_features(chart))
        self.assertTrue(windows)
        self.assertTrue(windows[0]["sequence"])

    def test_metadata_parser_keeps_chart_metadata_without_events(self) -> None:
        text = "\n".join(
            (
                "&title=Optimization Test",
                "&shortid=90001",
                "&artist=Artist",
                "&wholebpm=120",
                "&lv_4=12.6",
                "&des_4=Tester",
                "&inote_4=(180)1,2,",
            )
        )
        song = parse_maidata_metadata(text)
        self.assertEqual(song.short_id, "90001")
        self.assertEqual(song.title, "Optimization Test")
        self.assertEqual(song.charts[2].ds, 12.6)
        self.assertEqual(song.charts[2].bpm, 180.0)
        self.assertEqual(song.charts[2].events, [])

    def test_catalog_reuses_unchanged_files_and_indexes_new_files(self) -> None:
        text = "\n".join(
            (
                "&title=Catalog Test",
                "&shortid=90002",
                "&wholebpm=120",
                "&lv_4=12.6",
                "&inote_4=1,",
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "90002_Catalog Test.txt").write_text(text, encoding="utf-8")
            catalog = LocalChartCatalog(root)
            first = catalog.refs()
            second = catalog.refs()
            self.assertEqual(first, second)
            (root / "90003_Catalog Test.txt").write_text(text.replace("90002", "90003"), encoding="utf-8")
            self.assertEqual(len(catalog.refs()), 2)


if __name__ == "__main__":
    unittest.main()
