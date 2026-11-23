import unittest
from unittest.mock import patch

from scripts.audit_public_release import ROOT, _is_forbidden_path, _approved_legacy_image, LEGACY_PUBLIC_IMAGE_BLOBS


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

    def test_legacy_image_history_requires_exact_reviewed_blobs(self) -> None:
        name = "Dashboard-Overview.png"
        path = "Docs/Images/" + name
        blob = LEGACY_PUBLIC_IMAGE_BLOBS[name]
        for historical, expected in ((blob, True), ("changed-blob", False)):
            with self.subTest(blob=historical), patch(
                "scripts.audit_public_release._git",
                side_effect=["commit\n", f"100644 blob {historical}\t{path}\n"],
            ):
                self.assertEqual(_approved_legacy_image(path), expected)
        self.assertFalse(_approved_legacy_image("Docs/Images/Private.png"))
        self.assertFalse(_approved_legacy_image("Docs/Images/nested/" + name))
        self.assertTrue(_is_forbidden_path(ROOT / path))

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
