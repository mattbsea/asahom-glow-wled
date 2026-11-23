#!/usr/bin/env python3
"""Decode an SK6812-RGBW-style single-wire LED data line from a sigrok .sr capture.

usage: decode_ws_like.py CAPTURE.sr [--bit N] [--gap-us 100] [--thr-ns 560]
                         [--words] [--start S] [--end S] [--max-frames N]
                         [--tail-bits 24|0] [--edges-out FILE.npz]

Reads a sigrok session (.sr, zip of logic-1-N chunks, unitsize 1 or 2 bytes),
finds edges of one channel (--bit = bit within the sample unit; default = the
bit with the most edges), splits frames at low gaps >= --gap-us, classifies
each bit by its high time (< --thr-ns => 0, else 1), decodes 32-bit words
(G R B W, MSB first) and reports per frame: start time, bit count, light words,
tail (last 24 bits when the bit count is 32*N+24), words.
A frame whose bit count is not 32*N+tail is reported raw (no tail split).
Use --tail-bits 0 for WLED-style frames (no tail).
Requires numpy.
"""
import argparse
import configparser
import io
import sys
import zipfile
from collections import Counter

import numpy as np


def load_edges(path, bit=None):
    """Return (samplerate, bit, rise_idx, fall_idx, nsamples, initial_level)."""
    z = zipfile.ZipFile(path)
    meta = configparser.ConfigParser()
    meta.read_string(z.read("metadata").decode())
    dev = meta["device 1"]
    rate_s = dev["samplerate"].split()
    rate = float(rate_s[0]) * {"Hz": 1, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}[rate_s[1]]
    unit = int(dev["unitsize"])
    base = dev["capturefile"]
    chunks = sorted((n for n in z.namelist() if n.startswith(base + "-")),
                    key=lambda n: int(n.rsplit("-", 1)[1]))
    dt = {1: "u1", 2: "<u2", 4: "<u4"}[unit]
    # pass 1 (only if bit unknown): count edges per bit
    if bit is None:
        cnt = np.zeros(8 * unit, dtype=np.int64)
        prev = None
        for n in chunks:
            a = np.frombuffer(z.read(n), dtype=dt)
            if prev is not None:
                a = np.concatenate(([prev], a))
            prev = a[-1]
            x = a[1:] ^ a[:-1]
            for b in range(8 * unit):
                cnt[b] += int(np.count_nonzero((x >> b) & 1))
        bit = int(cnt.argmax())
    rises, falls, off, prev = [], [], 0, None
    first = None
    for n in chunks:
        a = (np.frombuffer(z.read(n), dtype=dt) >> bit) & 1
        a = a.astype(np.int8)
        if prev is None:
            first = int(a[0])
        else:
            a = np.concatenate(([prev], a))
            off -= 1
        d = np.diff(a)
        rises.append(np.flatnonzero(d == 1) + 1 + off)
        falls.append(np.flatnonzero(d == -1) + 1 + off)
        off += len(a)
        prev = a[-1]
    return rate, bit, np.concatenate(rises), np.concatenate(falls), off, first


def pair_pulses(rise, fall):
    """Return arrays (t_rise, high_len, period_to_next_rise) in samples; drop a leading fall."""
    if len(fall) and len(rise) and fall[0] < rise[0]:
        fall = fall[1:]
    n = min(len(rise), len(fall))
    rise, fall = rise[:n], fall[:n]
    return rise, fall - rise


def split_frames(rise, high, gap_samples):
    # gap = time from fall of one pulse to rise of next
    fall = rise + high
    gaps = rise[1:] - fall[:-1]
    cuts = np.flatnonzero(gaps >= gap_samples) + 1
    return np.split(np.arange(len(rise)), cuts)


