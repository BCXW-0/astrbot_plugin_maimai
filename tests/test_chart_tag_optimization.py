from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_maimaidx.libraries.chart_tags.auto_tagger import LocalChartCatalog, _select_model_tags
from astrbot_plugin_maimaidx.libraries.chart_tags.training_dataset import (
    _assert_model_quality,
    _ensure_training_label_coverage,
    _supervision_matrix,
    _target_matrix,
)
from astrbot_plugin_maimaidx.libraries.chart_tags.constants import ALLOWED_TAGS
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
    def test_runtime_selection_preserves_high_confidence_timing_labels(self) -> None:
        scores = {
            "节奏": 0.90,
            "爆发": 0.88,
            "跳拍": 0.86,
            "延迟星星": 0.73,
            "拆弹": 0.71,
            "协调": 0.99,
            "扫键": 0.98,
            "双押": 0.97,
        }
        tags = _select_model_tags(scores)

        self.assertLessEqual(len(tags), 5)
        self.assertIn("节奏", tags)
        self.assertIn("爆发", tags)
        self.assertEqual(len({"延迟星星", "拆弹"} & set(tags)), 1)

    def test_runtime_selection_keeps_burst_and_star_family_before_generic_structure(self) -> None:
        tags = _select_model_tags({
            "节奏": 0.91,
            "跳拍": 0.90,
            "爆发": 0.88,
            "延迟星星": 0.64,
            "扫键": 0.99,
            "双押": 0.98,
        })

        self.assertIn("爆发", tags)
        self.assertIn("延迟星星", tags)
        self.assertLessEqual(len(tags), 5)

    def test_runtime_selection_keeps_original_candidate_limit(self) -> None:
        tags = _select_model_tags({tag: 0.9 for tag in ALLOWED_TAGS})

        self.assertEqual(len(tags), 5)
        self.assertEqual(len(set(tags)), 5)

    def test_runtime_selection_does_not_fill_slots_with_generic_tags(self) -> None:
        tags = _select_model_tags({
            "节奏": 0.91,
            "跳拍": 0.90,
            "扫键": 0.99,
            "双押": 0.94,
            "爬梯交互": 0.88,
            "错位": 0.86,
        })

        self.assertIn("爬梯交互", tags)
        self.assertIn("错位", tags)
        self.assertNotIn("扫键", tags)

    def test_runtime_selection_protects_verified_collision_tag(self) -> None:
        tags = _select_model_tags(
            {
                "节奏": 0.9,
                "跳拍": 0.88,
                "爆发": 0.82,
                "拆弹": 0.78,
                "留尾": 0.99,
                "撞尾": 0.71,
            },
            protected_tags=["撞尾"],
        )

        self.assertIn("撞尾", tags)
        self.assertLessEqual(len(tags), 5)

    def test_training_targets_are_not_display_capped(self) -> None:
        records = [{"training_tags": ["双押"], "final_tags": []}]
        targets = _target_matrix(records)
        self.assertEqual(float(targets[0, ALLOWED_TAGS.index("双押")]), 1.0)

    def test_xls_candidate_omitted_from_display_is_weak_positive(self) -> None:
        records = [{
            "training_tags": ["双押"],
            "raw_tags": ["爆发"],
            "difficulty_tags": ["爆发"],
            "validation": {"confidence": 1.0},
            "external_tags": [],
            "validated_tags": [],
        }]
        targets = _target_matrix(records)
        weights = _supervision_matrix(records)
        self.assertEqual(float(targets[0, ALLOWED_TAGS.index("爆发")]), 1.0)
        self.assertLessEqual(float(weights[0, ALLOWED_TAGS.index("爆发")]), 0.35)

    def test_conflicting_external_tag_is_not_a_strong_negative(self) -> None:
        records = [{
            "training_tags": ["双押"],
            "validation": {"confidence": 0.0},
            "external_tags": ["管子"],
            "validated_tags": [],
        }]
        weights = _supervision_matrix(records)
        self.assertLess(float(weights[0, ALLOWED_TAGS.index("管子")]), 1.0)
        self.assertGreater(float(weights[0, ALLOWED_TAGS.index("双押")]), 0.0)

    def test_quality_gate_rejects_zero_prediction_model(self) -> None:
        with self.assertRaises(ValueError):
            _assert_model_quality(
                {"micro_precision": 0.0},
                {"micro_precision": 0.0, "micro_f1": 0.0, "predicted_positive_cells": 0},
            )

    def test_coverage_balancing_preserves_multiple_rare_labels(self) -> None:
        records = []
        for index in range(6):
            tags = ["双押", "管子", "留尾", "防蹭", "扫键"]
            if index == 0:
                tags[1] = "撞尾"
            records.append({
                "record_key": str(index),
                "raw_tags": ["节奏", "撞尾"],
                "difficulty_tags": ["节奏", "撞尾"],
                "candidate_scores": {"节奏": 1.0, "撞尾": 1.0},
                "difficulty_scores": {"节奏": 1.0, "撞尾": 1.0},
                "tag_evidence": {"节奏": [{"raw": "1,2,"}], "撞尾": [{"raw": "1-2"}]},
                "model_consensus": {"first_tags": ["双押"], "comparison": {"required_models": 2}},
                "validation": {"intersection_tags": [], "candidate_intersection_tags": []},
                "training_tags": tags,
                "training_tag_scores": {tag: 1.0 for tag in tags},
                "training_tag_sources": {},
            })

        _ensure_training_label_coverage(records)

        self.assertGreaterEqual(sum("节奏" in record["training_tags"] for record in records), 2)
        self.assertGreaterEqual(sum("撞尾" in record["training_tags"] for record in records), 2)
        self.assertTrue(all(len(record["training_tags"]) <= 5 for record in records))

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
