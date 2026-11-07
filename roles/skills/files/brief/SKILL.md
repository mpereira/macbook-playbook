---
name: brief
description: 'Turn a rough feature request into an implementation plan that has survived review by a second agent. Asks clarifying questions first, writes a plan, then loops with Codex until the plan converges. Use when the user says brief, brief me, plan this, write a plan, spec this out, or asks to think through an approach before any code is written. Pairs with /build, which implements the resulting plan.'
---

# Brief

Phase one of a two-phase workflow. Produce a plan that a second agent has
already tried to break. `/build` implements what comes out of this.

Order: **recon → questions → plan → loop**.

## 1. Recon before you ask anything

Spend a bounded pass on the actual code first — enough to know which parts of
the request the repo already answers. Read the repo's `AGENTS.md` / `CLAUDE.md`
and the area guides they point to, then find the files the request touches.

This step exists so the questions are worth asking. Questions written before
reading the code are generic, and answering them is the user doing your job.

Keep it proportionate: a few searches and file reads, not a full audit.

## 2. Ask everything you need, in one pass

Use `AskUserQuestion`. Batch up to 4 per call; call it again if genuinely
needed, but aim to finish in one or two rounds rather than drip-feeding.

Ask about:

- **Scope edges** — what is explicitly *not* in this change. The highest-value
  question and the one most often skipped.
- **Approach forks** — where two designs are both defensible and the choice
  shapes everything downstream.
- **Done criteria** — what has to be true to ship. Tests? A migration run? A
  flag flipped?
- **Constraints** — backwards compatibility, data migration, deadline pressure
  that would justify a cruder approach.

Rules:

- **Never ask what the code answers.** If recon can settle it, settle it.
- **Never ask what the user already said.** Re-asking reads as not listening.
- Give 2–4 concrete options, not an open prompt. Put your recommendation first
  and mark it `(Recommended)`.
- Stop when the remaining unknowns would not change the plan. Perfect
  information is not the goal; a plan that will not be invalidated is.

If the request is genuinely unambiguous, say so and skip to step 3 rather than
manufacturing questions.

## 3. Write the plan

```bash
mkdir -p "$(git rev-parse --show-toplevel)/.claude/plans"
```

Write to `.claude/plans/<slug>.md` — gitignored, and it persists across
sessions so `/build` can pick it up later. Slug from the feature, e.g.
`reddit-alert-date-range.md`.

Structure:

```markdown
# <Feature>

## Goal
One paragraph. What is true after this ships that is not true now.

## Non-goals
Explicit. This is what stops scope creep during /build.

## Decisions
Each fork you resolved in step 2, with the answer and the reason.

## Steps
Ordered and concrete. Name real files and functions. Each step should be
verifiable on its own.

## Verification
The exact commands that prove it works. Read the repo's agent docs for the
real targets rather than guessing at them.

## Risks
What could make this plan wrong, and the cheapest signal that it has.
```

Name real paths. A plan that says "update the API layer" cannot be reviewed,
and Codex will correctly say so.

## 4. Loop with the reviewer

Follow the `converge` skill in plan mode:

```bash
OUT="$(git rev-parse --show-toplevel)/.claude/converge" && mkdir -p "$OUT"
~/.claude/skills/converge/bin/codex-review \
  --mode plan --input .claude/plans/<slug>.md --round 1 \
  --out "$OUT" --workdir "$PWD"
```

Plan mode is **checkpointed**: after each round, show the user the findings and
what you intend to change, then continue. Plan-level errors compound into
everything built on them, so a per-round human glance is cheap.

Edit the plan file in place between rounds — `/build` reads the final version,
so it must reflect what was agreed.

`converge` owns the stopping rules and the disagreement rule. In particular:
do not weaken the plan just to make Codex approve it. A contested finding goes
to the user with both arguments.

## 5. Hand off

Report:

- Path to the converged plan
- What changed across rounds — the findings that actually improved it
- Anything contested and still open
- The suggested next command:

```bash
/build .claude/plans/<slug>.md
```

Do not start implementing. That is `/build`'s job, and the split is the point:
it gives the user a checkpoint between deciding and doing.
