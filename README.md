# Agent Memory Reference

A teaching reference for a **local, privacy-first persistent-memory and code-graph
system for AI coding agents.** It shows how to give a coding agent durable memory of a
codebase and of past decisions — without letting sensitive data leak into that memory
along the way.

This repository is a **clean-room reference, not a product and not a drop-in library.**
It is authored fresh to teach the architecture. It deliberately omits the
implementation-specific details (real detection patterns, real paths, a real stack, real
skill definitions) that would make it a paste-and-run tool, because those details are
exactly where a privacy-first system leaks. What is here is the shape of the system and
the reasoning behind each guardrail — enough to build your own version deliberately,
which is the only way a system like this should be built.

---

## The problem

Give an AI coding agent a persistent memory — a vault of notes it writes at the end of
each working session — and you get a real productivity gain: the agent cold-starts its
next session already knowing the project's decisions, constraints, and hard-won lessons.

You also get a new hazard. Whatever the agent writes into that vault is now sitting in
plain files on disk, and if the codebase touches customer-adjacent data, some fraction
of what flows through a working session is sensitive: a customer email quoted in a stack
trace, an account identifier pasted into a debugging note, a real name in a git author
header. Write that into the vault unfiltered and you have quietly built a second,
unaudited copy of your most sensitive data — one that no compliance boundary is watching.

The naive fix is a regex sweep on the way in: match the obvious identifiers, mask them,
write the rest. This is not enough, and treating it as enough is the actual failure mode.
Regex catches the *structured* identifiers — the ones with a predictable shape. It misses
the unstructured ones: a person's name has no regex; a street address only sometimes
does; a novel identifier format you have never seen has none at all. A pipeline that
optimizes only for "catch everything with a pattern" passes exactly the dangerous,
un-patterned residue straight through.

So redaction here is treated as **architecture, not a feature.** The rest of this
document is that architecture.

---

## Two-stage, deny-first redaction

The core idea: **two stages, and the second one is allowed to be wrong in the safe
direction.**

**Stage 1 — pattern redaction.** Mask the structured identifiers that have a reliable
shape: account-style tokens, emails, phone numbers, bearer tokens and JWTs, high-entropy
key-like runs. Replace each with a typed placeholder so the redacted text stays readable
(`[EMAIL]`, `[ACCOUNT]`, and so on). This catches the obvious majority cheaply and fast.
It does not catch everything, and it is not asked to.

**Stage 2 — heuristic gate.** After Stage 1, scan the *remaining* text for signals that
PII might still be present even without a clean pattern to match. This is where the
un-patterned residue gets caught. The heuristics look for *shapes*, not exact matches:

- Capitalized word-pairs sitting next to relationship keywords (near words like
  "member," "customer," "client," "owner") — a name-shaped thing in a name-suggesting
  context.
- Street-address shapes: a number followed by capitalized words and a street-type suffix.
- Postal-code and ZIP shapes.
- Date-of-birth-adjacent strings near birth-related keywords.
- Any token Stage 1 flagged as *maybe* a key but could not confirm.
- A local, hand-maintained denylist of known-sensitive strings, generated from your own
  source of truth and never committed.

Any Stage-2 hit does not get masked and written. It gets **quarantined** — held in a
separate directory, outside the vault, with a note recording which heuristic fired — for
a human to review before it is ever indexed.

**The deliberate tradeoff.** These heuristics over-flag by design. A capitalized word-pair
near "owner" might be a real customer name or might be the phrase "Project Owner." Stage 2
cannot always tell, so it quarantines both. This produces false positives, and that is the
correct choice: **over-flagging costs a thirty-second review; under-flagging costs an
incident.** A redaction system tuned to minimize review friction is tuned in the wrong
direction. The review pile is not a bug in the design — it is the design working.

**Fail closed.** If the pipeline cannot fully process a file — a parse error, malformed
input, anything unexpected — it does not write a partially-scanned file into the vault. It
quarantines it. The system never writes a file it did not fully scan. A crash resolves
toward *hold*, never toward *pass*.

