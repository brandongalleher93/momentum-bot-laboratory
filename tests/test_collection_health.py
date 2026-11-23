import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.collection_health import collection_health
from bot.config import Settings
from bot.shadow_paper import shadow_paper_settings


class CollectionHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = shadow_paper_settings(Settings())
        self.now = datetime(2026, 11, 23, 8, tzinfo=ZoneInfo("America/New_York"))

    def assess(self, status, age=0, now=None):
        now = now or self.now
        (self.root / "health.json").write_text(json.dumps({"updated_at": (now - timedelta(seconds=age)).isoformat(), "status": status}))
        return collection_health(self.root, self.settings, now)["status"]

    def test_missing_and_stale_heartbeat_are_not_healthy(self):
        self.assertEqual(collection_health(self.root, self.settings, self.now)["status"], "needs_attention")
        self.assertEqual(self.assess("collecting", age=121), "needs_attention")
        self.assertEqual(self.assess("collecting"), "collecting")

    def test_errors_remain_visible_outside_the_window(self):
        after = self.now.replace(hour=13)
        self.assertEqual(self.assess("verification_failed", now=after), "needs_attention")
        self.assertEqual(self.assess("degraded", now=after), "needs_attention")
        self.assertEqual(self.assess("collecting", now=after), "needs_attention")
        self.assertEqual(self.assess("verified", now=after), "verified_segment")

    def test_old_verified_segment_cannot_mask_missing_current_session(self):
        self.assertEqual(self.assess("verified", age=86400), "needs_attention")

    def test_bad_or_future_status_fails_closed(self):
        self.assertEqual(self.assess("unknown"), "needs_attention")
        self.assertEqual(self.assess("collecting", age=-300), "needs_attention")

    def test_prior_incomplete_segment_cannot_be_hidden_by_new_heartbeat(self):
        (self.root / "20261123-old.sqlite3").write_bytes(b"unfinished")
        self.assertEqual(self.assess("collecting"), "needs_attention")

    def test_non_object_health_record_is_rejected(self):
        (self.root / "health.json").write_text("[]")
        self.assertEqual(collection_health(self.root, self.settings, self.now)["status"], "needs_attention")
