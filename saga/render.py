"""Video: script.json + timeline.json (+ audio/timing.json) -> HyperFrames composition -> mp4.

Writes <outdir>/video/index.html: one GSAP timeline, chapter cards, the commit cards each
sentence cites (they light up while it is spoken), captions that show the cited SHA, and an
activity strip along the bottom with a playhead that walks the history. Without voiced
audio it lays out a silent cut from estimated reading time.

Usage: python -m saga.render outdir [--no-render] [--quality delivery]
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import shutil
import subprocess
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(os.path.dirname(HERE), "template")
HYPERFRAMES = "hyperframes@0.8.137"                # pinned, like the LlamaForge video
TITLE, CARD, TAIL, CREDITS, WPS = 5.0, 2.4, 1.1, 6.5, 2.6
STRIP_W = 1920 - 2 * 110                             # px; #strip spans left:110 right:110
e = html.escape


def frac(d: str, first: str, last: str) -> float:
    a, b, x = (date.fromisoformat(s[:10]).toordinal() for s in (first, last, d))
    return 0.0 if b == a else max(0.0, min(1.0, (x - a) / (b - a)))


def plan(script: dict, timing: dict | None) -> tuple[list[dict], float]:
    """Absolute times for every chapter and sentence."""
    t, out = TITLE, []
    for ch in script["chapters"]:
        tm = (timing or {}).get(ch["n"])
        if tm:
            nar, spans = tm["seconds"], tm["lines"]
        else:                                       # silent cut: estimated reading time
            secs = [max(1.6, len(ln["text"].split()) / WPS + 0.35) for ln in ch["lines"]]
            spans, acc = [], 0.0
            for s in secs:
                spans.append((acc, acc + s)); acc += s
            nar = acc
        v0 = t + CARD - 0.3
        out.append({"ch": ch, "start": t, "voice": v0, "nar": nar, "dur": CARD - 0.3 + nar + TAIL,
                    "lines": [(v0 + a, v0 + b) for a, b in spans], "voiced": bool(tm)})
        t += CARD - 0.3 + nar + TAIL
    return out, t + CREDITS


def build_html(script: dict, timeline: dict, timing: dict | None) -> tuple[str, float]:
    chapters, total = plan(script, timing)
    facts, repo = timeline["facts"], timeline["repo"]
    first, last = repo["first"], repo["last"]
    act = timeline["activity"]
    peak = max(1, max(m["commits"] for m in act))
    bars = "".join(f'<i style="height:{4 + 96 * math.sqrt(m["commits"] / peak):.1f}%"></i>' for m in act)
    ticks = "".join(f'<b class="tick" style="left:{100 * frac(c["ch"]["date"], first, last):.2f}%"></b>' for c in chapters)
    yr0, yr1 = first[:4], last[:4]
    if yr0 == yr1:                                  # one-year repo: label the strip by month
        yr0, yr1 = (datetime.fromisoformat(x).strftime("%b %Y") for x in (first, last))
    body, js = [], []
    A = js.append

    # ---------------- title
    body.append(f'''
      <div id="title" class="clip" data-start="0" data-duration="{TITLE}" data-track-index="1">
        <div class="kicker abs" id="t-kick" style="left:0;right:0;top:330px;text-align:center">a repo-saga documentary</div>
        <div class="fr abs" id="t-name" style="left:0;right:0;top:390px;text-align:center;font-size:{min(230, int(2600 / max(6, len(script['title']))))}px">{e(script['title'])}</div>
        <div class="sub abs" id="t-sub" style="left:0;right:0;top:680px;text-align:center">{e(script['subtitle'])}</div>
        <div class="mono abs" id="t-sha" style="left:0;right:0;top:760px;text-align:center">{repo['commits']:,} commits &middot; {repo['authors']:,} authors &middot; HEAD {repo['head'][:7]}</div>
      </div>''')
    A('tl.from("#t-kick",{opacity:0,y:16,duration:.8,ease:"power2.out"},.3);')
    A('tl.from("#t-name",{opacity:0,scale:1.08,filter:"blur(14px)",duration:1.6,ease:"power3.out"},.5);')
    A('tl.from("#t-sub",{opacity:0,y:20,duration:.8,ease:"power2.out"},1.5);')
    A('tl.from("#t-sha",{opacity:0,duration:.8},2.1);')
    A(f'tl.to("#title",{{opacity:0,duration:.6,ease:"power2.in"}},{TITLE - .6});')

    # ---------------- chapters
    audio = []
    for i, c in enumerate(chapters):
        ch, s, d = c["ch"], c["start"], c["dur"]
        cid = f"c{i}"
        year = ch["date"][:4]
        cards = ch["cards"][:3]
        compact = len(cards) > 2
        card_html = []
        for k, cd in enumerate(cards):
            churn = cd["add"] + cd["del"]
            pa = 0 if not churn else 100 * cd["add"] / churn
            note = f'<span class="note">{e(cd["note"])}</span>' if cd.get("note") else ""
            card_html.append(f'''
          <div class="card{' compact' if compact else ''}" id="{cid}-k{k}" data-sha="{cd['sha']}">
            <div class="row"><span class="sha">{cd['sha']}</span><span class="when">{e(cd['date'])}</span>{note}</div>
            <div class="subj">&ldquo;{e(cd['subject'])}&rdquo;</div>
            <div class="row"><span class="who">{e(cd['author'])}</span>
              <span class="churn"><em class="plus">+{cd['add']:,}</em> <em class="minus">&minus;{cd['del']:,}</em></span></div>
            <div class="bar"><i style="width:{pa:.1f}%"></i></div>
          </div>''')
        caps = []
        for k, (ln, (a, b)) in enumerate(zip(ch["lines"], c["lines"])):
            shas = [facts[f]["sha"][:7] for f in ln["cites"] if facts.get(f, {}).get("sha")]
            chip = f'{" · ".join(ln["cites"])}{" &rarr; " + shas[0] if shas else ""}'
            caps.append(f'<div class="cap" id="{cid}-l{k}"><span class="chip">{chip}</span><span class="ct">{e(ln["text"])}</span></div>')
        body.append(f'''
      <div id="{cid}" class="clip" data-start="{s:.3f}" data-duration="{d:.3f}" data-track-index="1">
        <div class="year abs" id="{cid}-yr">{year}</div>
        <div class="cardwrap abs" id="{cid}-card">
          <div class="kicker">Chapter {ch['n']}</div>
          <div class="fr ctitle">{e(ch['title'])}</div>
          <div class="sub">{e(_nice(ch['date']))}</div>
        </div>
        <div class="head abs" id="{cid}-head"><span class="kicker">{ch['n']}</span> <span class="fr">{e(ch['title'])}</span><span class="hdate">{e(_nice(ch['date']))}</span></div>
        <div class="stack abs" id="{cid}-stack">{''.join(card_html)}</div>
        {''.join(caps)}
      </div>''')
        A(f'tl.fromTo("#{cid}-yr",{{opacity:0,scale:1.04}},{{opacity:1,scale:1,duration:{d:.2f},ease:"none"}},{s:.3f});')
        A(f'tl.from("#{cid}-card .kicker",{{opacity:0,scale:1.15,duration:.7,ease:"power2.out"}},{s + .1:.3f});')
        A(f'tl.from("#{cid}-card .ctitle",{{opacity:0,y:30,filter:"blur(10px)",duration:.9,ease:"power3.out"}},{s + .25:.3f});')
        A(f'tl.from("#{cid}-card .sub",{{opacity:0,duration:.6}},{s + .7:.3f});')
        A(f'tl.to("#{cid}-card",{{opacity:0,y:-40,duration:.5,ease:"power2.in"}},{s + CARD - .55:.3f});')
        A(f'tl.from("#{cid}-head",{{opacity:0,x:-30,duration:.6,ease:"power2.out"}},{s + CARD - .25:.3f});')
        for k in range(len(cards)):
            A(f'tl.from("#{cid}-k{k}",{{opacity:0,x:80,duration:.6,ease:"power3.out"}},{c["voice"] + .15 + .35 * k:.3f});')
        for k, (ln, (a, b)) in enumerate(zip(ch["lines"], c["lines"])):
            A(f'tl.fromTo("#{cid}-l{k}",{{opacity:0,y:14}},{{opacity:1,y:0,duration:.35,ease:"power2.out"}},{a:.3f});')
            A(f'tl.to("#{cid}-l{k}",{{opacity:0,duration:.25}},{max(a + .4, b - .05):.3f});')
            lit = {facts[f]["sha"][:7] for f in ln["cites"] if facts.get(f, {}).get("sha")}
            for kk, cd in enumerate(cards):
                if cd["sha"] in lit:
                    A(f'tl.to("#{cid}-k{kk}",{{borderColor:"#d9a441",boxShadow:"0 0 0 2px #d9a441, 0 0 60px rgba(217,164,65,.28)",duration:.3}},{a:.3f});')
                    A(f'tl.to("#{cid}-k{kk}",{{borderColor:"rgba(239,230,214,.16)",boxShadow:"0 0 0 0 rgba(0,0,0,0)",duration:.4}},{b:.3f});')
        A(f'tl.to("#{cid}",{{opacity:0,duration:.45,ease:"power2.in"}},{s + d - .45:.3f});')
        A(f'tl.to("#playhead",{{x:{STRIP_W * frac(ch["date"], first, last):.1f},duration:1.4,ease:"power3.inOut"}},{s:.3f});')
        A(f'tl.to("#tick{i}",{{opacity:1,duration:.3}},{s + .5:.3f});')
        if c["voiced"]:
            audio.append(f'<audio id="vo-{i}" class="clip" src="audio/ch-{ch["n"]}.mp3" data-start="{c["voice"]:.3f}" data-duration="{c["nar"] + .05:.3f}" data-volume="1" data-track-index="11"></audio>')
        audio.append(f'<audio id="hit-{i}" class="clip" src="audio/hit.mp3" data-start="{s:.3f}" data-duration="2.4" data-volume="0.55" data-track-index="12"></audio>')
    ticks = "".join(f'<b class="tick" id="tick{i}" style="left:{100 * frac(c["ch"]["date"], first, last):.2f}%"></b>' for i, c in enumerate(chapters))

    # ---------------- credits
    cs = total - CREDITS
    n_lines = sum(len(c["ch"]["lines"]) for c in chapters)
    fb = sum(1 for c in chapters if c["ch"]["lines"][0].get("fallback"))
    voiced = all(c["voiced"] for c in chapters)
    body.append(f'''
      <div id="credits" class="clip" data-start="{cs:.3f}" data-duration="{CREDITS}" data-track-index="1">
        <div class="fr abs" id="cr-1" style="left:0;right:0;top:340px;text-align:center;font-size:84px">Every sentence in this film cites a commit.</div>
        <div class="mono abs" id="cr-2" style="left:0;right:0;top:470px;text-align:center">{n_lines} sentences &middot; {len(facts)} facts &middot; mined from {repo['commits']:,} commits at {repo['head'][:10]}{f" &middot; {fb} chapters fell back to raw facts" if fb else ""}</div>
        <div class="sub abs" id="cr-3" style="left:0;right:0;top:560px;text-align:center">written by {e(script.get('model', 'a local LLM'))} &middot; {"narrated by Qwen3-TTS" if voiced else "silent cut (no narration audio)"} &middot; rendered with HyperFrames &middot; all local</div>
        <div class="kicker abs" id="cr-4" style="left:0;right:0;top:680px;text-align:center">repo-saga</div>
      </div>''')
    A(f'tl.from("#cr-1",{{opacity:0,y:24,filter:"blur(8px)",duration:1,ease:"power3.out"}},{cs + .2:.3f});')
    A(f'tl.from("#cr-2",{{opacity:0,duration:.8}},{cs + 1.0:.3f});')
    A(f'tl.from("#cr-3",{{opacity:0,duration:.8}},{cs + 1.5:.3f});')
    A(f'tl.from("#cr-4",{{opacity:0,scale:1.2,duration:1.2,ease:"power2.out"}},{cs + 2.2:.3f});')
    A(f'tl.from("#strip",{{opacity:0,y:30,duration:.8,ease:"power2.out"}},{TITLE - .3:.3f});')
    A(f'tl.to("#strip",{{opacity:0,duration:.6}},{cs:.3f});')
    A(f'tl.to("#root",{{opacity:0,duration:.8}},{total - .8:.3f});')
    audio.append(f'<audio id="bed" class="clip" src="audio/bed.mp3" data-start="0" data-duration="{total:.3f}" data-volume="0.5" data-fade-in="2" data-fade-out="3" data-track-index="10"></audio>')

    page = f'''<!doctype html>
<html lang="en" data-resolution="landscape">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1920, height=1080" />
    <script src="assets/gsap.min.js"></script>
    <style>{CSS}</style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{total:.3f}" data-width="1920" data-height="1080">
      <div id="world" class="clip" data-start="0" data-duration="{total:.3f}" data-track-index="0">
        <div id="bg"></div><div id="dust"></div>
      </div>
      {''.join(body)}
      <div id="hud" class="clip" data-start="0" data-duration="{total:.3f}" data-track-index="2">
        <div id="vignette"></div><div class="lb top"></div><div class="lb bot"></div>
        <div id="strip"><div class="yr l">{yr0}</div><div class="yr r">{yr1}</div>
          <div id="bars">{bars}</div>{ticks}<div id="playhead"></div></div>
      </div>
      {''.join(audio)}
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
      tl.fromTo("#bg", {{ scale: 1.0 }}, {{ scale: 1.12, duration: {total:.2f}, ease: "none" }}, 0);
      tl.fromTo("#dust", {{ backgroundPosition: "0px 0px" }}, {{ backgroundPosition: "-600px -300px", duration: {total:.2f}, ease: "none" }}, 0);
      {chr(10).join("      " + x for x in js).strip()}
      window.__timelines = window.__timelines || {{}};
      window.__timelines["main"] = tl;
      tl.seek(0);
    </script>
  </body>
</html>
'''
    return page, total


def _nice(d: str) -> str:
    x = date.fromisoformat(d[:10])
    return f"{x:%B} {x.day}, {x.year}"


CSS = """
      @font-face { font-family: "Fraunces"; src: url(assets/fonts/fraunces-italic-var.woff2) format("woff2"); font-style: italic; font-weight: 100 900; }
      @font-face { font-family: "Barlow"; src: url(assets/fonts/barlow-500.woff2) format("woff2"); font-weight: 500; }
      @font-face { font-family: "Barlow"; src: url(assets/fonts/barlow-600.woff2) format("woff2"); font-weight: 600; }
      @font-face { font-family: "BarlowC"; src: url(assets/fonts/barlow-condensed-700.woff2) format("woff2"); font-weight: 700; }
      @font-face { font-family: "Mono"; src: url(assets/fonts/jetbrains-mono-var.woff2) format("woff2"); font-weight: 100 800; }
      :root { --ink: #efe6d6; --dim: #9c9183; --amber: #d9a441; --blood: #c0533c; --moss: #7fa27a; --night: #0c0a08; }
      * { margin: 0; padding: 0; box-sizing: border-box; }
      html, body { width: 1920px; height: 1080px; overflow: hidden; background: var(--night); }
      #root { position: relative; width: 1920px; height: 1080px; overflow: hidden; font-family: "Barlow", sans-serif; color: var(--ink); background: var(--night); }
      .clip { position: absolute; inset: 0; }
      .abs { position: absolute; }
      #bg { position: absolute; inset: -60px;
            background: radial-gradient(1200px 760px at 62% 40%, rgba(217,164,65,.13), transparent 70%),
                        radial-gradient(900px 700px at 15% 85%, rgba(192,83,60,.10), transparent 70%),
                        linear-gradient(180deg, #14100c 0%, #0b0907 100%); }
      #dust { position: absolute; inset: 0; opacity: .35;
              background-image: radial-gradient(rgba(239,230,214,.10) 1px, transparent 1.4px), radial-gradient(rgba(239,230,214,.06) 1px, transparent 1.2px);
              background-size: 140px 140px, 67px 67px; background-position: 0 0, 30px 50px; }
      #vignette { position: absolute; inset: 0; pointer-events: none; background: radial-gradient(1300px 820px at 50% 48%, transparent 52%, rgba(0,0,0,.72) 100%); }
      .lb { position: absolute; left: 0; right: 0; height: 64px; background: #000; }
      .lb.top { top: 0; } .lb.bot { bottom: 0; }
      .fr { font-family: "Fraunces"; font-style: italic; font-weight: 400; letter-spacing: -.01em; line-height: 1; color: var(--ink); }
      .kicker { font-family: "Mono"; font-weight: 500; font-size: 24px; letter-spacing: .32em; text-transform: uppercase; color: var(--amber); }
      .sub { font-family: "Barlow"; font-weight: 500; font-size: 34px; color: var(--dim); letter-spacing: .02em; }
      .mono { font-family: "Mono"; font-weight: 400; font-size: 26px; color: var(--dim); letter-spacing: .04em; }
      .year { right: -40px; top: 40px; font-family: "Fraunces"; font-style: italic; font-weight: 300; font-size: 560px; line-height: 1;
              color: rgba(239,230,214,.075); letter-spacing: -.04em; }
      .cardwrap { left: 0; right: 0; top: 330px; text-align: center; }
      .cardwrap .kicker { margin-bottom: 34px; }
      .ctitle { font-size: 150px; margin-bottom: 34px; }
      .head { left: 110px; top: 120px; white-space: nowrap; }
      .head .fr { font-size: 64px; margin-left: 18px; }
      .head .hdate { font-family: "Mono"; font-size: 24px; color: var(--dim); margin-left: 28px; letter-spacing: .06em; }
      .stack { right: 110px; top: 230px; width: 760px; display: flex; flex-direction: column; gap: 22px; }
      .card { background: rgba(20,16,12,.86); border: 1px solid rgba(239,230,214,.16); border-radius: 6px; padding: 24px 28px;
              box-shadow: 0 20px 50px rgba(0,0,0,.45); }
      .card .row { display: flex; align-items: baseline; gap: 20px; font-family: "Mono"; font-size: 22px; color: var(--dim); }
      .card .sha { color: var(--amber); font-weight: 600; font-size: 26px; }
      .card .note { margin-left: auto; color: var(--blood); text-transform: uppercase; letter-spacing: .16em; font-size: 18px; }
      .card .subj { font-family: "Fraunces"; font-style: italic; font-size: 36px; line-height: 1.18; margin: 14px 0 16px; color: var(--ink); }
      .card .who { color: var(--ink); font-family: "Barlow"; font-weight: 600; font-size: 24px; }
      .card .churn { margin-left: auto; } .card em { font-style: normal; }
      .plus { color: var(--moss); } .minus { color: var(--blood); }
      .bar { height: 5px; margin-top: 14px; background: var(--blood); border-radius: 3px; overflow: hidden; opacity: .8; }
      .bar i { display: block; height: 100%; background: var(--moss); }
      .card.compact { padding: 14px 22px; } .card.compact .subj { font-size: 26px; margin: 6px 0 4px; }
      .card.compact .bar, .card.compact .row:last-of-type { display: none; }
      .cap { position: absolute; left: 110px; width: 900px; top: 300px; font-size: 46px; line-height: 1.28; font-weight: 500; opacity: 0;
             text-shadow: 0 2px 18px rgba(0,0,0,.8); }
      .cap .chip { display: block; font-family: "Mono"; font-size: 20px; letter-spacing: .14em; color: var(--amber); margin-bottom: 18px; }
      #strip { position: absolute; left: 110px; right: 110px; bottom: 92px; height: 70px; }
      #bars { position: absolute; inset: 0; display: flex; align-items: flex-end; gap: 1px; opacity: .55; }
      #bars i { flex: 1; background: linear-gradient(180deg, rgba(217,164,65,.9), rgba(217,164,65,.25)); min-width: 1px; }
      .yr { position: absolute; bottom: -34px; font-family: "Mono"; font-size: 18px; color: var(--dim); letter-spacing: .1em; }
      .yr.l { left: 0; } .yr.r { right: 0; }
      .tick { position: absolute; bottom: -12px; width: 9px; height: 9px; margin-left: -4px; border-radius: 50%; background: var(--ink); opacity: .25; }
      #playhead { position: absolute; left: 0; top: -14px; bottom: -6px; width: 2px; background: var(--ink); box-shadow: 0 0 14px rgba(239,230,214,.8); }
"""


def sounds(audio_dir: str, total: float):
    """A low ambient bed and a chapter hit, synthesized with ffmpeg (no samples to license)."""
    os.makedirs(audio_dir, exist_ok=True)
    bed = ("0.20*sin(2*PI*55*t)*(0.55+0.45*sin(2*PI*0.045*t))"
           "+0.13*sin(2*PI*82.41*t)*(0.5+0.5*sin(2*PI*0.071*t+1))"
           "+0.09*sin(2*PI*110*t)*(0.5+0.5*sin(2*PI*0.033*t+2))"
           "+0.05*sin(2*PI*130.81*t)*(0.5+0.5*sin(2*PI*0.052*t+4))"
           "+0.035*sin(2*PI*164.81*t)*(0.5+0.5*sin(2*PI*0.11*t))")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc={bed}:s=44100:d={total + 1:.1f}",
                    "-af", "lowpass=f=700,aecho=0.8:0.7:420|780:0.3|0.2,volume=0.55", "-ac", "2",
                    "-c:a", "libmp3lame", "-b:a", "128k", os.path.join(audio_dir, "bed.mp3")], check=True)
    hit = "0.9*sin(2*PI*(38+46*exp(-5*t))*t)*exp(-1.9*t)+0.25*sin(2*PI*(76+60*exp(-7*t))*t)*exp(-3*t)"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc={hit}:s=44100:d=2.4",
                    "-af", "aecho=0.8:0.6:300:0.25,afade=t=out:st=1.6:d=0.8", "-ac", "2",
                    "-c:a", "libmp3lame", "-b:a", "128k", os.path.join(audio_dir, "hit.mp3")], check=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("outdir")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--quality", default=None, help="e.g. delivery")
    ap.add_argument("-o", "--output", help="mp4 path (default <outdir>/<repo>-saga.mp4)")
    a = ap.parse_args(argv)
    script = json.load(open(os.path.join(a.outdir, "script.json"), encoding="utf-8"))
    timeline = json.load(open(os.path.join(a.outdir, "timeline.json"), encoding="utf-8"))
    tpath = os.path.join(a.outdir, "audio", "timing.json")
    timing = json.load(open(tpath)) if os.path.exists(tpath) else None
    if timing and set(timing) != {c["n"] for c in script["chapters"]}:
        print("warning: narration missing for some chapters; those are silent", file=sys.stderr)
    vid = os.path.join(a.outdir, "video")
    shutil.copytree(os.path.join(TEMPLATE, "assets"), os.path.join(vid, "assets"), dirs_exist_ok=True)
    page, total = build_html(script, timeline, timing)
    open(os.path.join(vid, "index.html"), "w", encoding="utf-8").write(page)
    json.dump({"id": f"saga-{script['repo']}", "name": f"saga-{script['repo']}"}, open(os.path.join(vid, "meta.json"), "w"))
    vaud = os.path.join(vid, "audio")
    os.makedirs(vaud, exist_ok=True)
    if timing:
        for c in script["chapters"]:
            src = os.path.join(a.outdir, "audio", f"ch-{c['n']}.mp3")
            if os.path.exists(src):
                shutil.copy2(src, vaud)
    sounds(vaud, total)
    print(f"{script['repo']}: {total:.1f}s composition, {'voiced' if timing else 'SILENT'} -> {vid}/index.html")
    if a.no_render:
        return
    out = os.path.abspath(a.output or os.path.join(a.outdir, f"{script['repo']}-saga.mp4"))
    cmd = ["npx", "--yes", HYPERFRAMES, "render", "-f", "30", "-o", out] + (["-q", a.quality] if a.quality else [])
    subprocess.run(cmd, cwd=vid, check=True, shell=(os.name == "nt"))
    print(f"rendered -> {out}")


if __name__ == "__main__":
    main()
