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

PLUGINS = Path(__file__).resolve().parent.parent / "plugins"
CARD = PLUGINS / "rx_audio"
SRC = (CARD / "card.py").read_text(encoding="utf-8")
# The player moved out of the retired Listen card's own page into one
# shared script, mounted at the foot of the rig card.
LISTEN = (PLUGINS.parent / "hamdash" / "static" / "js" / "listen.js")
PAGE = LISTEN.read_text(encoding="utf-8")

#: Both cards hold a sound card and both let a tile stop listening, so
#: both have to tell pages apart. `spectrum` has no Stop route - only the
#: idle reaper - so nothing there can take the device from anybody.
HOLDS_AN_INPUT = ("rx_audio", "decoder")


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
        self.assertIn("tap_for(_conf(), who())", SRC)
        self.assertIn("release(who())", SRC)
        self.assertNotIn("tap_for(card.settings(), card.tile_uid())", SRC,
                         "the want is keyed on the tile again, so one "
                         "browser's Stop takes the device from another")

    def test_who_comes_from_the_page(self):
        body = body_of("who")
        self.assertIn('request.args.get("who")', body)

    def test_an_older_page_still_registers_a_want(self):
        # Falling back to the tile uid matters: no want at all would have
        # the reaper close the device under a page that is listening.
        body = body_of("who")
        self.assertIn("card.tile_uid()", body)

    def test_release_only_closes_when_nothing_else_wants_it(self):
        body = body_of("release")
        self.assertIn("and not tiles", body,
                      "release closes the device without checking whether "
                      "another listener still wants it")


class TestThePageIdentifiesItself(unittest.TestCase):
    def test_it_sends_who_when_asking_for_audio(self):
        # `params()` carries who, plus the device and rate the rig tile
        # chose - the engine cannot read another card's tile settings.
        self.assertIn("new URLSearchParams({ who: WHO })", PAGE)
        self.assertRegex(PAGE, r"/api/pcm\?since=\$\{lastSeq\}&\$\{params\(\)\}")

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



class TestBothAudioCardsTellPagesApart(unittest.TestCase):
    """The rule, applied to every card that can be asked to let go.

    `rx_audio` was fixed first and `decoder` carried the same keying for
    a while - `@card.post("/stop")` on `card.tile_uid()` - but it is
    reached by a different gesture, which is worth knowing before
    testing it. The decoder's `running` is a *tile setting*, so pressing
    Stop saves it and the bus stops every browser showing that tile:
    both listeners leave and closing the input is correct. What is per
    page is going hidden (invariant 5b), which tells nobody - the
    Decoder tab selected in one browser and Spots in another, where the
    hidden one's `/api/stop` took the input from the visible one.
    """

    def cards(self):
        for name in HOLDS_AN_INPUT:
            page = (PAGE if name == "rx_audio" else
                    (PLUGINS / name / "index.html").read_text(encoding="utf-8"))
            yield (name,
                   (PLUGINS / name / "card.py").read_text(encoding="utf-8"),
                   page)

    def test_no_card_keys_its_want_on_the_tile(self):
        for name, src, _page in self.cards():
            with self.subTest(card=name):
                self.assertNotRegex(
                    src, r"(listener_for|tap_for)\([^)]*card\.tile_uid\(\)",
                    "the want is keyed on the tile, so every browser "
                    "showing it is one listener")

    def test_every_stop_route_releases_a_page(self):
        for name, src, _page in self.cards():
            with self.subTest(card=name):
                i = src.index('@card.post("/stop")')
                body = src[i:i + 1200]
                self.assertIn("who()", body,
                              "Stop releases the tile, which stops every "
                              "browser showing it")

    def test_the_decoder_says_who_it_is_when_it_goes_hidden(self):
        """The gesture that is per page, so the one that must carry it.

        A hidden tile hands the input back without touching the saved
        `running`, so the browser that still has the tile on screen is
        still decoding. A stop that did not say which page it came from
        took the sound card out from under it.
        """
        page = (PLUGINS / "decoder" / "index.html").read_text(encoding="utf-8")
        i = page.index("HD.onVisible(")
        self.assertIn("/api/stop?who=${WHO}", page[i:i + 400],
                      "the hidden tile releases the whole tile, which is "
                      "every browser showing it")

    def test_every_page_identifies_itself(self):
        for name, _src, page in self.cards():
            with self.subTest(card=name):
                self.assertIn("getRandomValues", page)
                self.assertNotIn("randomUUID()", page,
                                 "secure-context only - on a plain-HTTP LAN "
                                 "board every page would share the fallback")
                self.assertRegex(page, r"who=\$\{WHO\}|who: WHO")


if __name__ == "__main__":
    unittest.main()
