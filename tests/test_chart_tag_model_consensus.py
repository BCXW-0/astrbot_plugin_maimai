from __future__ import annotations

import unittest

from astrbot_plugin_maimaidx.libraries.chart_tags.model_consensus import (
    CURRENT_DIALOGUE_MODEL_ID,
    DEFAULT_ASTRBOT_PROVIDER_ID,
    compare_model_results,
    is_accepted_model_review,
    parse_model_result,
)


def _result(provider: str, tags: list[str]) -> dict[str, object]:
    return {"provider": provider, "status": "completed", "tags": tags}


class ChartTagModelConsensusTest(unittest.TestCase):
    def test_accepts_only_current_dual_provider_pair(self) -> None:
        first = _result(CURRENT_DIALOGUE_MODEL_ID, ["双押"])
        second = _result(DEFAULT_ASTRBOT_PROVIDER_ID, ["双押"])
        review = {"first": first, "second": second, "comparison": compare_model_results(first, second)}
        self.assertTrue(is_accepted_model_review(review))

        old_second = _result("legacy_provider", ["双押"])
        old_review = {
            "first": first,
            "second": old_second,
            "comparison": compare_model_results(first, old_second),
        }
        self.assertFalse(is_accepted_model_review(old_review))

    def test_keeps_exact_three_model_history(self) -> None:
        first = _result(CURRENT_DIALOGUE_MODEL_ID, ["双押"])
        second = _result("historical_second", ["双押"])
        third = _result("historical_third", ["双押"])
        review = {
            "first": first,
            "second": second,
            "third": third,
            "comparison": compare_model_results(first, second, third),
        }
        self.assertTrue(is_accepted_model_review(review))

    def test_rejects_non_json_and_tool_call_text(self) -> None:
        self.assertEqual(parse_model_result("```json\n{\"tags\": []}\n```", provider="test")["status"], "invalid_response")
        self.assertEqual(parse_model_result("说明文字 {\"tags\": []}", provider="test")["status"], "invalid_response")
        self.assertEqual(parse_model_result("{\"tags\": [], \"raw\": \"custom_tool_call\"}", provider="test")["status"], "invalid_response")


if __name__ == "__main__":
    unittest.main()
