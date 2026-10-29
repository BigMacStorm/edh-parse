import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from edhparse.web.app import app


class TestWebUI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_home(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("edh-parse", r.text)
        self.assertIn("Safe shopping", r.text)

    def test_workflow_pages(self):
        for workflow_id in (
            "shopping-list",
            "summary",
            "convert",
            "diff",
            "safe-shopping",
            "new-cards",
            "pivot",
            "artist-signings",
            "compare",
            "locate",
            "find-in-binders",
            "wipcoms",
            "challenge",
        ):
            r = self.client.get(f"/workflow/{workflow_id}")
            self.assertEqual(r.status_code, 200, workflow_id)
            self.assertIn("<form", r.text)

    def test_compare_posts_to_service(self):
        with patch("edhparse.services.compare.run_compare") as mock_run:
            mock_run.return_value = {
                "ok": True,
                "message": "Compared a vs b",
                "outputs": [],
            }
            r = self.client.post(
                "/run/compare",
                data={"url_a": "atraxa-praetors-voice", "url_b": "giada-font-of-hope"},
            )
            self.assertEqual(r.status_code, 200)
            self.assertIn("Compared a vs b", r.text)
            mock_run.assert_called_once()

    def test_async_job_returns_progress(self):
        with patch("edhparse.services.compare.run_compare") as mock_run:
            mock_run.return_value = {
                "ok": True,
                "message": "Compared a vs b",
                "outputs": [],
            }
            r = self.client.post(
                "/run/compare",
                data={"url_a": "a", "url_b": "b"},
                headers={"X-Async-Job": "1"},
            )
            self.assertEqual(r.status_code, 200)
            payload = r.json()
            self.assertIn("job_id", payload)
            # poll until done
            import time

            final = None
            for _ in range(50):
                status = self.client.get(f"/api/jobs/{payload['job_id']}")
                self.assertEqual(status.status_code, 200)
                final = status.json()
                if final["status"] in ("done", "error"):
                    break
                time.sleep(0.05)
            self.assertIsNotNone(final)
            self.assertEqual(final["status"], "done")
            self.assertIn("Compared a vs b", final.get("result_html") or "")


if __name__ == "__main__":
    unittest.main()
