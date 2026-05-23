import unittest
from unittest.mock import AsyncMock, patch

from app.services.seedance_video import (
    SEEDANCE_FAST_I2V_MODEL,
    _atlas_assets_not_ready,
    _normalize_seedance_engine_variant,
    _prepare_vidu_reference,
    _resolve_seedance_generation_profile,
)


class TestAtlasAssetReadiness(unittest.TestCase):
    def test_detects_atlas_provider_asset_replication_error(self):
        details = (
            '{"code":425,"msg":"no provider has all atlas assets ready '
            '(atlas_ids=[atlas-asset-a75b831d5389], candidates=[deyun-vidu,deyun-vidu-apicoco])"}'
        )

        self.assertTrue(_atlas_assets_not_ready(425, details))

    def test_ignores_other_errors(self):
        self.assertFalse(_atlas_assets_not_ready(400, "bad request"))
        self.assertFalse(_atlas_assets_not_ready(425, "another transient upstream issue"))


class TestPrepareViduReference(unittest.IsolatedAsyncioTestCase):
    async def test_returns_direct_upload_url(self):
        with patch(
            "app.services.seedance_video._upload_media_to_atlas",
            new=AsyncMock(return_value="https://static.atlascloud.ai/media/images/example.jpg"),
        ):
            result = await _prepare_vidu_reference("frame.png", "api-key")

        self.assertEqual(result, "https://static.atlascloud.ai/media/images/example.jpg")

    async def test_rejects_non_http_references(self):
        with patch(
            "app.services.seedance_video._upload_media_to_atlas",
            new=AsyncMock(return_value="asset://atlas-asset-123"),
        ):
            with self.assertRaisesRegex(RuntimeError, "uploadMedia nao retornou URL publica"):
                await _prepare_vidu_reference("frame.png", "api-key")


class TestSeedanceProfiles(unittest.TestCase):
    def test_normalizes_swapped_display_aliases(self):
        self.assertEqual(_normalize_seedance_engine_variant("mega15"), "mega15")
        self.assertEqual(_normalize_seedance_engine_variant("Lite 2.0 Fast"), "mega15")
        self.assertEqual(_normalize_seedance_engine_variant("Seedance 2.0 Fast"), "mega15")
        self.assertEqual(_normalize_seedance_engine_variant("Mega 1.5 Real"), "lite2")

    def test_resolves_mega15_i2v_profile(self):
        model_id, duration, resolution = _resolve_seedance_generation_profile(
            engine_variant="mega15",
            use_i2v=True,
            duration=18,
            resolution="1080p",
        )

        self.assertEqual(model_id, SEEDANCE_FAST_I2V_MODEL)
        self.assertEqual(duration, 15)
        self.assertEqual(resolution, "480p")


if __name__ == "__main__":
    unittest.main()