**Two modes.** A strict mode (Stage 1 + Stage 2, quarantine on any Stage-2 hit) for
repositories that touch customer-adjacent data, and a lighter pattern-only mode for
repositories that provably carry no such data. The strict mode is the default; the lighter
mode is a deliberate opt-out you make per-repository, not a convenience you reach for.

A reference skeleton for this pipeline lives in [`redaction/`](./redaction/), with
illustrative patterns authored fresh for teaching. They are examples of the *shape* of
Stage 1, not a detection ruleset to rely on — write your own against your own data.

---

## AST-only code graph

The second half of the system is a **code-structure graph**: a queryable map of a
codebase's modules, imports, and call relationships, so the agent can ask "what touches
this?" without reading every file. This is generated by a local, third-party AST parser.

Two postures make it safe:

**AST-only. Never deep mode.** The parser has a "deep" mode that sends code structure to
an external LLM for richer analysis. On any repository with customer-adjacent data, that
mode is **forbidden** — not discouraged, forbidden — and the rebuild trigger is written so
that deep mode is structurally unable to fire, not merely left unset. AST-only analysis
runs entirely on your machine; nothing leaves the box. That property is the whole reason
the tool is acceptable to run at all, so it is enforced in code rather than trusted to
habit.

**No automatic post-commit hook.** The obvious way to keep a graph current is a
post-commit hook that rebuilds on every commit. This reference deliberately does not do
that, for three reasons: it puts a third-party binary on the automatic commit path; graph
rebuilds add latency to every commit on a large repository; and an automatic hook sits
uncomfortably close to serial-only git operations that must never be wrapped by other
tooling. Instead, the rebuild is an **explicit, flag-locked trigger** you invoke on demand
or at a verification checkpoint. If automation is ever wanted, the right place for it is a
pre-push hook — which fires far less often and still keeps the graph current before code
leaves the machine — never pre-commit, and never on a customer-data repository first.

A reference skeleton for the rebuild trigger lives in [`graph/`](./graph/), with the
deep-mode refusal shown as the load-bearing line.

---

## Auditing the third-party parser before it runs

The graph parser is third-party code that reads your entire codebase. It gets audited
before it touches a single repository. Their work is reference; the version that runs is
the one you have read. The checklist:

1. **Verify author and repository.** Confirm the package's listed homepage and repository
   resolve to the real project you intend to install. A near-miss name is a typosquat
   flag to clear, not a verdict to trust.
2. **Grep for network calls.** Search the source for outbound-request machinery
   (`requests`, `urllib`, `httpx`, `socket`, any API hostnames). Confirm network code
   exists *only* in the deep/semantic path, never in the default AST flow. If AST mode
   makes network calls, stop.
3. **Grep for subprocess and dynamic execution.** Search for `subprocess`, `os.system`,
   `eval`, `exec`, `pickle.load`. Read every hit line by line.
4. **Check install-time code.** Confirm the install path has no surprising logic — no
   download-and-run, no post-install hooks reaching outward.
5. **Run a known-CVE scan.** Resolve the dependency tree in isolation and scan it with a
   package-advisory tool. This catches *known-bad* packages; it does not catch novel
   malicious code — the manual source read is what covers the novel case. Run both and
   trust neither alone.

