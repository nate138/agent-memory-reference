# Agent Memory Reference

This is a reference for a **local, privacy-first memory and code-graph system for AI
coding agents** — a way to give a coding agent a durable "second brain" for a codebase,
without letting sensitive data leak into that brain along the way.

It's a **clean-room reference, not a product or a drop-in library.** I wrote it fresh to
teach the architecture. It leaves out the implementation-specific details — real detection
patterns, real paths, the actual stack, the real skill definitions — partly because those
are the parts that would leak, and partly because a system like this should be built
deliberately, by hand, not pasted in. What's here is the shape of the system and the
reasoning behind each guardrail. That's the part worth sharing.

---

## Why I built this

I'm a self-taught developer working solo on a large codebase. The thing that pushed me to
build this was mundane: I was tired of re-explaining my own project to an AI agent at the
start of every session. Every new chat started from zero — what the project was, what had
already been decided, where things lived. On a big codebase that adds up fast, both in my
time and in the agent's: it would burn through its context window searching around just to
find the paths and files it needed before it could do any actual work.

So the goal was simple to state: let the agent cold-start a session already knowing the
project, so recon and auditing are fast and it isn't spending context just to figure out
where everything is.

The constraint I put on that goal is where most of the real work went. I'm cautious by
nature about sensitive data — and this codebase handles customer and payment information,
with real compliance obligations attached. After working through some cybersecurity
coursework, I've built everything since from a security-first position: design the
protection in from the start, don't bolt it on afterward. A memory system that quietly
accumulates a second copy of your most sensitive data is exactly the kind of thing that
looks convenient and turns into an incident. So the memory had to be useful *and* it had
to be safe by construction. The rest of this document is mostly about how those two goals
are held together.

*(On my background: I spent 25 years as a tattoo artist and ran my own shop before moving
into development. That's a strange line on a technical README, but the work rails more than
you'd think — precision on something permanent, reading what a client actually wants,
problem-solving to get there. I came to security through Coursera's cybersecurity, IT, and
AI courses, and it stuck as a way of thinking, not just a checklist.)*

---

## What the system does, in plain terms

Three pieces work together:

**A vault (a "second brain").** A folder of plain markdown notes the agent reads at the
start of a session and writes to at the end — but only the information I've given it
permission to keep. It holds decisions, lessons, and context, not code and not customer
data.

**A code graph.** A local tool maps the codebase into a graph of nodes — files and the
connections between them at every level. Select a node and you see what it's connected to.
Instead of reading through source to understand how things link up, the agent (and I) can
query the map. This is the part that makes recon fast.

**A redaction pipeline in front of the vault.** Nothing reaches the vault until it passes
a strict, two-stage redaction check. Anything that doesn't pass cleanly doesn't get
written — it goes to a quarantine folder for me to review by hand. The chats and code
sessions that end up in the vault have all been through that filter.

And one habit layered on top: if the agent and I have done something by hand twice, that's
the signal it might be worth turning into a reusable skill — a named routine instead of a
procedure we re-derive every time.

The payoff is context efficiency. The agent thinks less about *where things are* and spends
its budget on the actual work.

---

## Two-stage, deny-first redaction

The core idea is two stages, and the second one is allowed to be wrong in the safe
direction.

**Stage 1 — pattern redaction.** Mask the identifiers that have a reliable shape:
account-style tokens, emails, phone numbers, bearer tokens, high-entropy key-like runs.
Each becomes a typed placeholder so the text stays readable (`[EMAIL]`, `[ACCOUNT]`, and so
on). This catches the obvious majority, cheaply. It doesn't catch everything, and it isn't
asked to.

**Stage 2 — heuristic gate.** After Stage 1, scan the *remaining* text for signs that PII
might still be there even without a clean pattern to match. This is where the un-patterned
stuff gets caught. It looks for shapes, not exact matches:

- Capitalized word-pairs sitting next to relationship words ("member," "customer,"
  "client," "owner") — a name-shaped thing in a name-suggesting spot.
- Street-address shapes: a number, capitalized words, a street-type suffix.
- Postal-code and ZIP shapes.
- Date-of-birth-adjacent strings near birth-related words.
- Anything Stage 1 flagged as *maybe* a key but couldn't confirm.
- A local, hand-maintained denylist of known-sensitive strings — generated from my own
  source of truth, never committed.

Any Stage-2 hit is **quarantined**, not masked-and-written: held outside the vault, with a
note saying which heuristic fired, for me to review before it's ever indexed.

**The tradeoff is deliberate.** These heuristics over-flag on purpose. A capitalized pair
near "owner" might be a real customer name or might be the phrase "Project Owner" — Stage 2
can't always tell, so it holds both. That produces false positives, and that's the correct
direction to be wrong in: **over-flagging costs a thirty-second review; under-flagging
costs an incident.** The review pile isn't a flaw in the design. It's the design working.

**Fail closed.** If the pipeline can't fully process a file — a parse error, malformed
input, anything unexpected — it doesn't write a half-scanned file to the vault. It
quarantines it. A crash resolves toward *hold*, never toward *pass*.

There are two modes: a strict mode (both stages, quarantine on any Stage-2 hit) for
anything touching customer-adjacent data, and a lighter pattern-only mode for repos that
provably carry none. Strict is the default. The lighter mode is a per-repo decision you
make on purpose, not a convenience you reach for.

A reference skeleton lives in [`redaction/`](./redaction/). The patterns in it are written
fresh, as examples of the *shape* of each stage — not a detection ruleset to rely on. Write
your own against your own data, and expect to tune it for a while before you trust it.

---

## AST-only code graph

The graph is generated by a local, third-party AST parser. Two rules keep it safe.

