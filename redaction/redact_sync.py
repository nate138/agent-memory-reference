#!/usr/bin/env python3
"""
redact_sync.py — reference skeleton for a two-stage, deny-first redaction pipeline.

This is a TEACHING SKELETON, not a production detector. The patterns below are authored
fresh to illustrate the SHAPE of each stage. They are not a detection ruleset to rely on
— write your own against your own data, and expect to tune them for weeks against real
input before you trust them.

Architecture (see the project README for the full reasoning):

    transcript (.jsonl)
          |
          v
    [Stage 1: pattern-redact]   mask structured identifiers -> typed tokens
          |
          v
    [Stage 2: heuristic gate]   scan remainder for maybe-PII shapes
          |
          +-- clean ---------> write to vault
          |
          +-- suspicious ----> quarantine + record which heuristic fired

Design commitments:
  - Deny-first: any Stage-2 hit is HELD, not masked-and-written.
  - Fail closed: any processing error quarantines the file; never write a
    partially-scanned file.
  - Over-flagging is intended. A false positive costs a 30-second review;
    a false negative costs an incident.
"""

import argparse
import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Stage 1 — pattern redaction
#
# Illustrative signatures ONLY. Each is a common, well-known public shape,
# written from scratch here to demonstrate the pattern -> typed-token mapping.
# Replace and extend with your own before this does any real work.
# ---------------------------------------------------------------------------

STAGE1_PATTERNS = [
    # (name, compiled pattern, replacement token)
    ("account_id", re.compile(r"\bacct_[A-Za-z0-9]{6,}\b"), "[ACCOUNT]"),
    ("email",      re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    ("jwt",        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), "[JWT]"),
    ("na_phone",   re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE]"),
    # A long high-entropy run is only *maybe* a key. Mask it, but also let it
    # fall through as a Stage-2 signal (see MAYBE_KEY_TOKEN).
    ("maybe_key",  re.compile(r"\b[A-Za-z0-9]{32,}\b"), "[KEY?]"),
]

MAYBE_KEY_TOKEN = "[KEY?]"


def stage1_redact(text: str):
    """Mask structured identifiers. Returns (redacted_text, masked_count)."""
    masked = 0
    for _name, pattern, token in STAGE1_PATTERNS:
        text, n = pattern.subn(token, text)
        masked += n
    return text, masked


# ---------------------------------------------------------------------------
# Stage 2 — heuristic gate
#
# Catches the un-patterned residue Stage 1 cannot. Looks for SHAPES near
# context keywords, not exact matches. Every hit -> quarantine. These are
# intentionally broad; they over-flag, and that is the correct direction.
# ---------------------------------------------------------------------------

# A capitalized word-pair (a name shape) appearing near a relationship keyword.
NAME_SHAPE = re.compile(
    r"(?:member|customer|client|owner|booked|name)\W+[A-Z][a-z]+\s+[A-Z][a-z]+"
    r"|[A-Z][a-z]+\s+[A-Z][a-z]+\W+(?:member|customer|client|owner)",
)

# number + Capitalized words + street-type suffix
ADDRESS_SHAPE = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s+){1,3}"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Dr|Drive|Lane|Ln|Way|Court|Ct)\b"
)

# Canadian postal (A1A 1A1) or US ZIP (12345 / 12345-6789)
POSTAL_SHAPE = re.compile(r"\b[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d\b|\b\d{5}(?:-\d{4})?\b")

# birth-adjacent string
DOB_SHAPE = re.compile(r"(?:dob|born|birth(?:day|date)?)\W+\d", re.IGNORECASE)


def load_terms(path):
    """Load one-term-per-line file (denylist or allowlist). Missing -> empty set."""
    if not path:
        return set()
    p = Path(path)
    if not p.exists():
        return set()
    return {
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def stage2_scan(text, denylist, allowlist):
    """
    Scan redacted text for maybe-PII. Returns a list of (heuristic, evidence)
    for every signal that fired. A non-empty list means QUARANTINE.
    """
    flags = []

    # Denylist is absolute: a known-sensitive term present -> hard quarantine.
    for term in denylist:
        if term and term in text:
            flags.append(("denylist", term))

    for label, pattern in (
        ("name_shape", NAME_SHAPE),
        ("address_shape", ADDRESS_SHAPE),
        ("postal_shape", POSTAL_SHAPE),
        ("dob_shape", DOB_SHAPE),
    ):
        for m in pattern.finditer(text):
            evidence = m.group(0)
            # Allowlist suppresses a known-safe hit (your own name, project
            # names, tech terms). Additive and operator-controlled.
            if evidence in allowlist:
                continue
            flags.append((label, evidence))

    # A leftover maybe-key token is itself a reason to hold.
    if MAYBE_KEY_TOKEN in text:
        flags.append(("maybe_key", MAYBE_KEY_TOKEN))

    return flags


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

FRONTMATTER = "---\ntype: chat\nsource: {origin}\nredaction-pass: v1\n---\n\n"


def process_file(path, vault_dir, quarantine_dir, mode, denylist, allowlist, origin):
    """
    Process one transcript. Fail closed: any exception -> quarantine, never write
    to the vault. Returns a result dict for the run report.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
        # A .jsonl transcript is line-delimited JSON; extract text content.
        chunks = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            chunks.append(obj.get("text", ""))
        text = "\n".join(chunks)

        redacted, masked = stage1_redact(text)

        if mode == "strict":
            flags = stage2_scan(redacted, denylist, allowlist)
            if flags:
                _write(quarantine_dir, path, redacted, held=True)
                return {"file": str(path), "status": "quarantined",
                        "masked": masked, "reasons": flags}

        out = FRONTMATTER.format(origin=origin) + redacted
        _write(vault_dir, path, out, held=False)
        return {"file": str(path), "status": "written", "masked": masked, "reasons": []}

    except Exception as exc:  # noqa: BLE001 — fail closed on ANY error
        try:
            _write(quarantine_dir, path, f"UNPROCESSED: {exc}\n", held=True)
        except Exception:
            pass
        return {"file": str(path), "status": "quarantined-error",
                "masked": 0, "reasons": [("processing_error", str(exc))]}


def _write(dest_dir, src_path, content, held):
    dest = Path(dest_dir) / (Path(src_path).stem + ".md")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Two-stage deny-first redaction sync.")
    ap.add_argument("--source-dir", required=True)
    ap.add_argument("--vault-dir", required=True)
    ap.add_argument("--quarantine-dir", required=True)
    ap.add_argument("--mode", choices=["strict", "pattern"], default="strict")
    ap.add_argument("--denylist")
    ap.add_argument("--allowlist")
    ap.add_argument("--origin", choices=["code", "web"], default="code")
    args = ap.parse_args(argv)

    denylist = load_terms(args.denylist)
    allowlist = load_terms(args.allowlist)

    results = []
    for path in sorted(Path(args.source_dir).glob("*.jsonl")):
        results.append(process_file(
            path, args.vault_dir, args.quarantine_dir,
            args.mode, denylist, allowlist, args.origin,
        ))

    written = sum(r["status"] == "written" for r in results)
    held = sum(r["status"].startswith("quarantined") for r in results)
    masked = sum(r["masked"] for r in results)

    print(f"files written:     {written}")
    print(f"files quarantined: {held}")
    print(f"tokens masked:     {masked}")
    for r in results:
        if r["reasons"]:
            why = ", ".join(f"{h}" for h, _ in r["reasons"])
            print(f"  HELD  {Path(r['file']).name}: {why}")

    # Nonzero exit if anything was held, so a caller can gate on a clean run.
    return 1 if held else 0


if __name__ == "__main__":
    sys.exit(main())
