"""A Test button tests what is on screen, not what was saved last.

The bug this guards: the logbook sync buttons - Test connection, Pull and
Push for LoTW, QRZ and World Radio League - start a job on the host, and
the host reads stored config. So a key pasted into Settings and tested
straight away was tested as "no key", and the only way to make Test work
was Save, reopen Settings, then Test. Reported exactly that way. The
callsign lookup's Test had always saved first; these did not.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

APP_JS = (Path(__file__).resolve().parent.parent
          / "hamdash" / "static" / "js" / "app.js").read_text(encoding="utf-8")


def body_of(name: str) -> str:
    """The text of `function name(...) { ... }`, by brace counting."""
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", APP_JS)
    assert m, f"{name} not found in app.js"
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(APP_JS[i], 0)
        i += 1
    return APP_JS[m.end():i]


def handler_for(selector: str) -> str:
    """The text of `$('<selector>').onclick = async (...) => { ... }`."""
    m = re.search(r"\$\('" + re.escape(selector)
                  + r"'\)\.onclick\s*=\s*async\s*\([^)]*\)\s*=>\s*\{", APP_JS)
    assert m, f"no onclick for {selector}"
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(APP_JS[i], 0)
        i += 1
    return APP_JS[m.end():i]


class TestSyncButtonsSaveFirst(unittest.TestCase):
    def test_the_sync_buttons_save_before_they_ask_the_host(self):
        body = body_of("wireSync")
        save = body.find("saveSettings(")
        ask = body.find("/api/log/sync/")
        self.assertNotEqual(save, -1,
                            "the sync buttons must save what is on screen first")
        self.assertLess(save, ask, "saved after the job started is too late")

    def test_the_save_is_awaited(self):
        """An unawaited save races the job, which then reads the old key
        about half the time - worse than never, because it looks fixed."""
        self.assertRegex(body_of("wireSync"), r"await\s+saveSettings\(")

    def test_the_save_is_quiet(self):
        """A plain save closes the sheet, which would take the Test button
        and its result away from the operator mid-press."""
        self.assertRegex(body_of("wireSync"),
                         r"saveSettings\(\s*\{\s*quiet:\s*true\s*\}\s*\)")

    def test_every_service_and_direction_goes_through_it(self):
        self.assertRegex(APP_JS, r"\['lotw',\s*'qrz',\s*'wrl'\]\.forEach")
        self.assertRegex(APP_JS, r"\['test',\s*'pull',\s*'push'\]\.forEach"
                                 r"\(\(d\)\s*=>\s*wireSync\(s,\s*d\)\)")

    def test_the_hints_no_longer_say_save_first(self):
        """"Paste the key, then save" described the bug as the procedure."""
        for hint in re.findall(r"syncNote\([^;]*?'([^']*then[^']*)'", APP_JS):
            self.assertNotIn("then save", hint.lower(), hint)


class TestTheLookupTestStillDoes(unittest.TestCase):
    """The one that had it right, so it stays right."""

    def test_lookup_test_saves_before_it_asks(self):
        body = handler_for("#s-lk-test")
        self.assertLess(body.find("saveSettings("), body.find("/api/lookup/test"))
        self.assertNotEqual(body.find("saveSettings("), -1)


if __name__ == "__main__":
    unittest.main()
