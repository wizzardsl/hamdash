"""Make the WSPR test fixtures, with WSJT-X as the referee.

    python tools/wspr_fixtures.py           # needs WSJT-X: wsprcode, wsprd
    python tools/wspr_fixtures.py --air SLOT.wav NAME [NOTE]

Writes `tests/fixtures/wspr/codes.json` - what `wsprcode` makes of a set
of messages of all three types: the 50-bit source encoding and the 162
channel symbols - and `tests/fixtures/wspr/sim_*.json`: a slot of audio
described by its inputs (messages, frequencies, DTs, SNRs, drifts and a
seed), and what WSJT-X's `wsprd` decoded from it. CI has no WSJT-X, so
both are committed; this is how they were made and how to make more.

No audio is committed. A two-minute slot is 2.9 MB, and it is not needed:
`wspr.slot_audio` rebuilds the same samples from the same inputs, because
NumPy's default generator promises the same stream for the same seed
across versions. So the test decodes exactly the audio wsprd did and is
held to wsprd's answers - which is what makes the SNR, DT and frequency
conventions WSJT-X's rather than this tree's own guess.

`--air` keeps a real two-minute slot off the air (a 12 kHz WAV starting
on the even minute) as `NAME.npz`, its 375 Hz complex baseband - 360 kB,
a sixteenth of the audio and everything the decoder reads - beside
`NAME.json` with what wsprd decoded from the full audio, and what jt9
made of it as FST4W, because a WSPR window on the air holds both.
Simulated audio cannot be smeared by the ionosphere; these can, and they
found the two things simulation missed (see CLAUDE.md, WSPR).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "wspr"
sys.path.insert(0, str(ROOT / "plugins" / "decoder"))

import wspr                                             # noqa: E402

MESSAGES = [
    "K1ABC FN42 37", "PJ4/K1ABC 33", "<PJ4/K1ABC> FK52UD 33", "K1ABC/P 33",
    "K1ABC/7 33", "G/K1ABC 37", "AB/K1ABC 37", "K1ABC/12 37", "K1ABC/A 0",
    "G4ABC IO91 60", "<K1ABC> FN42MH 37", "VA7SLL DO00 23",
    "VE7PPE CN89 30", "W1AW FN31 0", "JA1XYZ PM95 10", "ZL2AB RF70 53",
    "5P0T JO65 27", "A61ZZ LL75 40", "DL1ABC JO62 13", "KH6/K1ABC 20",
    "K1ABC/99 47",
]

# (name, seed, [(text, f0, dt, snr, drift)]): a spread of strengths, two
# stations 4 Hz apart (which only subtraction separates), drift both ways.
SLOTS = [
    ("sim_band", 4242, [
        ("K1ABC FN42 37", 1412.0, 0.0, -14, 0.0),
        ("VE7PPE CN89 30", 1446.3, 0.6, -20, 1.0),
        ("JA1XYZ PM95 10", 1481.7, -0.7, -24, -1.5),
        ("DL1ABC JO62 13", 1505.0, 1.2, -26, 0.5),
        ("ZL2AB RF70 53", 1509.0, 0.2, -18, 0.0),
        ("W1AW FN31 0", 1550.4, 0.3, -27, 0.0),
        ("G4ABC IO91 23", 1583.9, -0.3, -12, -0.5),
    ]),
]


def codes() -> dict:
    out = {}
    for m in MESSAGES:
        text = subprocess.run(["wsprcode", m], capture_output=True,
                              text=True, check=True).stdout
        hexs = re.search(r"Hex:\s+(.*)", text).group(1).replace(" ", "")
        chan = re.search(r"Channel symbols:\n(.*?)\n\n", text, re.S)
        out[m] = {"value": f"{int(hexs, 16) >> 6:013x}",
                  "symbols": "".join(chan.group(1).split())}
    return out


def wsprd(audio: np.ndarray) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        fn = Path(tmp) / "260106_1200.wav"
        with wave.open(str(fn), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(wspr.RATE)
            w.writeframes((audio * 32767 * 1.6).clip(-32767, 32767)
                          .astype("<i2").tobytes())
        text = subprocess.run(["wsprd", "-H", "-a", tmp, str(fn)],
                              capture_output=True, text=True, cwd=tmp,
                              check=True).stdout
    got = []
    for line in text.splitlines():
        m = re.match(r"\s*\d{4}\s+(-?\d+)\s+(-?[\d.]+)\s+([\d.]+)\s+(-?\d+)\s+"
                     r"(.+?)\s*$", line)
        if m:
            got.append({"snr": int(m.group(1)), "dt": float(m.group(2)),
                        "freq": round(float(m.group(3)) * 1e6, 1),
                        "drift": int(m.group(4)), "text": m.group(5)})
    return got


def fst4w(audio: np.ndarray) -> list:
    """jt9's FST4W-120 decodes of the slot: what else is in the window."""
    with tempfile.TemporaryDirectory() as tmp:
        fn = Path(tmp) / "260106_1200.wav"
        with wave.open(str(fn), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(wspr.RATE)
            w.writeframes((audio / max(np.abs(audio).max(), 1e-9) * 0.5 * 32767)
                          .astype("<i2").tobytes())
        text = subprocess.run(["jt9", "-W", "-p", "120", "-L", "1300", "-H",
                               "1700", "-f", "1500", "-F", "200", "-a", tmp,
                               "-t", tmp, str(fn)], capture_output=True,
                              text=True, cwd=tmp).stdout
    return [" ".join(l.split()) for l in text.splitlines()
            if l.strip() and not l.startswith("<")]


def air(path: str, name: str, note: str = "") -> None:
    with wave.open(path, "rb") as w:
        assert w.getframerate() == wspr.RATE and w.getsampwidth() == 2
        audio = (np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
                 .astype(np.float32) / 32768)
    bb = wspr._baseband(audio.astype(float), wspr.RATE).astype(np.complex64)
    np.savez_compressed(OUT / f"{name}.npz", bb=bb)
    doc = {"note": note,
           "wsprd": wsprd(audio / max(np.abs(audio).max(), 1e-9) * 0.5),
           "fst4w_jt9": fst4w(audio),
           "made_with": "WSJT-X wsprd and jt9 3.0.2"}
    (OUT / f"{name}.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(name, doc)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) >= 4 and sys.argv[1] == "--air":
        air(sys.argv[2], sys.argv[3], " ".join(sys.argv[4:]))
        return 0
    (OUT / "codes.json").write_text(json.dumps(codes(), indent=1) + "\n")
    for name, seed, msgs in SLOTS:
        audio = wspr.slot_audio(msgs, seed=seed)
        doc = {"seed": seed, "sent": [list(m) for m in msgs],
               "wsprd": wsprd(audio),
               "made_with": "WSJT-X wsprd 3.0.2, -H, single run"}
        (OUT / f"{name}.json").write_text(json.dumps(doc, indent=1) + "\n")
        print(name, len(doc["wsprd"]), "decoded by wsprd of", len(msgs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
