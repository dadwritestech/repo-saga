# Competition check (2026-10-28, `gh search repos`, sorted by stars)

Queries: "git history documentary", "git history video", "git log story narrated",
"repository history narrative LLM", "gource", "git history podcast",
"commit history story generator", "repo saga", "git story llm", "codebase documentary",
"github repo story video", "git wrapped", "commit history narration tts",
"git history ai storyteller", "repo biography", "git-story", "git-sim",
"repo history video llm", "git history summary llm", "gitstory", "codebase story ai".

| Repo | Stars | What it is | Overlap |
|---|---|---|---|
| acaudwell/Gource | 13.2k | Animated tree visualization of VCS history | Visual only. No story, no narration, no facts. |
| unhappychoice/gitlogue | 5.2k | "Cinematic" terminal replay of commits as typed diffs | Replays code. Doesn't tell a story. |
| initialcommit-com/git-sim | 4.7k | Animates git *commands* (merge, rebase) | Teaching tool. Not about history. |
| gabriel-pineda/git-wrapped-api, ishandeveloper/github-wrapped | 1.3k / 411 | "Spotify Wrapped" for a user's yearly GitHub stats | Stats cards per person, not per repo. |
| initialcommit-com/git-story | 276 | MP4 animation of the commit graph | Draws the graph. No narration, no meaning. |
| utensils/Envisaged | 161 | Gource + FFmpeg wrapper | Same as Gource. |
| **teee32/repo-saga** | **16** | **"Civilization history" of a repo: eras + events from heuristics, output as Markdown/SVG poster/JSON; no LLM** | **Closest. Same name and premise, but a static poster with no narration, no video and no LLM. It is also small.** |
| rendleyhq/github-wrapped-2024 | 17 | Yearly video summary of one user | Per user, template video. |

## Verdict
Nobody makes a **narrated documentary video** of a repo's history in which every line of
narration cites a commit. The pieces exist separately: visualizers (Gource, git-story) and
a heuristic chronicle (teee32/repo-saga). The gap is the combination: narration + video +
factual grounding + fully local.

**Name collision:** `repo-saga` is taken on GitHub (teee32, 16★; its README says `npx repo-saga`, but the npm name was unpublished as of 2026-10-28).
Rename before launch. Options (npm names free on 2026-10-28): `gitsaga`, `commitumentary`, `the-repo-documentary`, `saga.md`.
