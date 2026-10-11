"""What a card that will not run says, and where it says it.

The tile is where somebody writing a card is looking when it does not work,
so the reason belongs there rather than only in a log they have to be told
about first.

The part worth a test is the escaping. A card's output is rendered by the
*host*, on the host's origin, and a traceback is full of angle brackets:
unescaped, `in <module>` is read as a tag and silently disappears - the one
word on the line that says where it broke - and anything a card prints with
a script tag in it would run.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest

from hamdash import app as happ


class FakeCard:
    id = "brokencard"
    path = "/cards/brokencard"
    error = "card.json is not valid JSON: Expecting ',' delimiter: line 4"


class FakeWorker:
    def __init__(self, status="exited (1)", log=None):
        self.card = FakeCard()
        self.status = status
        self.log = log or []


TRACEBACK = [
    "Traceback (most recent call last):",
    '  File "card.py", line 7, in <module>',
    '    BOOM = WIDGETS["b"]',
    "KeyError: 'b'",
]


class TestTheTracebackSurvives(unittest.TestCase):
    def test_the_word_module_is_not_eaten(self):
        """`<module>` unescaped is an unknown tag: the browser swallows it
        and the line reads "line 7, in" with nothing after it."""
        page = happ._worker_down(FakeWorker(log=TRACEBACK))
        self.assertIn("&lt;module&gt;", page)
        self.assertNotIn("in <module>", page)

    def test_the_whole_traceback_is_there(self):
        page = happ._worker_down(FakeWorker(log=TRACEBACK))
        for line in ("Traceback", "KeyError", "line 7"):
            with self.subTest(line=line):
                self.assertIn(line, page)

    def test_a_script_a_card_printed_cannot_run(self):
        """A card's output is rendered by the host, on the host's origin."""
        page = happ._worker_down(
            FakeWorker(log=["<script>alert(1)</script>"]))
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_a_card_id_is_escaped_too(self):
        w = FakeWorker(log=["boom"])
        w.card.id = "<b>evil</b>"
        self.assertNotIn("<b>evil", happ._worker_down(w))

    def test_a_manifest_error_is_escaped(self):
        page = happ._card_error(FakeCard())
        self.assertIn("Expecting", page)
        self.assertNotIn("<script", page)


class TestItSaysWhatToDo(unittest.TestCase):
    def test_it_names_the_log(self):
        page = happ._worker_down(FakeWorker(log=["boom"]), home="C:/x/hamdash")
        self.assertIn("hamdash.log", page)

    def test_it_comes_back_by_itself_while_the_host_is_still_trying(self):
        """A card being fixed should reappear without the operator poking
        the tile, so the page reloads while a restart is still coming."""
        page = happ._worker_down(FakeWorker(status="exited (1)",
                                            log=["boom"]))
        self.assertIn("http-equiv=refresh", page)

    def test_it_stops_reloading_once_the_host_has_given_up(self):
        """Nothing is going to change, so reloading for ever is just noise."""
        page = happ._worker_down(
            FakeWorker(status="failed (restart limit)", log=["boom"]))
        self.assertNotIn("http-equiv=refresh", page)
        self.assertIn("stopped trying", page)

    def test_a_card_with_no_output_still_explains_itself(self):
        page = happ._worker_down(FakeWorker(log=[]))
        self.assertIn("no output", page)
        self.assertIn("brokencard", page)

    def test_it_is_a_themed_page_not_a_bare_pre(self):
        """It is shown inside a tile beside themed cards."""
        page = happ._worker_down(FakeWorker(log=["boom"]))
        self.assertIn("/static/css/app.css", page)
        self.assertTrue(page.startswith("<!doctype html>"))


class TestStarting(unittest.TestCase):
    def test_it_retries_itself(self):
        """The proxy hands this back rather than holding a server thread."""
        page = happ._starting(FakeWorker(status="starting"))
        self.assertIn("http-equiv=refresh", page)
        self.assertIn("brokencard", page)

    def test_a_card_id_cannot_inject(self):
        w = FakeWorker(status="starting")
        w.card.id = '"><script>x</script>'
        self.assertNotIn("<script>x", happ._starting(w))


if __name__ == "__main__":
    unittest.main()
