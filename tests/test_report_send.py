"""Send a problem report, for an operator with no GitHub account.

Settings -> Help -> Send uploads the report zip to the project's report
service (tools/report-service, a Cloudflare Worker), which files it as an
issue in a private repository and answers with a reference. Pinned here:
what goes out (the zip itself, plus version, platform, radio and the
operator's note as URL-encoded headers - nothing else), that the service's
own words reach the operator when it refuses, that only one upload runs at
a time and never on a request thread, and that it is only ever done from
the machine hamdash runs on. Nothing here touches the network: a local
HTTP server stands in for the service.

Run with:  python -m unittest discover -s tests
"""
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hamdash.app import create_app                      # noqa: E402
from hamdash.config import Config                       # noqa: E402
from hamdash.services import diagnostics as d           # noqa: E402

INFO = {"version": "1.4.0", "platform": "Windows 11", "radio": "FT-900 via flrig"}


class FakeService(BaseHTTPRequestHandler):
    seen: list = []
    answer = (200, {"ok": True, "reference": "HD-7F3KQ"})

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        FakeService.seen.append({"path": self.path, "headers": dict(self.headers),
                                 "body": body})
        status, reply = FakeService.answer
        data = json.dumps(reply).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class TestSendReport(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="hamdash-send-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.zip = self.dir / "hamdash-report-20261105-010203.zip"
        with zipfile.ZipFile(self.zip, "w") as z:
            z.writestr("README.txt", "a report")
        FakeService.seen = []
        FakeService.answer = (200, {"ok": True, "reference": "HD-7F3KQ"})
        self.server = HTTPServer(("127.0.0.1", 0), FakeService)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/report"

    def test_the_zip_and_what_it_is_go_out_and_the_reference_comes_back(self):
        ref = d.send_report(self.zip, INFO, "Transmit button stuck\nafter a reload, née",
                            url=self.url)
        self.assertEqual(ref, "HD-7F3KQ")
        (got,) = FakeService.seen
        self.assertEqual(got["body"], self.zip.read_bytes())
        h = got["headers"]
        self.assertEqual(h["Content-Type"], "application/zip")
        self.assertEqual(h["X-Hamdash-Version"], "1.4.0")
        self.assertEqual(h["X-Hamdash-Radio"], "FT-900%20via%20flrig")
        from urllib.parse import unquote
        self.assertEqual(unquote(h["X-Hamdash-Note"]),
                         "Transmit button stuck\nafter a reload, née")

    def test_nothing_else_goes(self):
        d.send_report(self.zip, INFO, "", url=self.url)
        sent = {k for k in FakeService.seen[0]["headers"] if k.startswith("X-")}
        self.assertEqual(sent, {"X-Hamdash-Version", "X-Hamdash-Platform",
                                "X-Hamdash-Radio"}, "no note was written")

    def test_the_services_own_words_when_it_refuses(self):
        FakeService.answer = (429, {"ok": False,
                                    "error": "too many reports from here; try again in a minute"})
        with self.assertRaises(d.SendError) as e:
            d.send_report(self.zip, INFO, url=self.url)
        self.assertIn("too many reports", str(e.exception))

    def test_unreachable_says_so(self):
        with self.assertRaises(d.SendError) as e:
            d.send_report(self.zip, INFO, url="http://127.0.0.1:9/report")
        self.assertIn("could not reach", str(e.exception))

    def test_the_real_service_address(self):
        self.assertTrue(d.REPORT_SERVICE.startswith("https://"))
        self.assertTrue(d.REPORT_SERVICE.endswith("/report"))


class TestTheSender(unittest.TestCase):
    def test_one_at_a_time_off_the_request_thread(self):
        go = threading.Event()

        def slow(path, info, note):
            go.wait(5)
            return "HD-AAAAA"
        s = d.Sender(send=slow)
        self.assertTrue(s.start(Path("r.zip"), INFO))
        self.assertEqual(s.status()["state"], "sending")
        self.assertFalse(s.start(Path("r.zip"), INFO), "a second while sending")
        go.set()
        for _ in range(50):
            if s.status()["state"] != "sending":
                break
            time.sleep(0.05)
        self.assertEqual((s.status()["state"], s.status()["reference"]),
                         ("sent", "HD-AAAAA"))

    def test_a_failure_is_kept_for_the_page(self):
        def refuse(path, info, note):
            raise d.SendError("the report service is not set up yet")
        s = d.Sender(send=refuse)
        s.start(Path("r.zip"), INFO)
        for _ in range(50):
            if s.status()["state"] != "sending":
                break
            time.sleep(0.05)
        self.assertEqual(s.status()["state"], "failed")
        self.assertIn("not set up", s.status()["error"])


class TestTheEndpoint(unittest.TestCase):
    def setUp(self):
        home = Path(tempfile.mkdtemp(prefix="hamdash-send-host-"))
        self.addCleanup(shutil.rmtree, home, True)
        (home / "layout.json").write_text("[]", encoding="utf-8")
        self.home = home
        app, shutdown = create_app(Config(home), port=8765)
        self.addCleanup(shutdown)
        self.client = app.test_client()

    def post(self, addr, note=""):
        return self.client.post("/api/diag/send", json={"note": note},
                                environ_base={"REMOTE_ADDR": addr})

    def test_only_from_this_machine(self):
        r = self.post("192.0.2.44")
        self.assertEqual(r.status_code, 403)
        self.assertFalse((self.home / "reports").exists())

    def test_it_makes_the_report_and_sends_it(self):
        got = {}

        def fake(path, info, note):
            got.update(path=path, info=info, note=note)
            return "HD-BBBBB"
        with mock.patch.object(d, "send_report", fake):
            r = self.post("127.0.0.1", "it froze")
            self.assertEqual(r.status_code, 200)
            for _ in range(50):
                s = self.client.get("/api/diag/send",
                                    environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_json()
                if s["state"] != "sending":
                    break
                time.sleep(0.05)
        self.assertEqual((s["state"], s["reference"]), ("sent", "HD-BBBBB"))
        self.assertTrue(Path(got["path"]).is_file(), "the zip is kept on disk too")
        self.assertEqual(got["note"], "it froze")
        self.assertEqual(set(got["info"]), {"version", "platform", "radio"})


class TestThePage(unittest.TestCase):
    HTML = (ROOT / "hamdash/static/index.html").read_text(encoding="utf-8")
    JS = (ROOT / "hamdash/static/js/app.js").read_text(encoding="utf-8")

    def test_it_says_what_goes_before_anything_goes(self):
        box = self.HTML[self.HTML.index('id="s-report-confirm"'):]
        box = box[:box.index("</div>\n    </div>")]
        for words in ("callsign and grid", "Passwords and API keys",
                      "deleted after 90 days"):
            self.assertIn(words, box)
        send = self.JS[self.JS.index("$('#s-report-send').onclick"):]
        send = send[:send.index("};")]
        self.assertNotIn("/api/diag/send", send, "the first press only asks")


if __name__ == "__main__":
    unittest.main()