def bits_to_int(bits):
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture")
    ap.add_argument("--bit", type=int, default=None)
    ap.add_argument("--gap-us", type=float, default=100.0, help="low time that ends a frame")
    ap.add_argument("--thr-ns", type=float, default=560.0, help="high-time threshold between 0 and 1")
    ap.add_argument("--tail-bits", type=int, default=24, help="tail length in bits (0 for WLED-style frames)")
    ap.add_argument("--words", action="store_true", help="print all words of each frame")
    ap.add_argument("--start", type=float, default=0.0, help="only frames starting >= S seconds")
    ap.add_argument("--end", type=float, default=1e18)
    ap.add_argument("--max-frames", type=int, default=60, help="max per-frame lines (0 = all)")
    ap.add_argument("--edges-out", help="save (rise,fall) sample indices as .npz")
    a = ap.parse_args()

    rate, bit, rise, fall, n, first = load_edges(a.capture, a.bit)
    ns = 1e9 / rate
    print(f"# {a.capture}: {n} samples @ {rate/1e6:g} MS/s ({n/rate:.3f} s), channel bit {bit}, "
          f"{len(rise)} rising edges, initial level {first}")
    if a.edges_out:
        np.savez(a.edges_out, rise=rise, fall=fall, rate=rate)
    rise, high = pair_pulses(rise, fall)
    if not len(rise):
        print("no pulses"); return
    frames = split_frames(rise, high, int(a.gap_us * 1e-6 * rate))
    thr = a.thr_ns / ns
    stats0, stats1, periods = [], [], []
    starts, lens = [], []
    shown = 0
    tailc = Counter()
    nwc = Counter()
    for fi, idx in enumerate(frames):
        r, h = rise[idx], high[idx]
        t0 = r[0] / rate
        if not (a.start <= t0 <= a.end):
            continue
        bits = (h >= thr).astype(np.uint8)
        nb = len(bits)
        if nb > 1:
            periods.append(np.diff(r))
        stats0.append(h[bits == 0]); stats1.append(h[bits == 1])
        starts.append(r[0]); lens.append(nb)
        nw, rem = divmod(nb - a.tail_bits, 32)
        ok = nb >= a.tail_bits and rem == 0
        words = [bits_to_int(bits[32 * i:32 * i + 32]) for i in range(nw)] if ok else []
        tail = bits_to_int(bits[32 * nw:]) if ok and a.tail_bits else 0
        if ok:
            tailc[tail] += 1; nwc[nw] += 1
        else:
            nwc[f"raw{nb}"] += 1
        if a.max_frames == 0 or shown < a.max_frames:
            shown += 1
            gap_prev = (r[0] - (rise[frames[fi-1][-1]] + high[frames[fi-1][-1]])) / rate * 1e6 if fi else float("nan")
            if ok:
                line = f"frame {fi:5d} t={t0:10.6f}s bits={nb:5d} words={nw:3d} tail={tail:06x} gap_before={gap_prev:8.1f}us"
            else:
                line = f"frame {fi:5d} t={t0:10.6f}s bits={nb:5d} (not 32N+tail) gap_before={gap_prev:8.1f}us"
            if a.words and ok:
                line += "\n    " + " ".join(f"{w:08x}" for w in words)
            elif ok and nw:
                line += "  first=" + f"{words[0]:08x}" + " last=" + f"{words[-1]:08x}"
            print(line)
    if not starts:
        print("no frames in range"); return
    s0 = np.concatenate(stats0) * ns if stats0 else np.array([])
    s1 = np.concatenate(stats1) * ns if stats1 else np.array([])
    per = np.concatenate(periods) * ns if periods else np.array([])
    # in-frame periods only (ignore the occasional outlier)
    per = per[per < 3000]
    print("\n# --- summary (ns, sample resolution %.1f ns) ---" % ns)
    for name, s in (("0-bit high", s0), ("1-bit high", s1), ("bit period", per)):
        if len(s):
            print(f"{name:10s}: n={len(s):7d} mean={s.mean():7.1f} sd={s.std():5.1f} min={s.min():7.1f} max={s.max():7.1f}")
    st = np.array(starts) / rate
    if len(st) > 1:
        d = np.diff(st)
        print(f"frame start interval: median={np.median(d)*1e3:.3f} ms  (rate {1/np.median(d):.2f} Hz) "
              f"min={d.min()*1e3:.3f} max={d.max()*1e3:.3f} ms")
    print("bit-count histogram:", dict(Counter(lens).most_common(12)))
    print("words/frame histogram:", dict(nwc.most_common(12)))
    print("tail histogram:", {f"{k:06x}": v for k, v in tailc.most_common(8)})


if __name__ == "__main__":
    main()
