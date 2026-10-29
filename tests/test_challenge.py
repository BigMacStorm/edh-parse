import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from edhparse.services import challenge as ch


class ChallengeServiceTests(unittest.TestCase):
    def test_thirty_two_groups(self):
        groups = ch.identity_groups()
        self.assertEqual(len(groups), 32)
        self.assertEqual(len({g["key"] for g in groups}), 32)
        self.assertEqual({g["row"] for g in groups}, {0, 1, 2, 3})

    def test_canonical_identity(self):
        self.assertEqual(ch.canonical_identity(["G", "W"]), "WG")
        self.assertEqual(ch.canonical_identity([]), "")

    def test_top_themes_sorted_and_limited(self):
        page = {"panels": {"taglinks": [{"count": c, "slug": f"t{c}", "value": f"T{c}"} for c in (3, 9, 1, 7, 5, 8, 2)]}}
        themes = ch.top_themes(page)
        self.assertEqual([t["label"] for t in themes], ["T9", "T8", "T7", "T5", "T3"])
        self.assertEqual(ch.top_themes({}), [])

    def test_faces(self):
        dfc = {"name": "A // B", "card_faces": [
            {"name": "A", "image_uris": {"art_crop": "x"}, "artist": "Z"},
            {"name": "B", "image_uris": {"art_crop": "y"}}]}
        self.assertEqual([f["art"] for f in ch._faces_of(dfc)], ["x", "y"])
        single = {"name": "S", "image_uris": {"art_crop": "s"}, "artist": "Q"}
        self.assertEqual(len(ch._faces_of(single)), 1)

    def test_pair_header_splits(self):
        cards = {"A": {"name": "A"}, "B": {"name": "B"}}
        with mock.patch.object(ch.scryfall_bulk, "get_card_by_name_or_face", side_effect=lambda n: cards.get(n)):
            self.assertEqual([c["name"] for c in ch.cards_for_header("A // B")], ["A", "B"])
            self.assertEqual(ch.cards_for_header("A // missing"), [])

    def test_image_host_allowlist(self):
        self.assertTrue(ch.is_allowed_image_url("https://cards.scryfall.io/art_crop/x.jpg"))
        self.assertFalse(ch.is_allowed_image_url("http://cards.scryfall.io/x.jpg"))
        self.assertFalse(ch.is_allowed_image_url("https://evil.example.com/x.jpg"))
        self.assertFalse(ch.is_allowed_image_url("https://scryfall.io.evil.com/x.jpg"))

    def test_safe_name_and_oracle_validation(self):
        self.assertEqual(ch.safe_name("../../etc/passwd"), "etc-passwd")
        self.assertEqual(ch.safe_name(""), "challenge")
        with self.assertRaises(ValueError):
            ch.fetch_prints("not-a-uuid")

    def test_slugs_from_text(self):
        text = "https://edhrec.com/commanders/atraxa-praetors-voice\nhttps://edhrec.com/commanders/atraxa-praetors-voice\n"
        self.assertEqual(ch.slugs_from_text(text), ["atraxa-praetors-voice"])


class ChallengeRouteTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        import edhparse.web.app as webapp

        self.webapp = webapp
        self.client = TestClient(webapp.app)
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(webapp, "CHALLENGE_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_img_rejects_foreign_hosts(self):
        r = self.client.get("/api/img", params={"u": "https://evil.example.com/a.jpg"})
        self.assertGreaterEqual(r.status_code, 400)

    def test_save_open_roundtrip(self):
        state = {"title": "T", "entries": []}
        r = self.client.post("/api/challenge/save", json={"name": "../x y", "state": state})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Path(self.tmp.name), Path(self.tmp.name))
        self.assertEqual(self.client.get("/api/challenge/saves").json()["saves"], ["x-y"])
        self.assertEqual(self.client.get("/api/challenge/open", params={"name": "x-y"}).json()["state"], state)

    def test_export_requires_png(self):
        r = self.client.post("/api/challenge/export?name=a", content=b"not a png")
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/challenge/export?name=a", content=b"\x89PNG\r\n\x1a\nrest")
        self.assertEqual(r.status_code, 200)
        self.assertTrue((Path(self.tmp.name) / "a.png").is_file())


if __name__ == "__main__":
    unittest.main()


class SlugCleanTests(unittest.TestCase):
    def test_stray_quote_removed(self):
        self.assertEqual(ch._clean_slugs(["omnath-locus-of-the-void'", "omnath-locus-of-the-void"]), ["omnath-locus-of-the-void"])
