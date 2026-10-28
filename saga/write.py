"""Script writer: timeline.json -> script.json (narration) + saga.md (the written saga).

A local LLM (llama-server, OpenAI-compatible) writes each chapter as a list of sentences,
and every sentence must cite fact ids from the timeline. A validator rejects the sentence
if it cites nothing, cites a fact from another chapter, uses a number that is not in its
cited facts, names an author it did not cite, or quotes something that is not verbatim.
Rejected chapters are retried with the errors; after that the chapter falls back to the
facts themselves. The narration can be dramatic; it cannot be made up.

Usage: python -m saga.write timeline.json -o outdir [--model gemma-4-12b]
Env:   LLAMA_BASE_URL (default http://localhost:8080), LLAMA_API_KEY
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

SYSTEM = r"""You narrate documentaries about software projects, in the voice of a serious
history documentary: measured, vivid, ominous where it fits, never jokey. You are given
numbered FACTS mined from a git repository. You write narration for ONE chapter.

Hard rules (a fact checker enforces them):
- Every sentence cites the fact ids it rests on. A sentence may add mood, framing or
  meaning, but no new information: no invented events, motives, feelings, places, people,
  tools, quotes or numbers. If a fact does not say why something happened, do not say why.
- Every number you write must appear, as digits, in a fact you cite for that sentence.
  Write it the way the fact writes it (1,285 / 9.8 / 3:37 a.m.). Leave numbers out rather than approximate.
- Quote commit messages only verbatim, in double quotes, and only from a cited fact.
  Quote at most one short message per sentence. Skip prefixes like "feat(mcp):".
- Refer to people by name, or as "they". Never "he" or "she".
- Do not just restate the facts in order. Find the story in them: open with a hook,
  contrast, build tension, land on a short final sentence. Vary sentence length.
- Spoken aloud by a TTS voice: plain words, no markdown, no emoji, no exclamation marks,
  no parentheses, no lists.

Example of the style, for an unrelated repository:
FACTS
  F1: The first commit was made on March 3, 2015 by Ada Byte, titled "hello".
  F2: In its first 30 days the repository got 412 commits.
GOOD
{"lines": [
 {"text": "Every empire starts somewhere.", "cites": ["F1"]},
 {"text": "This one started on March 3, 2015, with a commit from Ada Byte that said only \"hello\".", "cites": ["F1"]},
 {"text": "It did not stay quiet for long.", "cites": ["F2"]},
 {"text": "Within 30 days, 412 commits had followed.", "cites": ["F2"]}]}

