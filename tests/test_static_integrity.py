import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


class IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.targets = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(attributes["id"])
        for target_name in ("data-date-target", "data-time-target"):
            if attributes.get(target_name):
                self.targets.append(attributes[target_name])


def read_static(name):
    return (STATIC / name).read_text(encoding="utf-8")


def asset_version(source):
    match = re.search(r'APP_ASSET_VERSION\s*=\s*"([^"]+)"', source)
    if not match:
        raise AssertionError("APP_ASSET_VERSION is missing.")
    return match.group(1)


class StaticIntegrityTests(unittest.TestCase):
    def test_pages_do_not_contain_duplicate_ids(self):
        for filename in ("index.html", "login.html"):
            with self.subTest(filename=filename):
                parser = IdCollector()
                parser.feed(read_static(filename))
                duplicates = sorted({item for item in parser.ids if parser.ids.count(item) > 1})
                self.assertEqual(duplicates, [])

    def test_picker_targets_reference_real_inputs(self):
        parser = IdCollector()
        parser.feed(read_static("index.html"))
        page_ids = set(parser.ids)

        self.assertTrue(parser.targets)
        self.assertEqual(sorted(set(parser.targets) - page_ids), [])

    def test_static_javascript_id_references_exist(self):
        parser = IdCollector()
        parser.feed(read_static("index.html"))
        page_ids = set(parser.ids)
        referenced_ids = set(re.findall(r'\$\("#([A-Za-z][A-Za-z0-9_-]*)"\)', read_static("app.js")))

        self.assertEqual(sorted(referenced_ids - page_ids), [])

    def test_asset_versions_stay_aligned(self):
        app_version = asset_version(read_static("app.js"))
        login_version = asset_version(read_static("login.js"))
        worker_version = asset_version(read_static("service-worker.js"))

        self.assertEqual(app_version, login_version)
        self.assertEqual(app_version, worker_version)
        self.assertIn(f"/static/app.js?v={app_version}", read_static("index.html"))
        self.assertIn(f"/static/styles.css?v={app_version}", read_static("index.html"))
        self.assertIn(f"/static/login.js?v={app_version}", read_static("login.html"))
        self.assertIn(f"/static/styles.css?v={app_version}", read_static("login.html"))
        self.assertIn(f"/config.js?v={app_version}", read_static("index.html"))
        self.assertIn(f"/config.js?v={app_version}", read_static("login.html"))


if __name__ == "__main__":
    unittest.main()
