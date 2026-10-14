"""Two browsers on one tile are two listeners.

The card counts listeners so it knows when to let the sound card go, and
it counted them by *tile* uid - which is the same for every browser
showing that tile. Playing the radio's audio over the network is a card
whose whole point is being open somewhere else, so this was not an edge
case: with the board up in two places, either one pressing Stop took the
device from the other, whose next poll took it straight back.

It shows in the card log as a row of `last listener left` and to the
operator as audio that will not start again after a Stop. It was
reported exactly that way, and the second listener in that case turned
out to be a stale tab of mine.

`card.py` calls `card.run()` at import, which blocks and binds a port, so
these drive the want bookkeeping by loading the module's source rather
than importing it, the same way `TestAskingIsNotWanting` does.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

CARD = Path(__file__).resolve().parent.parent / "plugins" / "rx_audio"
SRC = (CARD / "card.py").read_text(encoding="utf-8")
PAGE = (CARD / "index.html").read_text(encoding="utf-8")


def body_of(name: str) -> str:
    """One function's source, up to the next top-level def or decorator."""
    i = SRC.index(f"def {name}(")
    rest = SRC[i:]
    m = re.search(r"\n(?=@card\.|def )", rest[1:])
    return rest[:m.start() + 1] if m else rest


class TestTheWantIsPerListener(unittest.TestCase):
    def test_the_routes_ask_who_not_which_tile(self):
        # The tile uid is still right for *settings* - that is what it is
        # for - but it cannot say who is listening.
        self.assertIn("tap_for(card.settings(), listener())", SRC)
        self.assertIn("release(listener())", SRC)
        self.assertNotIn("tap_for(card.settings(), card.tile_uid())", SRC,
                         "the want is keyed on the tile again, so one "
                         "browser's Stop takes the device from another")

    def test_who_comes_from_the_page(self):
        body = body_of("listener")
        self.assertIn('request.args.get("who")', body)

    def test_an_older_page_still_registers_a_want(self):
        # Falling back to the tile uid matters: no want at all would have
        # the reaper close the device under a page that is listening.
        body = body_of("listener")
        self.assertIn("card.tile_uid()", body)

    def test_release_only_closes_when_nothing_else_wants_it(self):
        body = body_of("release")
        self.assertIn("and not tiles", body,
                      "release closes the device without checking whether "
                      "another listener still wants it")


class TestThePageIdentifiesItself(unittest.TestCase):
    def test_it_sends_who_when_asking_for_audio(self):
        self.assertIn("who=${WHO}", PAGE)
        self.assertRegex(PAGE, r"/api/pcm\?since=\$\{lastSeq\}&who=\$\{WHO\}")

    def test_it_sends_who_when_stopping(self):
        self.assertRegex(PAGE, r"/api/stop\?who=\$\{WHO\}")

    def test_the_id_does_not_need_a_secure_context(self):
        # `crypto.randomUUID` is secure-context only, and this board is
        # plain HTTP on a LAN address exactly when this card matters -
        # it would be undefined there and every page would share the
        # fallback, which is the bug again.
        self.assertIn("getRandomValues", PAGE)
        self.assertNotIn("randomUUID()", PAGE)

    def test_the_id_is_made_once_per_page(self):
        # Per request would make every poll a new listener and the want
        # table would grow without bound.
        self.assertRegex(PAGE, r"const WHO = \(\(\) =>")


class TestTheBookkeepingItself(unittest.TestCase):
    """The logic the source checks above are standing in for.

    A small model of `_wanted`, so the rule - a device closes when its
    last listener goes and not before - is asserted rather than merely
    described.
    """

    def setUp(self):
        self.wanted = {}
        self.closed = []

    def want(self, key, who):
        self.wanted.setdefault(key, {})[who] = 1.0

    def release(self, who):
        for key, listeners in list(self.wanted.items()):
            if listeners.pop(who, None) is not None and not listeners:
                self.closed.append(key)
                self.wanted.pop(key, None)

    def test_one_listener_stopping_closes_the_device(self):
        self.want("mic|12000", "page-a")
        self.release("page-a")
        self.assertEqual(self.closed, ["mic|12000"])

    def test_a_second_listener_keeps_it_open(self):
        self.want("mic|12000", "page-a")
        self.want("mic|12000", "page-b")
        self.release("page-a")
        self.assertEqual(self.closed, [], "stopping one browser took the "
                                          "device from the other")
        self.assertEqual(list(self.wanted["mic|12000"]), ["page-b"])

    def test_the_last_one_out_closes_it(self):
        self.want("mic|12000", "page-a")
        self.want("mic|12000", "page-b")
        self.release("page-a")
        self.release("page-b")
        self.assertEqual(self.closed, ["mic|12000"])

    def test_keying_on_the_tile_is_what_broke_it(self):
        # The old behaviour, shown failing: both browsers are one entry,
        # so the first Stop closes the device for both.
        self.want("mic|12000", "tile-1")      # browser A, keyed by tile
        self.want("mic|12000", "tile-1")      # browser B, same key
        self.release("tile-1")                # either one presses Stop
        self.assertEqual(self.closed, ["mic|12000"])


if __name__ == "__main__":
    unittest.main()