Reply with JSON only, in exactly that shape."""

NUMWORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                         "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}


# --------------------------------------------------------------------------- llm
def api_key() -> str | None:
    return os.environ.get("LLAMA_API_KEY")


def chat(messages: list[dict], model: str, *, temperature=0.7, max_tokens=900) -> str:
    base = os.environ.get("LLAMA_BASE_URL", "http://localhost:8080").rstrip("/")
    key = api_key()
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            # gemma-4 is a thinking model; this is the only switch that turns it off on llama-server
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(f"{base}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.load(r)["choices"][0]["message"]["content"] or ""
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise SystemExit("llama-server returned 401: set LLAMA_API_KEY to its --api-key")
        raise


# ---------------------------------------------------------------------- validate
def norm_nums(text: str) -> set[str]:
    nums = {m.replace(",", "") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}
    return {x.rstrip(".") for x in nums}


def check_line(line: dict, allowed: dict[str, dict], authors: set[str]) -> list[str]:
    errs = []
    text, cites = line.get("text", "").strip(), line.get("cites") or []
    if not text:
        return ["empty sentence"]
    if not cites:
        return [f"no cites: {text!r}"]
    bad = [c for c in cites if c not in allowed]
    if bad:
        errs.append(f"cites {bad} which are not facts of this chapter: {text!r}")
    src = " ".join(allowed[c]["text"] for c in cites if c in allowed)
    missing = norm_nums(text) - norm_nums(src)
    if missing:
        errs.append(f"numbers {sorted(missing)} are not in the cited facts: {text!r}")
    for w in re.findall(r"[a-z]+", text.lower()):
        if w in NUMWORDS and NUMWORDS[w] > 1 and str(NUMWORDS[w]) not in norm_nums(src):
            errs.append(f"number word {w!r} is not in the cited facts: {text!r}")
    for a in authors:
        if len(a) > 2 and re.search(rf"(?<!\w){re.escape(a)}(?!\w)", text, re.I) and a.lower() not in src.lower():
            errs.append(f"names {a!r} without citing a fact about them: {text!r}")
    if len(re.findall(r"[\"“][^\"”]{3,}[\"”]", text)) > 1:
        errs.append(f"more than one quote in a sentence: {text!r}")
    if re.search(r"\b(conclusion|the end of|died|dead|abandon)", text, re.I):
        errs.append(f"claims the project or a person ended; the facts only say what was committed: {text!r}")
    for q in re.findall(r"[\"“]([^\"”]{3,})[\"”]", text):
        if q.strip(" .,…").lower() not in src.lower():
            errs.append(f"quote {q!r} is not verbatim in the cited facts: {text!r}")
    if "!" in text:
        errs.append(f"exclamation mark: {text!r}")
    if re.search(r"\b(he|she|him|her|his|hers)\b", text, re.I):
        errs.append(f"gendered pronoun; use the name or 'they': {text!r}")
    return errs


def fallback(chapter: dict, facts: dict) -> list[dict]:
    return [{"text": facts[f]["text"], "cites": [f], "fallback": True} for f in chapter["facts"][:3]]


# ------------------------------------------------------------------------ writer
def write_chapter(t: dict, ch: dict, model: str, words: tuple[int, int], prev: list[str], tries=3) -> tuple[list, list]:
    facts = t["facts"]
    allowed = {f: facts[f] for f in ch["facts"]}
    authors = {c["author"] for chap in t["chapters"] for c in chap["cards"]}
    outline = "\n".join(f"  {c['n']}. {c['title']} ({c['date'][:4]})" for c in t["chapters"])
    user = (f"REPOSITORY: {t['repo']['name']}\nTHE FULL DOCUMENTARY, chapter by chapter:\n{outline}\n\n"
            f"WRITE CHAPTER {ch['n']}: \"{ch['title']}\" ({ch['date']}).\n"
            f"Length: {words[0]}-{words[1]} words in total, 2 to 4 sentences.\n"
            + (f"The previous chapter ended: \"{prev[-1]}\"\nDo not repeat it.\n" if prev else
               "This is the opening chapter: begin the documentary.\n")
            + "FACTS you may use (cite their ids):\n"
            + "\n".join(f"  {k}: {v['text']}" for k, v in allowed.items()))
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    log = []
    for attempt in range(tries):
        raw = chat(msgs, model)
        try:
            lines = json.loads(raw)["lines"]
            assert isinstance(lines, list) and lines
        except Exception:
            errs = [f"reply was not the JSON shape asked for: {raw[:200]!r}"]
        else:
            errs = [e for ln in lines for e in check_line(ln, allowed, authors)]
        log.append({"attempt": attempt + 1, "raw": raw, "errors": errs})
        if not errs:
            return [{"text": (lambda x: x if x[-1] in ".?…\"" else x + ".")(ln["text"].strip()),
                     "cites": ln["cites"]} for ln in lines], log
        msgs += [{"role": "assistant", "content": raw},
                 {"role": "user", "content": "Rejected by the fact checker:\n- " + "\n- ".join(errs[:8])
                  + "\nRewrite the whole chapter. Same JSON shape."}]
    return fallback(ch, facts), log


def speakable(s: str) -> str:
    """What the TTS reads. Captions keep the original text."""
    s = re.sub(r"\bv?(\d+)\.(\d+)\.0\b(?![-.\w])", r"version \1 point \2", s)
    s = re.sub(r"\bv(\d+)\.(\d+)\.(\d+)\b", r"version \1 point \2 point \3", s)
    s = re.sub(r"(\d)-(alpha|beta|rc)\.(\d+)", r"\1 \2 \3", s)
    s = s.replace("%", " percent").replace("…", "").replace("+", "")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def remote_url(repo_path: str | None) -> str | None:
    if not repo_path:
        return None
    r = subprocess.run(["git", "-C", repo_path, "remote", "get-url", "origin"], capture_output=True, text=True)
    u = r.stdout.strip()
    m = re.match(r"(?:git@|https://)github\.com[:/](.+?)(?:\.git)?$", u)
    return f"https://github.com/{m.group(1)}" if m else None


def saga_md(t: dict, script: dict, base: str | None) -> str:
    facts, out = t["facts"], []
    r = t["repo"]
    out.append(f"# {script['title']}\n\n*{script['subtitle']}*\n")
    out.append(f"> Mined from {r['commits']:,} commits on `{r['head'][:10]}`. Every sentence below cites the "
               "commit it came from; numbers in brackets point to the evidence at the bottom.\n")
    used = []
    for ch in script["chapters"]:
        out.append(f"## {ch['n']}. {ch['title']}\n\n<sub>{ch['date']}</sub>\n")
        para = []
        for ln in ch["lines"]:
            refs = []
            for c in ln["cites"]:
                if c not in used:
                    used.append(c)
                refs.append(f"[^{used.index(c) + 1}]")
            para.append(ln["text"] + "".join(refs))
        out.append(" ".join(para) + "\n")
    out.append("---\n\n### Evidence\n")
    for i, fid in enumerate(used, 1):
        f = facts[fid]
        sha = f.get("sha")
        link = (f"[`{sha[:7]}`]({base}/commit/{sha})" if base else f"`{sha[:7]}`") if sha else ""
        out.append(f"[^{i}]: {f['text']} {link}")
    out.append("\n<sub>Written by a local LLM from facts mined by repo-saga. "
               "Fallback chapters, if any, are the raw facts.</sub>\n")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("timeline")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--model", default=os.environ.get("SAGA_MODEL", "gemma-4-12b"))
    ap.add_argument("--repo", help="repo path, to link commits to its GitHub remote")
    a = ap.parse_args(argv)
    t = json.load(open(a.timeline, encoding="utf-8"))
    os.makedirs(a.out, exist_ok=True)
    chapters, logs, prev = [], {}, []
    for i, ch in enumerate(t["chapters"]):
        words = (40, 55)
        lines, log = write_chapter(t, ch, a.model, words, prev)
        logs[ch["n"]] = log
        prev = [ln["text"] for ln in lines]
        tag = "FALLBACK" if lines[0].get("fallback") else f"ok after {len(log)}"
        print(f"  {ch['n']:>4}. {ch['title']:<34} {tag}", file=sys.stderr)
        for ln in lines:
            ln["speak"] = speakable(ln["text"])
        chapters.append({**{k: ch[k] for k in ("n", "kind", "title", "date", "cards")}, "lines": lines})
    r = t["repo"]
    script = {"schema": "repo-saga/script@1", "repo": r["name"], "head": r["head"], "model": a.model,
              "title": r["name"], "subtitle": f"A history in {len(chapters)} chapters, " + (r['first'][:4] if r['first'][:4] == r['last'][:4] else f"{r['first'][:4]}–{r['last'][:4]}"),
              "chapters": chapters}
    json.dump(script, open(os.path.join(a.out, "script.json"), "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    json.dump(logs, open(os.path.join(a.out, "factcheck-log.json"), "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    open(os.path.join(a.out, "saga.md"), "w", encoding="utf-8").write(saga_md(t, script, remote_url(a.repo)))
    n_words = sum(len(ln["text"].split()) for c in chapters for ln in c["lines"])
    fb = sum(1 for c in chapters if c["lines"][0].get("fallback"))
    print(f"{r['name']}: {n_words} words (~{n_words / 2.6:.0f}s spoken), {fb} fallback chapters -> {a.out}")


if __name__ == "__main__":
    main()