Install into a project-local virtual environment. Never install a code-reading tool
globally. In this reference the parser is [`graphify`](https://pypi.org/project/graphifyy/)
(the AST-only mode of a public PyPI package); the checklist above is tool-agnostic and
applies to anything you let read your source.

---

## The cockpit (concept)

A system with a redaction pipeline, a quarantine pile, and a code graph accumulates state
worth seeing at a glance: what got masked, what is waiting in quarantine and why, when the
graph last rebuilt. The reference design for that is a **local control panel** — described
here in concept, deliberately not rendered.

Its safety properties are the point:

- **Loopback-only.** It binds to `127.0.0.1` and is never network-reachable. It is a local
  page, not a service.
- **Read-mostly, and policy-constrained.** It reads the same plain files the scripts
  already write. It surfaces state and flips the safe switches. It **cannot** enable deep
  mode and **cannot** disable redaction on a customer-data repository — those are policy
  constants, not user-toggleable settings. The panel exposes only switches that are safe
  to flip.
- **No new backend.** No daemon, no database, no new data store. A heavier "OS" with its
  own backend would itself become a thing in compliance scope and a fresh attack surface;
  a static local page over plain files does the job with none of that.

The one piece worth building early is the **quarantine reviewer**: deny-by-default creates
a review pile by design, and if reviewing that pile means opening files by hand, the review
step gets skipped — and a skipped review defeats the whole strict posture. Make reviewing
held items a one-screen, two-action task (approve into the vault, or discard) so the review
actually happens.

---

## Two-agent workflow and recon-first

The discipline that makes all of the above safe to operate is a **two-agent split**:

- An **orchestrator** plans, audits, and holds the decision points. It reasons and reviews;
  it does not execute file operations itself.
- An **executor** does the work: reads, writes, runs commands. It executes; it does not own
  the judgment calls or the gates.

The value is orthogonality — two different checkers catch more than one, and collapsing the
roles defeats the structure.

Riding on top of that is one principle worth stating on its own: **every handoff claim is a
premise to verify, not settled ground truth.** A note that says a thing is done is a claim
to check against the actual disk state before acting on it, because across real sessions
those claims have been wrong often enough that trusting them is the expensive path. Recon
before writing, every session. The map is not the territory; read the territory.

---

## How the system grows skills

The memory layer is not just a record — over time it becomes the raw material for
**skills**: small, reusable routines the agent can invoke by name instead of re-deriving a
procedure from scratch each session. But the way skills get created is deliberately
constrained, because the failure mode here is a system that generates plausible-looking
routines nobody actually validated.

The governing rule is **do it twice by hand before it becomes a skill.** A procedure earns
promotion into a named skill only after it has been performed manually, in full, at least
twice — enough to know its real shape, its edge cases, and that it is worth keeping. A
routine written speculatively, before it has ever been run against reality, is a guess with
a name on it.

Two properties keep this safe:

- **Operator-driven, not self-improving.** Skills are distilled from *observed* recurring
  patterns by the person running the system — not minted autonomously by the agent. The
  agent does not decide, on its own, that a pattern is now a skill and write itself a new
  capability. That would be the same auto-ingest hazard the memory pipeline already designs
  out, wearing a different label. Pattern recognition is the operator's call.
- **Distilled from the vault, not from thin air.** Because the vault already accumulates
  what actually happened each session — under redaction, deny-first — it is a corpus of
  real, sanitized workflow history. Skills mined from that corpus are grounded in work that
  was really done, not in an imagined ideal of how the work might go.

The result is a skill layer that grows slowly and on purpose: proven routines only,
promoted by a human, sourced from a memory that was itself built carefully. The skill
*definitions* themselves are not shipped in this reference — see the omissions below — but
the discipline for creating them is the transferable part, and it is the point.

---

## What this reference deliberately omits

To keep the privacy boundary honest, some things are intentionally not in this repository,
in any form:

- **The real detection patterns.** The Stage-1 regexes and Stage-2 heuristics that run in
  the private system are not here. The illustrative patterns shown are authored fresh for
  teaching and are not a ruleset to depend on.
- **The denylist and allowlist contents.** Never, in any form.
- **Real paths, a real stack, real module and schema names.** The system runs on a specific
  stack against a specific codebase; naming either fingerprints the private system to no
  teaching benefit. Everything here speaks in roles, not brands.
- **Skill definitions.** The agent's saved routines are described in concept elsewhere, not
  shipped here as runnable bodies.

This is the same deny-by-default posture the redaction pipeline itself runs on, applied to
the act of publishing: when an element could not be affirmed safe to expose, it stays out.
Over-redacting a teaching artifact costs a little completeness. Under-redacting it costs the
exact thing the artifact is meant to demonstrate.
