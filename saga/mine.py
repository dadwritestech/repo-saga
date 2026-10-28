"""History miner: git log -> timeline.json of dramatic, citeable "chapters".

Every chapter carries facts. A fact is one sentence plus the commit (or tag) it was
read from. The script writer may only say things it can pin to a fact id, so this
file is the single source of truth for what the documentary is allowed to claim.

stdlib only. Usage: python -m saga.mine <repo> [-o timeline.json] [--chapters 8]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

RS, US = "\x1e", "\x1f"
NOISE = re.compile(r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|Cargo\.lock|go\.sum|poetry\.lock|"
                   r"uv\.lock|composer\.lock|Gemfile\.lock)$|(^|/)(vendor|node_modules|dist|third_party)/|\.min\.(js|css)$")
FIXY = re.compile(r"\b(fix(es|ed|ing)?|typo|oops|again|really|actually|wip|hotfix|argh|ugh|please|finally|"
                  r"broke|broken|forgot|missing|try|attempt)\b|^\W*$|^.{0,4}$", re.I)
SEMVER = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-.]?(.*))?$")


# ---------------------------------------------------------------------------- git
def git(repo: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", repo, "-c", "i18n.logOutputEncoding=UTF-8", *args],
                       capture_output=True)
    if r.returncode:
        raise SystemExit(f"git {' '.join(args[:2])} failed: {r.stderr.decode('utf-8', 'replace')[:400]}")
    return r.stdout.decode("utf-8", "replace")


def read_commits(repo: str) -> list[dict]:
    fmt = RS + US.join(["%H", "%aN", "%aE", "%aI", "%cI", "%P", "%s"])
    out = git(repo, "log", "--reverse", "--numstat", "-M", f"--format={fmt}", "HEAD")
    commits = []
    for block in out.split(RS)[1:]:
        head, _, body = block.partition("\n")
        sha, author, email, adate, cdate, parents, subject = head.split(US, 6)
        add = dele = files = renames = 0
        for line in body.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            a, d, path = parts
            files += 1
            if "=>" in path:
                renames += 1
            if a == "-" or NOISE.search(path.replace("{", "").replace("}", "")):
                continue
            add += int(a); dele += int(d)
        commits.append({
            "sha": sha, "short": sha[:7], "author": author.strip() or "unknown", "email": email.strip().lower(),
            "adate": datetime.fromisoformat(adate), "cdate": datetime.fromisoformat(cdate),
            "parents": parents.split(), "subject": subject.strip(),
            "add": add, "del": dele, "files": files, "renames": renames,
        })
    if not commits:
        raise SystemExit("no commits on HEAD")
    merge_identities(commits)
    if git(repo, "rev-parse", "--is-shallow-repository").strip() == "true":
        print("warning: shallow clone; the saga starts at the shallow boundary, not the real founding",
              file=sys.stderr)
    return commits


def merge_identities(commits: list[dict]) -> None:
    """One person, many spellings: 'visionmedia', 'Tj Holowaychuk' and 'TJ Holowaychuk' share an
    email. Without this the miner announces that the founder left years before they did."""
    parent: dict[str, str] = {}

    def find(k):
        while parent.setdefault(k, k) != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for c in commits:
        a = "n:" + c["author"].lower()
        e = c["email"]
        if "@" in e and not re.search(r"(example\.com|localhost|\(none\)|^root@|^none@)", e):
            parent[find(a)] = find("e:" + e)
        else:
            find(a)
    groups: dict[str, Counter] = defaultdict(Counter)
    for c in commits:
        groups[find("n:" + c["author"].lower())][c["author"]] += 1
    display = {}
    for root, names in groups.items():
        top, k = names.most_common(1)[0]
        spaced = [(m, nm) for nm, m in names.items() if " " in nm.strip()]
        if " " not in top and spaced and max(spaced)[0] >= 0.2 * k:
            top = max(spaced)[1]                    # prefer "TJ Holowaychuk" over "visionmedia"
        same = [nm for nm in names if nm.lower() == top.lower()]
        display[root] = max(same, key=lambda nm: sum(ch.isupper() for ch in nm))   # "TJ" over "Tj"
    for c in commits:
        c["author"] = display[find("n:" + c["author"].lower())]
        del c["email"]                              # never leaves this process


def read_tags(repo: str, by_sha: dict) -> list[dict]:
    try:
        out = git(repo, "show-ref", "--tags", "-d")
    except SystemExit:
        return []                                   # no tags at all
    peeled = {}
    for line in out.splitlines():
        sha, ref = line.split(" ", 1)
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            peeled[name[:-3]] = sha
        else:
            peeled.setdefault(name, sha)
    tags = []
    for name, sha in peeled.items():
        c = by_sha.get(sha)
        if c:                                       # only tags on HEAD's history
            tags.append({"name": name, "sha": sha, "date": c["cdate"], "commit": c})
    return sorted(tags, key=lambda t: t["date"])


def reflog_forced(repo: str) -> list[str]:
    """Forced updates are only visible in a local reflog (a clone of someone else's repo has none)."""
    try:
        out = git(repo, "reflog", "show", "--all", "--format=%H%x1f%gs%x1f%gd")
    except SystemExit:
        return []
    return [l.split(US)[0] for l in out.splitlines() if "forced-update" in l]


# ------------------------------------------------------------------- formatting
def day(d: datetime) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def clock(d: datetime) -> str:
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d} {'a.m.' if d.hour < 12 else 'p.m.'}"


