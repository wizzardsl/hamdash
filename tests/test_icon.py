"""hamdash's icon: drawn by a script, carried everywhere it should be.

`tools/make_icon.py` draws it - an antenna mast radiating amber (transmit)
and teal (receive) on the board's background - and writes every file. The
files are committed so a build needs nothing installed; this checks they are
what the script makes, so the drawing and the files cannot drift apart, and
that each place that shows the icon points at one that exists.

Checked by hand rather than here: on a desktop the window shows it. On a
bare Xvfb with no window manager GTK holds the icon (`get_icon()`) but never
publishes `_NET_WM_ICON`, so an xprop check there reads "not found" for any
GTK window at all - a plain five-line one included.

Run with:  python -m unittest discover -s tests
"""
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON = ROOT / "hamdash" / "static" / "icon"


def ico_entries(data: bytes) -> list:
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1), "not an icon file"
    out = []
    for i in range(count):
        w, h, _c, _r, _p, _bpp, size, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * i)
        out.append((w or 256, h or 256, data[offset:offset + size]))
    return out


class TestTheFiles(unittest.TestCase):
    def test_the_windows_icon_has_every_size_windows_asks_for(self):
        entries = ico_entries((ICON / "hamdash.ico").read_bytes())
        self.assertEqual([w for w, _h, _d in entries], [16, 24, 32, 48, 64, 128, 256])
        for w, h, data in entries:
            with self.subTest(size=w):
                self.assertEqual(w, h)
                self.assertTrue(data.startswith(b"\x89PNG"), "PNG entries")

    def test_the_pngs_are_the_sizes_they_say(self):
        for name, size in (("hamdash-512.png", 512), ("hamdash-192.png", 192),
                           ("favicon-64.png", 64)):
            data = (ICON / name).read_bytes()
            with self.subTest(name=name):
                self.assertTrue(data.startswith(b"\x89PNG"))
                self.assertEqual(struct.unpack(">II", data[16:24]), (size, size))

    def test_the_mac_icon_has_its_retina_sizes(self):
        data = (ICON / "hamdash.icns").read_bytes()
        self.assertEqual(data[:4], b"icns")
        self.assertEqual(struct.unpack(">I", data[4:8])[0], len(data))
        kinds, i = [], 8
        while i < len(data):
            kind, size = data[i:i + 4], struct.unpack(">I", data[i + 4:i + 8])[0]
            self.assertTrue(data[i + 8:i + 16].startswith(b"\x89PNG"), kind)
            kinds.append(kind)
            i += size
        for k in (b"icp4", b"ic07", b"ic08", b"ic09", b"ic10", b"ic13", b"ic14"):
            self.assertIn(k, kinds)

    def test_they_are_what_the_script_draws(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy draws the icon")
        sys.path.insert(0, str(ROOT / "tools"))
        import make_icon
        self.assertEqual((ICON / "favicon-64.png").read_bytes(),
                         make_icon.png(make_icon.render(64)),
                         "run python tools/make_icon.py and commit what it writes")
        made = make_icon.ico({s: make_icon.png(make_icon.render(s))
                              for s in make_icon.ICO_SIZES})
        self.assertEqual((ICON / "hamdash.ico").read_bytes(), made)
        drawn = {s: make_icon.png(make_icon.render(s))
                 for s in {s for _k, s in make_icon.ICNS_TYPES}}
        self.assertEqual((ICON / "hamdash.icns").read_bytes(),
                         make_icon.icns(drawn))


class TestWhereItIsUsed(unittest.TestCase):
    def test_the_windows_build_takes_it(self):
        spec = (ROOT / "hamdash.spec").read_text(encoding="utf-8")
        self.assertIn('icon="hamdash/static/icon/hamdash.ico" if sys.platform == "win32"',
                      spec)

    def test_macos_gets_an_app_with_it(self):
        """A bare binary in a folder opened a Terminal beside it and had no
        icon. The .app also has to say why it wants the microphone - the
        radio's sound card is one to macOS - or audio input is refused."""
        spec = (ROOT / "hamdash.spec").read_text(encoding="utf-8")
        tail = spec[spec.index('if sys.platform == "darwin":'):]
        self.assertIn("BUNDLE(", tail)
        self.assertIn('icon="hamdash/static/icon/hamdash.icns"', tail)
        self.assertIn('"NSMicrophoneUsageDescription"', tail)
        self.assertIn('"NSAllowsLocalNetworking": True', tail)
        self.assertIn('"NSLocalNetworkUsageDescription"', tail)

    def test_the_mac_zip_is_made_where_it_is_built(self):
        """upload-artifact drops symlinks and execute bits; an .app is full
        of both. ditto on the Mac, and the release copies that zip as is."""
        build = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
        self.assertIn("ditto -c -k --keepParent dist/hamdash.app", build)
        release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn('cp "$dir/${name}.zip" "assets/${name}-${TAG}.zip"', release)

    def test_the_installer_takes_it(self):
        iss = (ROOT / "installer" / "hamdash.iss").read_text(encoding="utf-8")
        line = next(l for l in iss.splitlines() if l.startswith("SetupIconFile="))
        rel = line.split("=", 1)[1].replace("\\", "/")
        self.assertTrue((ROOT / "installer" / rel).resolve().is_file(), rel)

    def test_the_pages_show_it(self):
        for page in ("index.html", "popout.html"):
            html = (ROOT / "hamdash" / "static" / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertIn('<link rel="icon" type="image/png" '
                              'href="/static/icon/favicon-64.png">', html)

    def test_the_window_takes_it(self):
        run_py = (ROOT / "run.py").read_text(encoding="utf-8")
        self.assertIn('webview.start(icon=str(BUNDLED_STATIC / "icon" / "hamdash-512.png"))',
                      run_py)


if __name__ == "__main__":
    unittest.main()
