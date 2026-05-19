import unittest

from app.services.seedance_video import _atlas_assets_not_ready


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


if __name__ == "__main__":
    unittest.main()