def n(x: int) -> str:
    return f"{x:,}"


def span(a: datetime, b: datetime) -> str:
    days = (b - a).days
    if days >= 730:
        return f"{days / 365.25:.1f} years"
    if days >= 60:
        return f"{round(days / 30.44)} months"
    if days >= 2:
        return f"{days} days"
    hours = int((b - a).total_seconds() // 3600)
    return f"{hours} hours" if hours >= 2 else f"{int((b - a).total_seconds() // 60)} minutes"


def quote(s: str, k: int = 90) -> str:
    if len(s) <= k:
        return s
    cut = s[:k - 1].rsplit(" ", 1)[0] if " " in s[:k - 1] else s[:k - 1]
    return cut.rstrip(" ,;:-") + "…"


# ---------------------------------------------------------------------- miner
class Saga:
    def __init__(self, repo: str):
        self.repo = os.path.abspath(repo)
        self.commits = read_commits(repo)
        self.by_sha = {c["sha"]: c for c in self.commits}
        self.tags = read_tags(repo, self.by_sha)
        self.facts: dict[str, dict] = {}
        self.events: list[dict] = []

    def fact(self, text: str, c: dict | None = None, **extra) -> str:
        fid = f"F{len(self.facts) + 1}"
        self.facts[fid] = {"id": fid, "text": text, "sha": c["sha"] if c else None,
                           "date": (c["cdate"] if c else None) and c["cdate"].isoformat(), **extra}
        return fid

    def event(self, kind: str, weight: float, c: dict, facts: list[str], cards: list[dict], **extra):
        self.events.append({"kind": kind, "weight": weight, "date": c["adate"], "facts": facts,
                            "cards": cards, **extra})

    @staticmethod
    def card(c: dict, note: str = "") -> dict:
        return {"sha": c["short"], "author": c["author"], "date": c["adate"].strftime("%Y-%m-%d %H:%M"),
                "subject": quote(c["subject"], 80), "add": c["add"], "del": c["del"], "note": note}

    # -------- detectors. Each one adds events; weight = how much drama it carries.
    def genesis(self):
        c = self.commits[0]
        f = [self.fact(f"The first commit was made on {day(c['adate'])} by {c['author']}, titled \"{quote(c['subject'])}\".", c)]
        early = [x for x in self.commits if x["cdate"] <= c["cdate"] + timedelta(days=30)]
        if len(early) > 1:
            f.append(self.fact(f"In its first 30 days the repository got {n(len(early))} commits.", early[-1]))
        self.event("genesis", 100, c, f, [self.card(c, "the first commit")])

    def founder_exit(self):
        founder = self.commits[0]["author"]
        theirs = [c for c in self.commits if c["author"] == founder]
        last, head = theirs[-1], self.commits[-1]
        if len(theirs) < 10 or (head["cdate"] - last["cdate"]).days < 365:
            return
        after = sum(1 for c in self.commits if c["cdate"] > last["cdate"])
        f = [self.fact(f"Founder {founder} made {n(len(theirs))} commits; the last one was on {day(last['adate'])}, \"{quote(last['subject'])}\".", last),
             self.fact(f"After that, {n(after)} more commits were made by others.", head)]
        self.event("founder_exit", 70, last, f, [self.card(last, f"{founder}'s last commit")])

    def arrivals(self):
        counts = Counter(c["author"] for c in self.commits)
        founder = self.commits[0]["author"]
        total = len(self.commits)
        for i, (who, k) in enumerate(counts.most_common(4)):
            if who == founder or k < max(5, total * 0.05):
                continue
            first = next(c for c in self.commits if c["author"] == who)
            if first is self.commits[0]:
                continue
            f = [self.fact(f"{who} made a first commit on {day(first['adate'])}, \"{quote(first['subject'])}\".", first),
                 self.fact(f"{who} went on to make {n(k)} commits, {100 * k / total:.0f}% of all commits.", first)]
            self.event("arrival", 35 + 30 * k / total, first, f, [self.card(first, f"{who} arrives")], who=who)

    def releases(self):
        if not self.tags:
            return
        t0 = self.tags[0]
        f = [self.fact(f"The first tag, {t0['name']}, was cut on {day(t0['date'])}, {span(self.commits[0]['cdate'], t0['date'])} after the first commit.", t0["commit"])]
        self.event("first_release", 55, t0["commit"], f, [self.card(t0["commit"], f"tag {t0['name']}")])
        majors: dict[int, dict] = {}
        for t in self.tags:
            m = SEMVER.match(t["name"])
            if not m:
                continue
            major, pre = int(m.group(1)), (m.group(4) or "")
            slot = majors.setdefault(major, {"pre": None, "final": None})
            if pre and not slot["pre"]:
                slot["pre"] = t
            if not pre and not slot["final"]:
                slot["final"] = t
        prev = None
        for major in sorted(majors):
            s = majors[major]
            if major >= 1 and s["final"] and s["final"] is not t0:
                t = s["final"]
                f = [self.fact(f"Version {t['name']} was tagged on {day(t['date'])}.", t["commit"])]
                w = 40
                if prev:
                    f.append(self.fact(f"That was {span(prev['date'], t['date'])} after {prev['name']}.", t["commit"]))
                if s["pre"] and s["pre"]["date"] < t["date"]:
                    wait = t["date"] - s["pre"]["date"]
                    f.append(self.fact(f"The first {major}.x pre-release, {s['pre']['name']}, was tagged on {day(s['pre']['date'])}: {span(s['pre']['date'], t['date'])} before the final release.", s["pre"]["commit"]))
                    w += min(50, wait.days / 30)        # a decade-long beta is the story
                self.event("major_release", w, t["commit"], f, [self.card(t["commit"], f"tag {t['name']}")]
                           + ([self.card(s["pre"]["commit"], f"tag {s['pre']['name']}")] if s["pre"] else []))
                prev = t
        f = [self.fact(f"There are {n(len(self.tags))} tags on the main line of history.", self.tags[-1]["commit"])]
        self.stats_facts.extend(f)

    def refactors(self):
        cands = [c for c in self.commits[1:] if len(c["parents"]) == 1]
        if not cands:
            return
        churn = sorted(c["add"] + c["del"] for c in cands)
        p99 = churn[int(len(churn) * 0.99) - 1] if len(churn) > 20 else churn[-1]
        big = sorted((c for c in cands if c["add"] + c["del"] >= max(p99, 400) or c["renames"] >= 15),
                     key=lambda c: c["add"] + c["del"] + 40 * c["renames"], reverse=True)[:2]
        for c in big:
            bits = f"{n(c['files'])} files, +{n(c['add'])} and -{n(c['del'])} lines"
            f = [self.fact(f"On {day(c['adate'])}, {c['author']} landed \"{quote(c['subject'])}\": {bits}.", c)]
            if c["renames"] >= 5:
                f.append(self.fact(f"That commit moved or renamed {n(c['renames'])} files.", c))
            kind = "purge" if c["del"] > 3 * c["add"] else "refactor"
            self.event(kind, 45 + min(30, (c["add"] + c["del"]) / 500), c, f, [self.card(c, "the big one")])
        purge = max(cands, key=lambda c: c["del"] - c["add"])
        if purge["del"] >= 500 and purge["del"] > 3 * purge["add"] and purge not in big:
            f = [self.fact(f"On {day(purge['adate'])}, {purge['author']} deleted {n(purge['del'])} lines in one commit, \"{quote(purge['subject'])}\".", purge)]
            self.event("purge", 50, purge, f, [self.card(purge, "the purge")])

    def reverts(self):
        revs = [c for c in self.commits if c["subject"].lower().startswith("revert")]
        if not revs:
            return
        bodies = {}
        for line in git(self.repo, "log", "--format=%H%x1f%b%x1e", "--grep=This reverts commit", "HEAD").split(RS):
            if US in line:
                h, b = line.strip().split(US, 1)
                m = re.search(r"This reverts commit ([0-9a-f]{7,40})", b)
                if m:
                    bodies[h] = m.group(1)
        best = None
        for r in revs:
            orig = next((c for s, c in self.by_sha.items() if bodies.get(r["sha"]) and s.startswith(bodies[r["sha"]])), None)
            if orig and r["cdate"] > orig["cdate"]:
                score = ((r["author"] != orig["author"]), (r["cdate"] - orig["cdate"]))
                if best is None or score > best[2]:
                    best = (r, orig, score)
        f = [self.fact(f"The history contains {n(len(revs))} revert commits.", revs[-1])]
        if best:
            r, o, _ = best
            f.append(self.fact(f"\"{quote(o['subject'])}\" by {o['author']} lasted {span(o['cdate'], r['cdate'])} before {r['author']} reverted it on {day(r['adate'])}.", r))
            cards = [self.card(o, "the change"), self.card(r, "the revert")]
            self.event("revert", 40 + min(25, len(revs)), r, f, cards)
        else:
            r = revs[0]
            f.append(self.fact(f"The first revert came on {day(r['adate'])}: \"{quote(r['subject'])}\".", r))
            self.event("revert", 30 + min(25, len(revs)), r, f, [self.card(r, "the revert")])

    def fix_streaks(self):
        best, run = [], []
        for c in self.commits:
            if FIXY.search(c["subject"]) and (not run or (c["cdate"] - run[-1]["cdate"]).total_seconds() < 6 * 3600):
                run.append(c)
            else:
                if len(run) > len(best):
                    best = run
                run = [c] if FIXY.search(c["subject"]) else []
        if len(run) > len(best):
            best = run
        if len(best) < 3:
            return
        a, b = best[0], best[-1]
        f = [self.fact(f"Between {clock(a['adate'])} on {day(a['adate'])} and {clock(b['adate'])}, {len(best)} fix-style commits landed in a row, over {span(a['cdate'], b['cdate'])}.", b)]
        shown = best[:5]
        f.append(self.fact("Their messages: " + "; ".join(f"\"{quote(c['subject'], 60)}\"" for c in shown) + ".", shown[-1]))
        shout = [c for c in best if re.search(r"\bFIX", c["subject"]) or "!" in c["subject"]]
        self.event("fix_streak", 40 + 5 * len(best) + 10 * bool(shout), a, f, [self.card(c) for c in shown])

    def late_nights(self):
        night = [c for c in self.commits if 0 <= c["adate"].hour < 5]
        if len(night) < 3:
            return
        deepest = max(night, key=lambda c: (c["adate"].hour * 60 + c["adate"].minute, c["add"] + c["del"]))
        owl, k = Counter(c["author"] for c in night).most_common(1)[0]
        f = [self.fact(f"{n(len(night))} of {n(len(self.commits))} commits ({100 * len(night) / len(self.commits):.0f}%) were authored between midnight and 5 a.m., by the committer's own clock.", night[-1]),
             self.fact(f"At {clock(deepest['adate'])} on {day(deepest['adate'])}, {deepest['author']} committed \"{quote(deepest['subject'])}\".", deepest),
             self.fact(f"The most nocturnal author was {owl}, with {n(k)} commits before 5 a.m.", night[-1])]
        self.event("late_night", 35 + min(25, 100 * len(night) / len(self.commits)), deepest, f, [self.card(deepest, "before dawn")])

    def silences(self):
        gaps = []
        for a, b in zip(self.commits, self.commits[1:]):
            gaps.append(((b["cdate"] - a["cdate"]).total_seconds(), a, b))
        if not gaps:
            return
        med = statistics.median(g for g, *_ in gaps) or 1
        floor = max(21 * 86400, 40 * med)
        for g, a, b in sorted((x for x in gaps if x[0] >= floor), key=lambda x: x[0], reverse=True)[:2]:
            f = [self.fact(f"After \"{quote(a['subject'])}\" on {day(a['adate'])}, nothing was committed for {span(a['cdate'], b['cdate'])}.", a),
                 self.fact(f"The silence ended on {day(b['adate'])} when {b['author']} committed \"{quote(b['subject'])}\".", b)]
            self.event("silence", 40 + min(40, g / 86400 / 15), b, f, [self.card(a, "before"), self.card(b, "after")])

    def crunch(self):
        weeks: dict[tuple, list] = defaultdict(list)
        for c in self.commits[1:]:
            weeks[c["cdate"].isocalendar()[:2]].append(c)
        if len(weeks) < 6:
            return
        size = {k: len(v) for k, v in weeks.items()}
        med = statistics.median(size.values())
        top = max(weeks, key=lambda k: size[k])
        if size[top] < max(15, 5 * med):
            return
        cs = weeks[top]
        who, k = Counter(c["author"] for c in cs).most_common(1)[0]
        f = [self.fact(f"The busiest week began {day(cs[0]['adate'])}: {n(len(cs))} commits, against a typical week of {med:g}.", cs[-1]),
             self.fact(f"{who} made {n(k)} of them.", cs[-1])]
        self.event("crunch", 40 + min(30, size[top] / med), cs[0], f, [self.card(c) for c in cs[:3]])

    def rewrites(self):
        forced = reflog_forced(self.repo)
        if forced:
            c = self.by_sha.get(forced[0]) or self.commits[-1]
            f = [self.fact(f"The local reflog records {n(len(forced))} forced updates, where history was rewritten and force-pushed.", c)]
            self.event("force_push", 60, c, f, [self.card(c, "after a forced update")])
        rewritten = [c for c in self.commits if (c["cdate"] - c["adate"]).days >= 30]
        if len(rewritten) >= max(5, len(self.commits) * 0.02):
            self.stats_facts.append(self.fact(
                f"{n(len(rewritten))} commits were committed 30 or more days after they were written: rebased, cherry-picked or amended.", rewritten[-1]))

    def present(self):
        c, first = self.commits[-1], self.commits[0]
        authors = len({x["author"] for x in self.commits})
        add, dele = sum(x["add"] for x in self.commits), sum(x["del"] for x in self.commits)
        f = [self.fact(f"Today the history holds {n(len(self.commits))} commits by {n(authors)} authors, over {span(first['cdate'], c['cdate'])}.", c),
             self.fact(f"Across all of them, {n(add)} lines were added and {n(dele)} deleted, not counting lockfiles and vendored code.", c),
             self.fact(f"The latest commit, on {day(c['adate'])}, is \"{quote(c['subject'])}\" by {c['author']}.", c)]
        self.event("present", 100, c, f + self.stats_facts, [self.card(c, "today")])

    # ---------------------------------------------------------------- assemble
    TITLES = {"genesis": "Genesis", "founder_exit": "The Founder Walks Away", "arrival": "A New Hand",
              "first_release": "The First Release", "major_release": "The Long Road to {tag}",
              "refactor": "The Great Rewrite", "purge": "The Purge", "revert": "The Betrayal",
              "fix_streak": "Fix, Fix, FIX", "late_night": "After Midnight", "silence": "The Silence",
              "crunch": "Crunch Week", "force_push": "History Rewritten", "present": "Present Day"}

    def build(self, max_chapters: int = 10) -> dict:
        self.stats_facts: list[str] = []
        for det in (self.genesis, self.founder_exit, self.arrivals, self.releases, self.refactors,
                    self.reverts, self.fix_streaks, self.late_nights, self.silences, self.crunch,
                    self.rewrites, self.present):
            det()
        fixed = [e for e in self.events if e["kind"] in ("genesis", "present")]
        middle = sorted((e for e in self.events if e not in fixed), key=lambda e: e["weight"], reverse=True)
        picked, per_kind = [], Counter()
        for e in middle:
            if len(picked) >= max_chapters - 2:
                break
            if per_kind[e["kind"]] >= 2:
                continue
            picked.append(e); per_kind[e["kind"]] += 1
        chosen = sorted(fixed[:1] + picked, key=lambda e: e["date"].astimezone(timezone.utc)) + fixed[1:]
        for e in chosen:                            # what else was going on that year
            if e["kind"] in ("present",):
                continue
            yr = e["date"].year
            cs = [c for c in self.commits if c["adate"].year == yr]
            e["facts"].append(self.fact(f"In {yr} as a whole, {n(len(cs))} commits were made by {n(len({c['author'] for c in cs}))} authors.", cs[-1]))
        used = {fid for e in chosen for fid in e["facts"]}
        chapters = []
        roman = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
        for i, e in enumerate(chosen):
            title = self.TITLES[e["kind"]]
            if "{tag}" in title:
                title = title.format(tag=e["cards"][0]["note"].removeprefix("tag "))
            if e["kind"] == "arrival":
                title = f"Enter {e['who']}"
            if any(ch["title"] == title for ch in chapters):
                title += ", Part II"
            chapters.append({"n": roman[i], "kind": e["kind"], "title": title, "date": e["date"].date().isoformat(),
                             "facts": e["facts"], "cards": e["cards"]})
        first, last = self.commits[0], self.commits[-1]
        months = Counter(c["cdate"].strftime("%Y-%m") for c in self.commits)
        cur, end, activity = first["cdate"].replace(day=1), last["cdate"], []
        while cur <= end:
            activity.append({"month": cur.strftime("%Y-%m"), "commits": months.get(cur.strftime("%Y-%m"), 0)})
            cur = (cur + timedelta(days=32)).replace(day=1)
        return {
            "schema": "repo-saga/timeline@1",
            "repo": {"name": os.path.basename(self.repo.rstrip("\\/")), "head": last["sha"],
                     "first": first["cdate"].isoformat(), "last": last["cdate"].isoformat(),
                     "commits": len(self.commits), "authors": len({c["author"] for c in self.commits}),
                     "tags": len(self.tags)},
            "chapters": chapters,
            "facts": {k: v for k, v in self.facts.items() if k in used},
            "unused_events": [{"kind": e["kind"], "weight": round(e["weight"], 1), "date": e["date"].date().isoformat()}
                              for e in self.events if e not in chosen],
            "activity": activity,
        }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo")
    ap.add_argument("-o", "--out", default="timeline.json")
    ap.add_argument("--chapters", type=int, default=10)
    a = ap.parse_args(argv)
    t = Saga(a.repo).build(a.chapters)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(t, fh, indent=2, ensure_ascii=False)
    print(f"{t['repo']['name']}: {t['repo']['commits']} commits, {len(t['chapters'])} chapters, {len(t['facts'])} facts -> {a.out}")
    for c in t["chapters"]:
        print(f"  {c['n']:>4}. {c['date']}  {c['title']}")


if __name__ == "__main__":
    main()
