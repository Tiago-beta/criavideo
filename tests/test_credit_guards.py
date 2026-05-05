import unittest

from fastapi import HTTPException

from app.routers.credits import build_insufficient_credits_message, deduct_credits
from app.services.credit_pricing import (
    estimate_local_video_processing_credits,
    estimate_similar_analysis_credits,
    estimate_similar_previews_credits,
    estimate_similar_scene_credits,
)


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeDb:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []
        self.commit_calls = 0

    async def execute(self, query, params):
        self.calls.append((str(query), params))
        if not self._results:
            raise AssertionError("Unexpected execute call")
        return self._results.pop(0)

    async def commit(self):
        self.commit_calls += 1


class TestCreditGuards(unittest.IsolatedAsyncioTestCase):
    async def test_deduct_credits_uses_atomic_update(self):
        db = _FakeDb([_FakeScalarResult(25)])

        remaining = await deduct_credits(db, user_id=7, amount=5)

        self.assertEqual(remaining, 25)
        self.assertEqual(len(db.calls), 1)
        self.assertIn("RETURNING credits", db.calls[0][0])
        self.assertEqual(db.commit_calls, 1)

    async def test_deduct_credits_returns_user_friendly_insufficient_message(self):
        db = _FakeDb([_FakeScalarResult(None), _FakeScalarResult(3)])

        with self.assertRaises(HTTPException) as ctx:
            await deduct_credits(db, user_id=9, amount=40)

        self.assertEqual(ctx.exception.status_code, 402)
        self.assertEqual(ctx.exception.detail, build_insufficient_credits_message(3, 40))
        self.assertEqual(db.commit_calls, 0)


class TestSimilarCreditPricing(unittest.TestCase):
    def test_similar_analysis_estimate_distinguishes_general_and_scene_modes(self):
        general = estimate_similar_analysis_credits(duration_seconds=15, analysis_mode="general")
        scene = estimate_similar_analysis_credits(duration_seconds=15, analysis_mode="scene")

        self.assertEqual(general["breakdown"]["mode"], "similar_analysis")
        self.assertEqual(general["breakdown"]["analysis_mode"], "general")
        self.assertEqual(scene["breakdown"]["analysis_mode"], "scene")
        self.assertGreater(scene["credits_needed"], general["credits_needed"])

    def test_similar_preview_estimate_sums_scene_costs(self):
        single = estimate_similar_scene_credits(engine="grok", duration_seconds=5)
        combined = estimate_similar_previews_credits(engine="grok", scene_durations=[5, 10])

        self.assertGreater(combined["credits_needed"], single["credits_needed"])
        self.assertEqual(combined["breakdown"]["scene_count"], 2)
        self.assertEqual(combined["breakdown"]["scene_durations"], [5.0, 10.0])

    def test_local_processing_estimate_has_processing_floor(self):
        estimate = estimate_local_video_processing_credits(duration_seconds=30)

        self.assertGreaterEqual(estimate["credits_needed"], 8)
        self.assertEqual(estimate["breakdown"]["mode"], "local_video_processing")


if __name__ == "__main__":
    unittest.main()