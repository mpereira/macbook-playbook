---
name: build
description: 'Implement a feature and loop with Codex until the code converges. Resolves open questions first, implements, runs the repo validation commands, then reviews the diff with a second agent until no blocking findings remain. Use when the user says build, build it, implement this, ship this, or hands over a plan produced by /brief.'
---

# Build

Phase two. Implement, then let a second agent try to break it before the user
ever sees it.

Order: **plan → questions → implement → validate → loop**.

## 1. Find the plan

If the user named one, use it. Otherwise look for a plan from `/brief`:

```bash
ls -t "$(git rev-parse --show-toplevel)/.claude/plans/"*.md 2>/dev/null | head
```

If a plan exists and plausibly matches the request, say which one you are using
and continue. If several match, ask which. If none exists, treat the user's
message as the spec — but if the work is large or the approach is genuinely
open, say so and suggest `/brief` first rather than guessing at a design.

A plan's **Non-goals** section is binding. Do not quietly expand past it; if
something outside it turns out to be necessary, say so and ask.

## 2. Ask only what is still open

Far fewer questions than `/brief` — the plan should have settled the design.
Use `AskUserQuestion` for what it genuinely left open, and nothing else.
Re-asking a question the plan already answers means you did not read it.

Working with no plan? Then ask properly: scope edges, approach forks, done
criteria. Same rules as `/brief` step 2.

## 3. Read the repo's rules before writing code

Read `AGENTS.md` / `CLAUDE.md` and the area guides for the code you are about
to touch. Repos carry non-negotiables that are expensive to discover late —
which checkout you are allowed to edit, which files are generated and must
never be hand-edited, which conventions replace your defaults.

Match the surrounding code. Its naming, its comment density, its idioms.

## 4. Implement

Work through the plan's steps in order. Where reality contradicts the plan, the
plan loses — but say so in the final report rather than silently diverging.

Do not commit unless the user asks. The loop below may change the diff several
times, and committing mid-loop just creates history to clean up.

## 5. Validate before you spend a review round

Run the repo's own checks first — formatter, type checker, scoped tests. The
plan's **Verification** section should name them; otherwise the agent docs do.

This is the cheap gate. A Codex round is slow and costs real tokens, and
findings like "this does not typecheck" are ones your own tooling finds in
seconds. Never open a review round on code that fails local validation.

## 6. Loop with the reviewer

Follow the `converge` skill in code mode:

```bash
OUT="$(git rev-parse --show-toplevel)/.claude/converge" && mkdir -p "$OUT"
~/.claude/skills/converge/bin/codex-review \
  --mode code --base dev --round 1 \
  --out "$OUT" --workdir "$PWD"
```

Use the repo's real base branch. Codex reads the diff and the surrounding files
itself under a read-only sandbox — it cannot edit your work.

Code mode **runs all rounds without checkpointing**, then reports. Interrupt
early only for a contested finding or a stalemate.

Between rounds: fix what you accept, write `reply-N.md` marking each finding
`FIXED` or `DISPUTED`, re-run validation, then run the next round. Re-running
validation matters — a fix made to satisfy Codex can break a test, and finding
that out in the next review round wastes the round.

`converge` owns the stopping rules. The one worth repeating: **do not make a
change you believe is wrong just to end the loop.** Dispute it, and if Codex
upholds it and you are still unconvinced, hand it to the user with both
arguments.

## 7. Report

- What was built, against the plan
- Where you diverged from the plan and why
- Validation output — actual results. If something fails, say so plainly and
  show it. Never report done on a red test.
- Findings fixed across rounds, and anything contested and still open
- What you deliberately left as `minor`/`nit`

Then stop. Committing, pushing, and opening a PR are the user's call.
