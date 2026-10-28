"""repo-saga: one command, repo in, documentary out.

Usage: python -m saga <repo> [-o out/<name>] [--silent] [--chapters 10] [--model gemma-4-12b]

  mine   -> timeline.json      (chapters of facts, each tied to a commit)
  write  -> script.json, saga.md, factcheck-log.json   (needs llama-server, LLAMA_API_KEY)
  voice  -> audio/              (Qwen3-TTS via llama-tts; skipped with --silent)
  render -> <name>-saga.mp4     (HyperFrames)
"""
from __future__ import annotations

import argparse
import os

from . import mine, render, voice, write


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo")
    ap.add_argument("-o", "--out")
    ap.add_argument("--chapters", type=int, default=10)
    ap.add_argument("--model", default="gemma-4-12b")
    ap.add_argument("--silent", action="store_true", help="skip narration; captions only")
    a = ap.parse_args(argv)
    out = a.out or os.path.join("out", os.path.basename(os.path.abspath(a.repo)).lower())
    os.makedirs(out, exist_ok=True)
    tl = os.path.join(out, "timeline.json")
    mine.main([a.repo, "-o", tl, "--chapters", str(a.chapters)])
    write.main([tl, "-o", out, "--repo", a.repo, "--model", a.model])
    if not a.silent:
        voice.main([out])
    render.main([out])


if __name__ == "__main__":
    main()
