import unittest

from astrbot_plugin_maimaidx.libraries.chart_tags.external_evidence import _difficulty_matches, _overlap, _search_results


class ExternalEvidenceTest(unittest.TestCase):
    def test_search_results_use_video_card_alt_title(self) -> None:
        body = (
            '<a href="//www.bilibili.com/video/BV18o9nBVEtQ/">'
            '<div class="stats">2.6万 45 02:30</div>'
            '<img alt="[maimai谱面确认] 雑魚 MASTER">'
            '</a>'
        )

        self.assertEqual(_search_results(body), [{
            "bvid": "BV18o9nBVEtQ",
            "title": "[maimai谱面确认] 雑魚 MASTER",
            "url": "https://www.bilibili.com/video/BV18o9nBVEtQ/",
        }])

    def test_validation_uses_model_coverage_and_keeps_jaccard(self) -> None:
        coverage, intersection, jaccard = _overlap(["协调", "双押"], ["协调", "双押", "定位"])
        self.assertEqual(intersection, ["协调", "双押"])
        self.assertEqual(coverage, 1.0)
        self.assertEqual(jaccard, 2 / 3)

    def test_rejects_explicitly_wrong_difficulty(self) -> None:
        self.assertFalse(_difficulty_matches("Master", "采配の刻 EXPERT", "红谱 11+"))
        self.assertTrue(_difficulty_matches("Master", "采配の刻 MASTER", "紫谱 13+"))
        self.assertTrue(_difficulty_matches("Re:Master", "Rooftop Run Re:MASTER", "白谱 13"))


if __name__ == "__main__":
    unittest.main()
