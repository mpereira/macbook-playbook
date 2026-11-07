---
name: review
description: 'Start a Codex review loop on existing work — the current diff, a branch, or a plan/design doc — from any session, including one that did not begin with /brief or /build. Use when the user says review this, review the diff, review this branch, start a review loop, or wants a second agent to check work that already exists.'
---

# Review

An entry point into the `converge` loop for work this session did not author:
changes the user wrote, a branch from an earlier session, or a document that
never went through `/brief`. You orient, then run the loop.

Order: **target → orient → stance → loop → report**.

## 1. Pick the target

From the arguments:

- A `.md` path → plan mode on that file.
- A git ref → code mode with that ref as `--base`.
- Nothing → code mode against the repo's main branch. Find it rather than
  guessing: the repo's `CLAUDE.md`/`AGENTS.md` often names it, else
  `git symbolic-ref refs/remotes/origin/HEAD`. Confirm there is actually
  something to review first (`git diff <base>...HEAD --stat`,
  `git status --short`); an empty diff means asking the user what they meant,
  not opening a round on nothing.

Reviewing a PR that is not checked out is out of scope — ask the user to
check out the branch, or point them at repo tooling built for PRs.

## 2. Orient before the first round

You will be the responding author in the loop, and you cannot dispute a
finding about work you do not understand. Before round 1:

- Read the diff (or the document) end to end, plus enough surrounding code to
  know what the change is for.
- Read the repo's `AGENTS.md` / `CLAUDE.md` and the area guides for the
  touched code — findings often turn on repo conventions.
- If the *intent* is not recoverable from the work itself (no plan, no commit
  messages, cryptic diff), ask the user one question about what it is meant to
  do. Skip this when intent is obvious.

## 3. Stance: fix or report

Two ways to run the loop; pick from the user's phrasing, ask with
`AskUserQuestion` only if genuinely unclear:

- **Fix** ("review and fix", "get this ready") — you adopt authorship: fix
  accepted findings between rounds, dispute wrong ones, exactly like
  `/build` step 6. Run the repo's validation commands before round 1 — never
  spend a review round on findings a type checker reports in seconds — and
  re-run them after each round of fixes.
- **Report** ("what does Codex think", "review this", the default when the
  work is the user's own uncommitted changes and they did not ask for fixes)
  — nothing gets edited. Run round 1, verify each finding yourself against
  the code, then run round 2 with a reply that DISPUTES the ones you can
  refute. What survives goes to the user as the deliverable. Two rounds is
  the cap in this stance; without fixes, further rounds add nothing.

The work may be uncommitted and not yours. In fix stance, never revert or
rewrite beyond what a finding requires, and say in the report exactly which
files you touched.

## 4. Run the loop

Follow the `converge` skill; it owns the round mechanics, stopping rules, and
the disagreement rule. One deviation: use a fresh out-dir per loop instead of
the shared one — rounds 2+ resume the *most recent* Codex session, and stale
`round-N.json` files from an earlier loop in the same repo must not be mixed
into this one:

```bash
OUT="$(git rev-parse --show-toplevel)/.claude/converge/review-$(date +%Y%m%d-%H%M%S)" && mkdir -p "$OUT"
~/.claude/skills/converge/bin/codex-review \
  --mode code --base <base> --round 1 --out "$OUT" --workdir "$PWD"
```

For a document target, swap in `--mode plan --input <file>`. Always start at
round 1: this session has no Codex session to resume.

Checkpointing follows converge: plan mode pauses after each round for the
user; code mode runs its rounds and reports, interrupting early only for a
contested finding or a stalemate.

## 5. Report

- The verdict and the standing findings, each with file:line and whether you
  fixed, disputed, or are handing it to the user (contested findings get both
  arguments, stated fairly).
- In fix stance: which files you changed, and validation output — actual
  results, plainly.
- What you deliberately left unaddressed (`minor`/`nit`).
- Where the round files live, for the user's own inspection.

Do not commit, push, or publish the review to a PR unless asked;
`converge`'s `publish-review` exists for that, dry-run first.
