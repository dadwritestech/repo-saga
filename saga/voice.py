"""Narration: script.json -> one voiced mp3 per chapter + timing.json (sentence start/end).

Qwen3-TTS through llama.cpp's llama-tts, the same engine as the LlamaForge launch video.
llama-tts pays ~1-2 min of fixed overhead per call on a busy machine, so it is called once
per chapter, not once per sentence. Sentence times for captions are recovered from the
pauses in the chapter audio. One narrator throughout: chapters clone a reference clip.

Usage: python -m saga.voice outdir [--speaker ref.wav | --seed 42]
Env:   LLAMA_TTS_BIN, QWEN_TTS_MODEL, QWEN_TTS_MMPROJ (defaults: the LlamaForge install)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import wave

import numpy as np

BIN = os.environ.get("LLAMA_TTS_BIN", "D:/LlamaForge/llama.cpp/build/bin/llama-tts.exe")
MODEL = os.environ.get("QWEN_TTS_MODEL", "D:/LlamaForge/tts/models/Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf")
MMPROJ = os.environ.get("QWEN_TTS_MMPROJ", "D:/LlamaForge/tts/models/mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf")
REF_TEXT = "Every project has a history. Most of it is written in the commits, one line at a time."
FRAMES_PER_CHAR, MIN_FRAMES, MAX_FRAMES = 1.6, 512, 4096        # Qwen3-TTS runs at 12 frames/s
HOP, THR, MAX_GAP, PAD, PEAK_DB = 0.01, 0.012, 0.55, 0.05, -1.5


def tts(text: str, out: str, seed: int, speaker: str | None) -> float:
    frames = max(MIN_FRAMES, min(MAX_FRAMES, int(len(text) * FRAMES_PER_CHAR) + 120))
    cmd = [BIN, "-m", MODEL, "--mmproj", MMPROJ, "-p", text, "-o", out, "-n", str(frames),
           "--tts-lang", "en", "-s", str(seed)] + (["--tts-speaker-file", speaker] if speaker else [])
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode or not os.path.exists(out):
        raise SystemExit(f"llama-tts failed ({r.returncode}):\n{(r.stderr or '')[-1500:]}")
    print(f"    llama-tts {time.time() - t0:.0f}s wall", file=sys.stderr)
    x, sr = read(out)
    return len(x) / sr


def read(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    return x, sr


def fit(src: str, n_lines: int, weights: list[int]) -> tuple[np.ndarray, int, list[tuple[float, float]]]:
    """Trim edges, cap long pauses, normalise; then split into n_lines sentences at pauses."""
    x, sr = read(src)
    hop = int(sr * HOP)
    env = np.array([np.sqrt((x[i:i + hop] ** 2).mean()) for i in range(0, len(x) - hop, hop)])
    loud = np.where(env > THR)[0]
    if not len(loud):
        raise SystemExit(f"{src} is silent")
    a, b = max(0, loud[0] - int(PAD / HOP)), min(len(env), loud[-1] + 1 + int(PAD / HOP))
    keep, quiet, run = [], [], 0
    for f in range(a, b):
        run = 0 if env[f] > THR else run + 1
        if run <= MAX_GAP / HOP:
            keep.append(f)
            quiet.append(run > 0)
    y = np.concatenate([x[f * hop:(f + 1) * hop] for f in keep])
    fade = int(0.015 * sr)
    y[:fade] *= np.linspace(0, 1, fade); y[-fade:] *= np.linspace(1, 0, fade)
    y *= 10 ** (PEAK_DB / 20) / np.abs(y).max()
    total = len(keep) * HOP
    # pauses in the fitted audio: (midpoint seconds, length seconds)
    pauses, i = [], 0
    while i < len(quiet):
        if quiet[i]:
            j = i
            while j < len(quiet) and quiet[j]:
                j += 1
            if (j - i) * HOP >= 0.18:
                pauses.append(((i + j) / 2 * HOP, (j - i) * HOP))
            i = j
        else:
            i += 1
    # expected sentence boundaries by character share; snap each to the nearest long-ish pause
    cuts, acc, used = [], 0, set()
    for w in weights[:-1]:
        acc += w
        want = total * acc / sum(weights)
        near = [(abs(m - want), k) for k, (m, ln) in enumerate(pauses) if k not in used and abs(m - want) < 0.3 * total / n_lines]
        if near:
            k = min(near)[1]; used.add(k); cuts.append(pauses[k][0])
        else:
            cuts.append(want)
    cuts = sorted(cuts)
    edges = [0.0] + cuts + [total]
    return y, sr, [(round(edges[k], 2), round(edges[k + 1], 2)) for k in range(n_lines)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("outdir")
    ap.add_argument("--speaker", help="reference wav for the narrator voice")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--only", help="comma list of chapter numerals to (re)voice")
    a = ap.parse_args(argv)
    script = json.load(open(os.path.join(a.outdir, "script.json"), encoding="utf-8"))
    raw = os.path.join(a.outdir, "work", "tts"); os.makedirs(raw, exist_ok=True)
    aud = os.path.join(a.outdir, "audio"); os.makedirs(aud, exist_ok=True)
    speaker = a.speaker
    if not speaker:                                 # make (once) a narrator reference from the seed
        speaker = os.path.join(raw, f"narrator-seed{a.seed}.wav")
        if not os.path.exists(speaker):
            print(f"  narrator reference, seed {a.seed}", file=sys.stderr)
            tts(REF_TEXT, speaker, a.seed, None)
    tpath = os.path.join(aud, "timing.json")
    timing = json.load(open(tpath)) if os.path.exists(tpath) else {}
    only = set(a.only.split(",")) if a.only else None
    for ch in script["chapters"]:
        if only and ch["n"] not in only or (not only and ch["n"] in timing):
            continue
        text = " ".join(ln["speak"] for ln in ch["lines"])
        wav = os.path.join(raw, f"ch-{ch['n']}.wav")
        print(f"  {ch['n']}. {ch['title']} ({len(text.split())} words)", file=sys.stderr)
        tts(text, wav, a.seed, speaker)
        y, sr, spans = fit(wav, len(ch["lines"]), [len(ln["speak"]) for ln in ch["lines"]])
        fitted = os.path.join(raw, f"ch-{ch['n']}-fit.wav")
        with wave.open(fitted, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes((y * 32767).astype(np.int16).tobytes())
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", fitted, "-ar", "44100", "-ac", "1",
                        "-c:a", "libmp3lame", "-b:a", "160k", os.path.join(aud, f"ch-{ch['n']}.mp3")], check=True)
        timing[ch["n"]] = {"seconds": round(len(y) / sr, 2), "lines": spans}
        json.dump(timing, open(tpath, "w"), indent=2)    # save after every chapter: resumable
        print(f"    {len(y) / sr:.1f}s voiced, lines at {[s for s, _ in spans]}", file=sys.stderr)
    print(f"{script['repo']}: {sum(v['seconds'] for v in timing.values()):.0f}s of narration -> {aud}")


if __name__ == "__main__":
    main()
