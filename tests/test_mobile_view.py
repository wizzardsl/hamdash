"""The phone shell: the rules that are not about pixels.

`tests/js/phone.test.js` drives the ordering - what lands in the bar - and
needs Node. These are the structural ones, and they run everywhere, because
the thing that would actually hurt here is not a misplaced button:

  * two shells live at once, which is every card mounted twice - two
    iframes, two listeners, two openers of one sound card. That is the bug
    `tests/test_rx_listeners.py` exists for, reached from the other end.
  * a card torn out of the document without being told it is hidden, which
    leaves it holding the radio's audio until its own reaper notices.
  * the view choice written somewhere that travels, so the board opened at
    the desk comes up in the shell the phone picked.

Run with:  python -m unittest discover -s tests
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "hamdash" / "static" / "js"

APP = (JS / "app.js").read_text(encoding="utf-8")
MOBILE = (JS / "mobile.js").read_text(encoding="utf-8")
GRID = (JS / "grid.js").read_text(encoding="utf-8")
FRAMES = (JS / "frames.js").read_text(encoding="utf-8")
INDEX = (ROOT / "hamdash" / "static" / "index.html").read_text(encoding="utf-8")


class TestOnlyOneShellIsEverLive(unittest.TestCase):
    def test_building_one_destroys_the_other(self):
        i = APP.index("function buildShell(")
        body = APP[i:i + 900]
        self.assertIn("shell.destroy()", body,
                      "the outgoing shell is left mounted, so every card "
                      "has two iframes and two listeners")
        self.assertLess(body.index("shell.destroy()"), body.index("makePhone"),
                        "the new shell is built before the old one is torn "
                        "down, so both exist at once")

    def test_both_shells_can_be_torn_down(self):
        for name, src in (("Board", GRID), ("Phone", MOBILE)):
            with self.subTest(shell=name):
                self.assertRegex(src, r"\n\s*destroy\(\)\s*\{",
                                 f"{name} cannot be taken down, so switching "
                                 f"views leaks its cards")

    def test_tearing_down_tells_every_card_it_is_hidden(self):
        """A card that is removed without being told goes on holding
        whatever it holds until its own idle reaper notices - thirty
        seconds in which the operator has switched views and the radio's
        audio is still locked against everything else."""
        for name, src in (("Board", GRID), ("Phone", MOBILE)):
            with self.subTest(shell=name):
                i = src.index("destroy()")
                body = src[i:i + 600]
                self.assertIn("tellVisible(", body)
                self.assertIn("false", body)

    def test_only_one_of_them_is_on_screen(self):
        self.assertIn("gridEl.hidden = kind !== 'board'", APP)
        self.assertIn("phoneEl.hidden = kind !== 'phone'", APP)


class TestOneAnswerToWhetherAnybodyIsLooking(unittest.TestCase):
    """Invariant 5b has two callers now, so it gets one implementation.

    The recurring lesson in this tree is that two copies of an answer
    become two different answers, and this one is only ever noticed as a
    sound card locked against another program.
    """

    def test_one_sender_of_the_visibility_message(self):
        senders = [p.name for p in sorted(JS.glob("*.js"))
                   if "__hamdash: 'visible'" in p.read_text(encoding="utf-8")]
        self.assertEqual(senders, ["frames.js"],
                         "more than one file tells a card page whether it "
                         "is visible")

    def test_both_shells_call_it(self):
        for name, src in (("grid.js", GRID), ("mobile.js", MOBILE)):
            with self.subTest(file=name):
                self.assertIn("HDFrames.tellVisible(", src)

    def test_the_phone_tells_the_ones_it_is_not_showing(self):
        i = MOBILE.index("    show(cardUid) {")
        body = MOBILE[i:i + 800]
        self.assertIn("tellVisible(holder, on)", body,
                      "only the card on screen is told, so the one that "
                      "just left goes on polling")

    def test_frames_js_is_loaded_before_the_shells(self):
        order = [INDEX.index(f"js/{f}") for f in
                 ("frames.js", "grid.js", "mobile.js", "app.js")]
        self.assertEqual(order, sorted(order))


class TestMountingIsLazy(unittest.TestCase):
    """A phone must not start six card processes to show one card."""

    def test_a_card_is_built_when_it_is_first_chosen(self):
        i = MOBILE.index("    _mount(entry) {")
        body = MOBILE[i:i + 400]
        self.assertIn("if (this._holders.has(entry.uid)) return;", body,
                      "a card is rebuilt every time it is shown, which "
                      "reloads its page and loses what was on it")

    def test_nothing_mounts_everything_up_front(self):
        i = MOBILE.index("    setTiles(tiles) {")
        body = MOBILE[i:i + 900]
        self.assertNotIn("_mount(", body,
                         "setTiles mounts every card, so opening the board "
                         "on a phone starts all of them at once")


class TestTheChoiceIsAboutThisScreen(unittest.TestCase):
    """Which shell is a fact about this person at this screen, like
    `hd.learned.*` - the same board is opened from a desk and from a
    pocket, so a choice that travelled would make one of them wrong."""

    def test_it_is_remembered_in_local_storage(self):
        self.assertIn("localStorage.getItem(VIEW_KEY)", APP)
        self.assertIn("localStorage.setItem(VIEW_KEY", APP)

    def test_it_never_reaches_the_layout_or_the_config(self):
        for call in re.findall(r"HD\.put\('/api/(layout|config)'[^\n]*", APP):
            self.assertNotIn("view", call.lower().replace("preview", ""))
        self.assertNotIn("'hd.view'", (ROOT / "hamdash" / "config.py")
                         .read_text(encoding="utf-8"))

    def test_a_private_window_does_not_break_it(self):
        """`localStorage` throws outright in some privacy modes, and this
        runs on boot - unguarded it would take the whole board down."""
        for fn in ("savedView", "rememberView"):
            i = APP.index(f"const {fn} =")
            self.assertIn("catch", APP[i:i + 240], f"{fn} is unguarded")


class TestDetection(unittest.TestCase):
    def test_it_measures_the_viewport_not_the_user_agent(self):
        """Sniffing the UA string gets this wrong in both directions for
        ever. A phone is a narrow viewport, and so is a narrow window."""
        self.assertNotIn("userAgent", MOBILE)
        self.assertNotIn("navigator.platform", MOBILE)
        self.assertIn("matchMedia", MOBILE)

    def test_an_unmeasured_window_is_not_a_phone(self):
        i = MOBILE.index("  function wants() {")
        self.assertIn("if (!global.innerWidth) return false;",
                      MOBILE[i:i + 500],
                      "a window that has not been laid out reports 0 and "
                      "matches every max-width query there is")

    def test_a_chosen_view_is_not_overruled_by_a_resize(self):
        i = APP.index("HDPhone.onChange(")
        self.assertIn("if (savedView()) return;", APP[i:i + 300],
                      "turning the phone would throw away the operator's "
                      "choice and the card they were looking at")


class TestWhatTheOperatorAskedFor(unittest.TestCase):
    """Logging and seeing spots are what a phone is for; the radio and the
    audio are what you do sitting in front of it. Written down because it
    is a decision, not an accident of ordering."""

    def test_logging_and_spots_lead(self):
        i = MOBILE.index("const PRIORITY = [")
        order = re.findall(r"'([a-z_]+)'", MOBILE[i:MOBILE.index("]", i)])
        self.assertEqual(order[:2], ["logbook", "spots"])
        self.assertIn("rig_control", order)
        self.assertIn("rx_audio", order)

    def test_nothing_on_the_board_is_unreachable(self):
        """Every card gets a slot or a place behind More - including the
        ones stacked behind a tab, which have no tab strip here."""
        i = MOBILE.index("  function split(")
        body = MOBILE[i:i + 300]
        self.assertIn("rest: entries.slice(BAR)", body)

    def test_the_way_back_to_the_board_exists_in_both_shells(self):
        self.assertIn('id="view-toggle"', INDEX)       # on the board
        self.assertIn('id="phone-full"', INDEX)        # in More


class TestThePhoneDoesNotOfferWhatItCannotDo(unittest.TestCase):
    def test_no_pop_out_on_a_phone(self):
        """A second OS window means nothing on a phone, and `detect()` in
        a mobile browser answers `none` - which would greet every phone
        with a toast about installing pywebview."""
        self.assertNotIn("HDPopout", MOBILE)
        i = APP.index("HDPopout.detect()")
        self.assertIn("shellKind === 'board'", APP[max(0, i - 400):i + 40],
                      "pop-out detection runs on the phone shell too")


if __name__ == "__main__":
    unittest.main()
