import unittest

from scripts.audit_public_release import ROOT, _is_forbidden_path


class PublicReleaseAuditTests(unittest.TestCase):
    def test_allows_images_only_in_approved_public_roots(self) -> None:
        self.assertFalse(
            _is_forbidden_path(ROOT / "docs" / "Images" / "Dashboard.png")
        )
        self.assertFalse(
            _is_forbidden_path(
                ROOT / "assets" / "branding" / "application-icon.webp"
            )
        )

    def test_rejects_images_outside_approved_public_roots(self) -> None:
        forbidden = [
            ROOT / "docs" / "Dashboard.png",
            ROOT / "docs" / "Private" / "Dashboard.png",
            ROOT / "assets" / "application-icon.png",
            ROOT / "screenshots" / "Dashboard.png",
            ROOT / "docs" / "images" / "case-mismatch.png",
        ]

        for path in forbidden:
            with self.subTest(path=path):
                self.assertTrue(_is_forbidden_path(path))


if __name__ == "__main__":
    unittest.main()
