"""Make the FT8/FT4 test fixtures, with WSJT-X as the referee.

    python tools/ftx_fixtures.py            # needs WSJT-X: ft8sim, jt9
    python tools/ftx_fixtures.py --expect   # re-run jt9 on what is there
    python tools/ftx_fixtures.py --codes    # just ft8code/ft4code

Writes `tests/fixtures/ftx/*.wav` and, beside each, `*.json` holding what
went in and what WSJT-X's own `jt9` decoded from the file - which is the
answer `tests/test_ftx.py` holds the decoder to. CI has no WSJT-X, so the
files are committed; this is how they were made and how to make more.

* **FT8 signals are WSJT-X's own waveforms.** Each is made by `ft8sim`
  with no noise (its SNR argument 99), and they are summed with white
  noise here so that several share one noise floor - `ft8sim` makes one
  signal per file, each with its own noise.
* **FT4 signals are this tree's, and jt9 is the check on them.**
  `ft4sim_mult` in WSJT-X 3.0.2 reads its message list and writes nothing
  (no error, exit 0), so the FT4 file is synthesised by `ftx.synth` from
  symbols that `tests/test_ftx.py` checks against `ft4code`. jt9 decoding
  them at the SNRs they were made at is what says the waveform is right.
* Recordings off the air are added by hand (`tools/ftx_fixtures.py
  --expect` writes their JSON from jt9).
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "ftx"
sys.path.insert(0, str(ROOT / "plugins" / "decoder"))

import ftx                                              # noqa: E402

RATE = 12000
NOISE_RMS = 3000.0          # of 32767, so a stack of strong signals fits

# (message, audio Hz, DT s, SNR dB). Spread across the passband, the
# report types all present, and two pairs that overlap: 1480/1495 sit
# inside each other's 50 Hz, one 9 dB under the other, and 2400/2412 the
# same at a different DT.
FT8_MULTI = [
    ("CQ VA7HDX CN89", 520, 0.0, -10),
    ("VE7XYZ VA7HDX -12", 810, 0.4, -16),
    ("K1ABC W9XYZ R EN37", 1050, -0.3, -18),
    ("W9XYZ K1ABC RR73", 1300, 1.2, -20),
    ("CQ DX JA1XYZ PM95", 1480, 0.1, -5),
    ("JA1XYZ G4ABC IO91", 1495, 0.6, -14),
    ("PA9XYZ DL1ABC JO62", 1780, 0.2, -21),
    ("CQ POTA N0XRG EL29", 2100, -0.8, -12),
    ("TNX BOB 73 GL", 2400, 0.3, -8),
    ("EA3XYZ F4ABC 73", 2412, 0.9, -15),
    ("K1ABC/R W9XYZ EN37", 2700, 0.5, -17),
    ("G4ABC/P PA9XYZ JO22", 2950, -0.2, -13),
]

FT4_MULTI = [
    ("CQ VA7HDX CN89", 600, 0.0, -8),
    ("VE7XYZ VA7HDX -12", 900, 0.2, -12),
    ("K1ABC W9XYZ R-07", 1250, -0.2, -14),
    ("W9XYZ K1ABC RR73", 1600, 0.4, -10),
    ("CQ TEST JA1XYZ PM95", 1900, 0.1, -4),
    ("JA1XYZ G4ABC IO91", 1930, 0.3, -11),
    ("PA9XYZ DL1ABC +05", 2300, -0.1, -15),
    ("EA3XYZ F4ABC 73", 2700, 0.0, -13),
]


def amplitude(snr_db: float) -> float:
    """Amplitude of a sine at this SNR in 2500 Hz over unit noise."""
    return math.sqrt(2 * 10 ** (snr_db / 10) * 2500 / (RATE / 2))


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()),
                             dtype="<i2").astype(np.float64)


def write_wav(path: Path, x: np.ndarray) -> None:
    pcm = np.clip(np.round(x), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def ft8sim(text: str, f0: float, dt: float, tmp: Path) -> np.ndarray:
    for old in tmp.glob("*.wav"):
        old.unlink()
    subprocess.run(["ft8sim", text, str(f0), str(dt), "0", "0", "1", "99"],
                   cwd=tmp, check=True, capture_output=True)
    sig = read_wav(next(tmp.glob("*.wav")))
    return sig / 32767.0                            # a unit sine


def jt9(path: Path, mode: str) -> list:
    flag, period = ("-8", "15") if mode == "ft8" else ("-5", "7")
    with tempfile.TemporaryDirectory() as tmp:
        # jt9's own defaults for "my call" and "his call" are K1ABC and
        # W9XYZ, so a fixture using them is decoded a-priori - from what
        # jt9 expects to hear - and reads as far more sensitive than it is.
        # Calls nobody here uses, so only AP type 1 (CQ) is left.
        out = subprocess.run(["jt9", flag, "-p", period, "-d", "3",
                              "-c", "KA1ZZZ", "-x", "KB2ZZZ",
                              str(Path(path).resolve())],
                             cwd=tmp, capture_output=True, text=True).stdout
    got = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 6 or not parts[0].isdigit() or parts[4] not in "~+":
            continue
        words = parts[5:]
        ap = ""
        # Trailing flags: "a1".."a8" for an a-priori decode, "?" for one
        # jt9 is not sure of.
        while words and (words[-1] == "?" or (words[-1][0] == "a"
                                              and words[-1][1:].isdigit())):
            if words[-1] != "?":
                ap = words[-1]
            words = words[:-1]
        row = {"snr": int(parts[1]), "dt": float(parts[2]),
               "freq": int(parts[3]), "text": " ".join(words)}
        if ap:
            row["ap"] = ap
        got.append(row)
    return got


def make(name: str, mode: str, rows, seed: int) -> None:
    rng = np.random.default_rng(seed)
    m = ftx.MODES[mode]
    n = int(m.slot * RATE)
    x = rng.standard_normal(n)
    with tempfile.TemporaryDirectory() as tmp:
        for text, f0, dt, snr in rows:
            if mode == "ft8":
                s = ft8sim(text, f0, dt, Path(tmp))[:n]
            else:
                s = np.zeros(n)
                one = ftx.synth(ftx.message_symbols(text, m), f0, m)
                at = int(round((ftx.NOMINAL_START + dt) * RATE))
                s[at:at + one.size] = one[:max(0, n - at)]
            x[:s.size] += amplitude(snr) * s
    wav = OUT / f"{name}.wav"
    write_wav(wav, x * NOISE_RMS)
    expect(wav, mode, made=[{"text": t, "freq": f, "dt": d, "snr": s}
                            for t, f, d, s in rows])


def expect(wav: Path, mode: str, made=None, note: str = "") -> None:
    meta = wav.with_suffix(".json")
    old = json.loads(meta.read_text()) if meta.exists() else {}
    old.update({"mode": mode, "jt9": jt9(wav, mode),
                "jt9_version": subprocess.run(
                    ["wsjtx_app_version", "-v"], capture_output=True,
                    text=True).stdout.strip() or "?"})
    if made is not None:
        old["made"] = made
    if note:
        old["note"] = note
    meta.write_text(json.dumps(old, indent=1) + "\n")
    print(f"{wav.name}: jt9 decoded {len(old['jt9'])}")


# Messages whose encoding WSJT-X is asked for: every message type and
# report form, the awkward callsigns, and everything the card's mock plays.
CODE_MESSAGES = [
    "CQ VA7HDX CN89", "VE7XYZ VA7HDX -12", "VA7HDX VE7XYZ R-05",
    "VE7XYZ VA7HDX RR73", "VE7XYZ VA7HDX 73", "VE7XYZ VA7HDX RRR",
    "CQ DX K1ABC FN42", "CQ POTA W1AW FN31", "CQ 145 K1ABC FN42",
    "K1ABC/R W9XYZ EN37", "G4ABC/P PA9XYZ JO22", "K1ABC W9XYZ R EN37",
    "W9XYZ K1ABC +03", "QRZ K1ABC FN42", "DE K1ABC FN42", "K1ABC W9XYZ +25",
    "CQ K1A FN42", "CQ 4X1ABC KM72", "A1ABC B2XYZ",
    "CQ PJ4/K1ABC", "<PJ4/K1ABC> W9XYZ -10", "TNX BOB 73 GL", "HELLO WORLD",
    # WSJT-X alters these as it encodes them (ft8code marks them *): what
    # it sends is what the decoder must read, not what was typed.
    "PJ4/K1ABC W9XYZ RR73", "3DA0XYZ K1ABC FN42",
]


def codes() -> None:
    from ftx import MOCK_FT4, MOCK_FT8
    msgs = list(dict.fromkeys(CODE_MESSAGES + [m[0] for s in MOCK_FT8 + MOCK_FT4
                                               for m in s]))
    out = {"made_with": subprocess.run(["wsjtx_app_version", "-v"],
                                       capture_output=True,
                                       text=True).stdout.strip(),
           "messages": []}
    for m in msgs:
        row = {"text": m}
        for prog in ("ft8code", "ft4code"):
            lines = subprocess.run([prog, m], capture_output=True,
                                   text=True).stdout.splitlines()

            def after(tag):
                for i, line in enumerate(lines):
                    if tag in line:
                        return lines[i + 1].strip()
                return ""
            i = next(i for i, line in enumerate(lines)
                     if "Channel symbols" in line)
            sym = "".join(c for c in "".join(lines[i + 2:]) if c.isdigit())
            dec = next(line for line in lines if line.strip().startswith("1."))
            # "1. <sent>  <decoded>  [*]  i3.n3 <kind>"; the columns are
            # fixed-width in WSJT-X's output.
            decoded = dec[41:78].strip()
            key = prog[:3]
            row[key] = {"symbols": sym, "decoded": decoded,
                        "altered": dec[78:81].strip() == "*"}
            if prog == "ft8code":
                row["bits77"] = after("77 bits")
                row["crc14"] = after("CRC")
                row["parity83"] = after("Parity")
        out["messages"].append(row)
    (OUT / "codes.json").write_text(json.dumps(out, indent=1) + "\n")
    print(f"codes.json: {len(msgs)} messages")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", nargs="*", metavar="WAV",
                    help="only re-run jt9 on these (or all) fixtures")
    ap.add_argument("--mode", default="", help="ft8 or ft4, for --expect")
    ap.add_argument("--note", default="")
    ap.add_argument("--codes", action="store_true",
                    help="only write codes.json, from ft8code and ft4code")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.codes:
        codes()
        return 0
    if a.expect is not None:
        for p in [Path(x) for x in a.expect] or sorted(OUT.glob("*.wav")):
            mode = a.mode or ("ft4" if "ft4" in p.name else "ft8")
            expect(p, mode, note=a.note)
        return 0
    make("ft8_sim_multi", "ft8", FT8_MULTI, 8)
    make("ft4_sim_multi", "ft4", FT4_MULTI, 4)
    codes()
    return 0


if __name__ == "__main__":
    sys.exit(main())
