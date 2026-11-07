"""Make the WSPR test fixtures, with WSJT-X as the referee.

    python tools/wspr_fixtures.py           # needs WSJT-X: wsprcode, wsprd

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


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
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