**AST-only. Never deep mode.** The parser has a "deep" mode that sends code structure to an
external service for richer analysis. On any repo with customer-adjacent data, that mode is
*forbidden* — and the rebuild trigger is written so deep mode structurally can't fire, not
just left switched off. AST-only analysis runs entirely on my machine; nothing leaves the
box. That's the whole reason the tool is acceptable to run, so it's enforced in code, not
left to memory.

**No automatic post-commit hook.** The obvious way to keep a graph current is to rebuild on
every commit. I deliberately don't. It puts a third-party binary on the automatic commit
path, adds latency to every commit on a large repo, and sits too close to git operations
that need to stay clean and serial. Instead the rebuild is an explicit trigger I run on
demand or at a checkpoint. If I ever automate it, the right place is a pre-push hook — which
fires far less often — never pre-commit, and never on the customer-data repo first.

A reference skeleton for the rebuild trigger is in [`graph/`](./graph/), with the deep-mode
refusal as the line that matters.

---

## Auditing the parser before it runs

The graph parser is third-party code that reads my entire codebase, so it gets audited
before it touches anything. Someone else's tool is reference; the version that runs is the
one I've read. The checklist:

1. **Verify author and repository.** Confirm the package's listed homepage and repo resolve
   to the real project. A near-miss name is a typosquat flag to clear, not to trust.
2. **Grep for network calls.** Search for outbound-request machinery. Confirm network code
   exists *only* in the deep path, never in the default AST flow. If AST mode makes network
   calls, stop.
3. **Grep for subprocess and dynamic execution.** `subprocess`, `os.system`, `eval`,
   `exec`, `pickle.load` — read every hit.
4. **Check install-time code.** No download-and-run, no post-install hooks reaching out.
5. **Run a known-CVE scan.** Resolve the dependency tree in isolation and scan it. This
   catches *known*-bad packages; it doesn't catch novel malicious code — the manual read
   covers that case. Run both, trust neither alone.

Install into a project-local virtual environment. Never install a code-reading tool
globally. Here the parser is [`graphify`](https://pypi.org/project/graphifyy/) (the
AST-only mode of a public PyPI package); the checklist is tool-agnostic and applies to
anything you let read your source.

---

## How skills get made

Over time the vault becomes raw material for **skills**: small reusable routines the agent
can call by name instead of re-deriving each session. But how they get created is
constrained on purpose, because the easy failure mode is a pile of plausible-looking
routines nobody actually validated.

The rule: **do it twice by hand before it becomes a skill.** A procedure earns a name only
after it's been run manually, in full, at least twice — enough to know its real shape and
edge cases and that it's worth keeping. A routine written before it's ever been run is a
guess with a name on it.

Two things keep this safe. It's **operator-driven, not self-improving** — I decide a
pattern has earned promotion; the agent doesn't mint itself new capabilities on its own,
which would be the same auto-ingest hazard the memory pipeline already designs out. And
skills are **distilled from the vault**, which already holds what actually happened each
session, under redaction — so they're grounded in real work, not an imagined ideal of it.

The skill definitions themselves aren't shipped here (see the omissions below), but the
discipline for making them is the part that transfers.

---

## The cockpit (concept)

A system with a redaction pipeline, a quarantine pile, and a code graph builds up state
worth seeing at a glance — what got masked, what's waiting in quarantine and why, when the
graph last rebuilt. The design for that is a **local control panel**, described here in
concept, not rendered.

Its safety properties are the point. It's **loopback-only** (`127.0.0.1`) and never
network-reachable — a local page, not a service. It's **read-mostly and policy-constrained**:
it reads the same plain files the scripts already write, surfaces state, and flips only the
safe switches. It *cannot* enable deep mode and *cannot* disable redaction on a
customer-data repo — those are policy constants, not toggles. And it has **no new backend** —
no daemon, no database, no new data store. A heavier thing with its own backend would itself
land in compliance scope and become a new attack surface; a static local page over plain
files does the job with none of that.

The one piece worth building early is the **quarantine reviewer.** Deny-by-default creates a
review pile by design, and if reviewing it means opening files by hand one at a time, the
review gets skipped — and a skipped review defeats the whole strict posture. Make it a
one-screen, two-action task (approve into the vault, or discard) so the review actually
happens.

---

## Two-agent workflow, and recon-first

The discipline that makes all of this safe to run is a **two-agent split.** An orchestrator
plans, audits, and holds the decision points — it reasons and reviews, it doesn't execute
file operations itself. An executor does the work: reads, writes, commands. The value is
that two different checkers catch more than one, so I don't collapse the roles.

Riding on top is one principle worth stating on its own: **every handoff claim is a premise
to verify, not settled fact.** A note that says something is done is a claim to check
against the actual state on disk before acting on it — because across real sessions those
claims have been wrong often enough that trusting them is the expensive path. Recon before
writing, every session.

---

## What this reference deliberately leaves out

To keep the privacy line honest, some things are intentionally not here, in any form:

- **The real detection patterns.** The Stage-1 regexes and Stage-2 heuristics that actually
  run aren't here. The examples shown are written fresh for teaching, not a ruleset to
  depend on.
- **The denylist and allowlist contents.** Never, in any form.
- **Real paths, the real stack, real module and schema names.** Naming them fingerprints
  the real system for no teaching benefit. Everything here speaks in roles, not brands.
- **The skill definitions.** Described in concept, not shipped as runnable bodies.

This is the same deny-by-default posture the redaction pipeline runs on, applied to the act
of publishing: when something couldn't be confirmed safe to expose, it stayed out.
Over-redacting a teaching artifact costs a little completeness. Under-redacting it costs the
exact thing the artifact is meant to show.
