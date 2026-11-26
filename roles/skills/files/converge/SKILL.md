---
name: converge
description: 'Run an automated review loop between Claude and Codex until they converge on a plan or a code change, instead of the human copy-pasting between two agents. Use when the user says converge, review loop, run it past Codex, get Codex to review this, iterate with the reviewer, or asks for a second agent to check a plan or a diff before it ships.'
---

# Converge

A two-agent loop. You author, Codex reviews, you respond, repeat until the
blocking findings are gone or the disagreement needs a human. Replaces the
manual cycle of pasting output into Codex and pasting findings back.

Invoke as `/converge plan <file>` or `/converge code [base-branch]`.
The base branch defaults to the repo's main branch: the one its
`AGENTS.md` names, else `git symbolic-ref refs/remotes/origin/HEAD`.

## Setup

Everything runs through two scripts in this skill directory:

```bash
~/.claude/skills/converge/bin/codex-review --help
```

The Codex CLI is bundled inside `ChatGPT.app` and is usually not on `PATH`; the
wrapper finds it either way. Reviews run with `--sandbox read-only`, so Codex
can read the repo and run `git diff` but cannot edit anything. Authoring is
yours alone.

## The loop

Rounds are written to `.claude/converge/` at the repo root — gitignored, and
next to the plans `/brief` writes. Set it once per session:

```bash
OUT="$(git rev-parse --show-toplevel)/.claude/converge" && mkdir -p "$OUT"
```

There is no `$SCRATCHPAD` environment variable; do not reference one.

**Round 1**

```bash
~/.claude/skills/converge/bin/codex-review \
  --mode plan --input PLAN.md --round 1 --out "$OUT" --workdir "$PWD"
```

For code, swap `--mode plan --input PLAN.md` for `--mode code --base origin/main`. The
script prints the path to `round-N.json`; read it.

**Rounds 2+**

Write your response to `$OUT/reply-N.md` first — one section per finding,
under a heading that carries the finding id and either `FIXED` (what you
changed) or `DISPUTED` (why the finding is wrong), such as
`## stale-cursor-on-retry: FIXED`. Then:

```bash
~/.claude/skills/converge/bin/codex-review \
  --mode code --base origin/main --round 2 --out "$OUT" --reply "$OUT/reply-1.md"
```

Rounds 2+ resume the same Codex session, so the reviewer keeps its own context
and can verify that a fix landed rather than taking your word for it. Do not
pass `--workdir` on resume rounds; the session already has it.

## Stopping

Stop and report when any of these is true:

1. **Converged** — `verdict` is `approve`, or no finding has severity
   `blocker` or `major`. This is the success case.
2. **Stalemate** — the set of standing blocker/major finding ids is identical
   to the previous round's, with nothing withdrawn and nothing fixed. Two
   rounds saying the same thing means more rounds will too.
3. **Cap** — four rounds. Report where it stands.
4. **Contested twice** — see below.

Track finding ids across rounds; that is what makes 2 detectable. `status` is
`new`, `upheld`, or `withdrawn`, and ids are stable by design.

## Disagreement — the rule that matters

**Never change the work solely to make the loop terminate.** If you think a
finding is wrong, dispute it. If Codex upholds it and you are still not
convinced after considering its answer, that finding is *contested*: stop
looping on it, keep working the other findings, and surface it to the user with
both arguments stated fairly and your recommendation.

The failure mode this prevents: the only exit from the loop is Codex approving,
so the cheapest path is to capitulate to a finding you believe is incorrect.
That produces convergence worse than either agent alone, and it is invisible in
the output. A contested finding is a successful outcome of the loop, not a
failure of it.

Codex is told to withdraw findings your counter-argument defeats, and it does.
Do not assume a finding is right because it was repeated.

## Checkpointing

- **plan mode** — stop after each round and show the user the findings and your
  intended response before continuing. Plan-level mistakes compound into
  everything built on them, so a human check per round is cheap insurance.
- **code mode** — run all rounds, then report the transcript. Interrupt early
  only for a contested finding or a stalemate.

## Reporting

Per round, one compact block:

```
Round 2 — revise (2 standing, 1 withdrawn)
  🛑 stale-cursor-on-retry [upheld]  services/reddit/fetch.py:88
  🔹 missing-tz-normalisation [new]  services/reddit/window.py:31
  ✅ unbounded-retry-loop [withdrawn]
```

At the end: what converged, what is contested, and what you deliberately left
as a `minor`/`nit` without fixing.

## Publishing to a GitHub PR

Optional, for when the work is already on a PR and the debate should live in a
durable, line-anchored place instead of a scratch file.

```bash
~/.claude/skills/converge/bin/publish-review \
  --pr 123 --round-file "$OUT/round-2.json" \
  --disputes "$OUT/disputes.md" --dry-run
```

Findings with a file and line become inline comments; the rest go in the body.
Contested findings get their own section so the user can rule on them with the
code in front of them.

The review posts through `gh` as the user's own GitHub account, which is why
the body carries a header marking it as automated. Teammates must not read a
Codex finding as a human review.

The label `approved-by-codex` records Codex's approval on the pull request.
`publish-review` adds it when the round's verdict is `approve` and removes it
when a later round's verdict is anything else, so the label always matches the
last published round. It creates the label in a repository that does not have
it yet. A round that ends the loop without an `approve` verdict, because no
blocker or major finding stands, does not get the label. When the label call
fails after the review is posted, the script exits with an error that says so
and gives the `gh pr edit` command that sets the label: run that command and do
not post the review again. `--no-label` leaves the labels alone.

Two modes, chosen by the repo's docs:

- **Publish the outcome** (the default, and the only mode when the repo's
  docs say nothing): run the loop locally, then publish the last round.
  `--dry-run` first, and get explicit approval before posting.
- **Publish every round** (when the repo's pull request doc says the review
  runs on the pull request): after each round, publish it without a dry run
  and without asking. Fix or dispute each finding, commit the fixes as new
  commits and push them, then reply on the pull request: in the thread of
  each inline comment, and in one comment for the findings of the review
  body, named by id. A fixed finding gets the commit and what changed. A
  disputed one gets the reason. Never amend or force-push between rounds:
  it strands the inline comments on stale lines. The history is rewritten
  once, after the approval.

When publishing every round, pass `--new-only` to `publish-review` from round
2 on. A finding upheld from an earlier round then keeps its one thread and is
listed in the review body, instead of getting a second inline comment.

`publish-reply` posts the replies from the same `reply-N.md` the next round
takes:

```bash
~/.claude/skills/converge/bin/publish-reply \
  --pr 123 --round-file "$OUT/round-1.json" --reply "$OUT/reply-1.md"
```

It finds each finding's thread by the id in the inline comment and replies
there. Findings with no thread go in one pull request comment. For a reply
posted by hand, `publish-review` prints the id of each inline comment it
posts.
