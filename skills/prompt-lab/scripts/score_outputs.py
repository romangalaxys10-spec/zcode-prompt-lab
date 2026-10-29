#!/usr/bin/env python3
"""score_outputs.py -- deterministic scoring for a prompt/skill A/B harness.

Stdlib only. Python 3.8+. Cross-OS.

What it scores, and what it does not:
  * Objective criteria in the rubric are auto-scored and fully deterministic:
      - type "contains": each needle must appear (case-insensitive).
      - type "regex":    each pattern must match (re.search, DOTALL).
      - type "manual" / "llm-judged-placeholder": NOT auto-scored. They are
        written out with judged:false so the agent can fill them in.
  * A fixture's expected_properties are auto-scored only when the property is
    a plain substring (present) or starts with "no" / "does not contain"
    / "not contain" (absent). Anything subjective is flagged for manual.
  * Everything that needs reasoning is the agent's call, and that call is not
    deterministic. This script never invents a judgment.

Layout the harness uses (matches SKILL.md):
  <out-dir>/<fixture-id>/<variant>-run<n>.txt   one output per run
  <out-dir>/<fixture-id>/runs.json              contamination audit trail
  <out-dir>/<variant>/scores.json               one score file per variant

Commands:
  --check-rubric <rubric.json>
  --check-fixtures <fixture.json|dir>
  --rubric <r.json> --fixture <f.json|dir> --out-dir <d> [--variants A B]
      score objective criteria, write <d>/<variant>/scores.json
  --rubric <r.json> --out-dir <d> --aggregate [--variants A B]
      re-read each variant's scores.json (with manual judgments applied) and
      print a comparison table + winner to stdout

Exit codes:
  0 success
  1 file / missing-output error
  2 bad arguments or malformed JSON
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def fail(code, msg):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.exit(code)


# ---------------------------------------------------------------- rubric


def load_rubric(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(1, "rubric not found: %s" % path)
    except json.JSONDecodeError as e:
        fail(2, "rubric is not valid JSON: %s" % e)
    criteria = raw.get("criteria", raw) if isinstance(raw, dict) else raw
    if not isinstance(criteria, list) or not criteria:
        fail(2, "rubric must define a non-empty 'criteria' list")
    norm = []
    known_types = {"contains", "regex", "manual", "llm-judged-placeholder"}
    for i, c in enumerate(criteria):
        if not isinstance(c, dict) or "type" not in c:
            fail(2, "criterion #%d must be an object with a 'type'" % i)
        ctype = c["type"]
        if ctype not in known_types:
            fail(2, "criterion #%d has unknown type %r (use contains/regex/manual)" % (i, ctype))
        if ctype == "contains" and not c.get("needles"):
            fail(2, "criterion %s is 'contains' but lists no needles" % c.get("id", i))
        if ctype == "regex" and not c.get("patterns"):
            fail(2, "criterion %s is 'regex' but lists no patterns" % c.get("id", i))
        w = c.get("weight", 1.0)
        if not isinstance(w, (int, float)) or w < 0:
            fail(2, "criterion %s has a bad weight %r" % (c.get("id", i), w))
        norm.append({
            "id": c.get("id", "c%d" % i),
            "type": "manual" if ctype in ("manual", "llm-judged-placeholder") else ctype,
            "description": c.get("description", ""),
            "needles": c.get("needles", []) or [],
            "patterns": c.get("patterns", []) or [],
            "weight": float(w),
            "auto": c["type"] in ("contains", "regex"),
        })
    return norm


# ---------------------------------------------------------------- fixtures


def load_fixtures(path):
    p = Path(path)
    if p.is_dir():
        files = sorted(p.glob("*.json"))
        if not files:
            fail(1, "no fixture .json files in %s" % path)
        out = []
        for f in files:
            out.extend(load_fixtures(str(f)))
        return out
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(1, "fixture not found: %s" % path)
    except json.JSONDecodeError as e:
        fail(2, "fixture is not valid JSON: %s" % e)
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        fail(2, "fixture must be a list (or a single object) with at least one entry")
    norm = []
    for i, fx in enumerate(raw):
        if not isinstance(fx, dict):
            fail(2, "fixture #%d must be an object" % i)
        if "input" not in fx:
            fail(2, "fixture #%d has no 'input'" % i)
        norm.append({
            "id": fx.get("id", "f%d" % i),
            "input": fx["input"],
            "expected_properties": fx.get("expected_properties", []) or [],
        })
    return norm


def classify_property(prop):
    """Return ('present', needle) or ('absent', needle) for auto-checkable
    properties, else ('manual', prop) for ones the agent must judge."""
    s = str(prop).strip()
    low = s.lower()
    for prefix in ("does not contain", "not contain", "without", "no "):
        if low.startswith(prefix):
            needle = s[len(prefix):].strip()
            if not needle:
                return ("manual", s)
            return ("absent", needle)
    return ("present", s)


# ---------------------------------------------------------------- scoring


def match_criterion(c, text, low_text):
    """Return (ok, reason) for one auto criterion. Fails closed on bad regex."""
    if c["type"] == "contains":
        missing = [n for n in c["needles"] if str(n).lower() not in low_text]
        return (len(missing) == 0,
                "missing: %s" % ", ".join(map(str, missing)) if missing else "all needles present")
    for pat in c["patterns"]:
        try:
            if not re.search(pat, text, re.DOTALL):
                return (False, "regex did not match: %s" % pat)
        except re.error as e:
            return (False, "invalid regex %r: %s" % (pat, e))
    return (True, "all patterns matched")


def score_one(text, criterion, prop_mode, prop_needle):
    if prop_mode == "present":
        return prop_needle.lower() in text.lower()
    if prop_mode == "absent":
        return prop_needle.lower() not in text.lower()
    ok, _ = match_criterion(criterion, text, text.lower())
    return ok


def collect_outputs(out_dir, fixture_id, variant, runs):
    """Yield (run_no, path, text_or_None)."""
    d = Path(out_dir) / str(fixture_id)
    for n in range(1, runs + 1):
        cand = d / ("%s-run%d.txt" % (variant, n))
        # allow 1-based or arbitrary run numbers in the directory
        if cand.exists():
            yield n, cand, cand.read_text(encoding="utf-8")
        else:
            # fall back to any file for this run
            alt = sorted(d.glob("%s-run*.txt" % variant))
            txt = None
            if alt:
                txt = alt[0].read_text(encoding="utf-8")
            yield n, cand, txt


def compute_scores(variant, rubric, fixtures, out_dir, runs_per, existing=None):
    """Build one variant's scores.json dict.

    'existing' (a loaded scores.json) is used to carry forward manual
    judgments the agent already wrote.
    """
    ex = existing or {}
    ex_fx = {f["id"]: f for f in ex.get("fixtures", [])}

    fx_results = []
    total_w = 0.0
    got_w = 0.0
    pending_count = 0

    for fx in fixtures:
        fx_id = fx["id"]
        ex_run = {}
        exr = ex_fx.get(fx_id, {}).get("runs", [])
        for r in exr:
            ex_run[int(r.get("run", 0))] = r

        run_entries = []
        for n in range(1, runs_per + 1):
            prev = ex_run.get(n, {})
            text = None
            # prefer the live output file; fall back to nothing
            for rn, path, live_text in collect_outputs(out_dir, fx_id, variant, 1):
                if live_text is not None:
                    text = live_text
                    break
            # if the agent recorded an output already, reuse recorded text
            rec = prev.get("text")
            if text is None and rec:
                text = rec
            low = (text or "").lower()

            crit_out = {}
            for c in rubric:
                if c["auto"]:
                    ok, reason = match_criterion(c, text or "", low)
                    crit_out[c["id"]] = {
                        "type": c["type"], "pass": ok, "reason": reason,
                        "judged": True, "weight": c["weight"],
                    }
                    total_w += c["weight"]
                    if ok:
                        got_w += c["weight"]
                else:
                    judged = prev.get("criteria", {}).get(c["id"], {})
                    if judged.get("judged") is True:
                        ok = bool(judged.get("pass", False))
                        crit_out[c["id"]] = {
                            "type": c["type"], "pass": ok,
                            "reason": judged.get("reason", "agent judgment"),
                            "judged": True, "weight": c["weight"],
                        }
                        total_w += c["weight"]
                        if ok:
                            got_w += c["weight"]
                    else:
                        pending_count += 1
                        crit_out[c["id"]] = {
                            "type": c["type"], "pass": None, "judged": False,
                            "reason": "awaiting agent judgment: %s" % c["description"],
                            "weight": c["weight"],
                        }
                        crit_out[c["id"]]["judge"] = c["description"]

            prop_out = []
            for prop in fx["expected_properties"]:
                mode, needle = classify_property(prop)
                if mode in ("present", "absent"):
                    # missing output counts as a failed 'present' check, not as manual
                    ok = score_one(text or "", {"type": "contains", "needles": [needle]}, mode, needle)
                    prev_p = [p for p in prev.get("expected_properties", [])
                              if p.get("property") == str(prop)]
                    if prev_p and prev_p[0].get("judged") is True:
                        ok = bool(prev_p[0].get("pass"))
                    crit_w = 1.0
                    total_w += crit_w
                    if ok:
                        got_w += crit_w
                    prop_out.append({
                        "property": str(prop), "mode": mode, "needle": needle,
                        "auto": True, "pass": ok, "judged": True, "weight": crit_w,
                    })
                else:
                    prev_p = [p for p in prev.get("expected_properties", [])
                              if p.get("property") == str(prop)]
                    judged = prev_p and prev_p[0].get("judged") is True
                    ok = bool(prev_p[0].get("pass")) if judged else None
                    if not judged:
                        pending_count += 1
                    prop_out.append({
                        "property": str(prop), "mode": "manual",
                        "auto": False, "pass": ok, "judged": judged, "weight": 1.0,
                    })

            score = (got_w / total_w) if total_w else 0.0
            run_entries.append({
                "run": n,
                "output_file": str(Path(out_dir) / str(fx_id) / ("%s-run%d.txt" % (variant, n))),
                "text": text,
                "criteria": crit_out,
                "expected_properties": prop_out,
                "score": round(score, 4),
            })

        fx_score = (sum(r["score"] for r in run_entries) / len(run_entries)) if run_entries else 0.0
        fx_results.append({
            "id": fx_id,
            "runs": run_entries,
            "score": round(fx_score, 4),
        })

    total = (got_w / total_w) if total_w else 0.0
    return {
        "variant": variant,
        "fixtures": fx_results,
        "total": round(total, 4),
        "pending_manual": pending_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "total covers objective criteria plus any manual criteria already judged. "
                "Unjudged manual criteria are excluded from the total and reported as pending_manual.",
    }


def write_scores(out_dir, variant, doc):
    target = Path(out_dir) / variant / "scores.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


# ---------------------------------------------------------------- commands


def cmd_check_rubric(path):
    crit = load_rubric(path)
    print("rubric OK: %d criteria" % len(crit))
    for c in crit:
        auto = "auto" if c["auto"] else "manual"
        extra = ""
        if c["type"] == "contains":
            extra = " needles=%s" % ", ".join(map(str, c["needles"]))
        if c["type"] == "regex":
            extra = " patterns=%s" % ", ".join(c["patterns"])
        print("  [%s] %-24s w=%s  %s%s" % (auto, c["id"], c["weight"], c["description"], extra))
    sys.exit(0)


def cmd_check_fixtures(path):
    fx = load_fixtures(path)
    print("fixtures OK: %d entries" % len(fx))
    for f in fx:
        print("  %s: %d expected_properties" % (f["id"], len(f["expected_properties"])))
    sys.exit(0)


def cmd_score(rubric_path, fixture_path, out_dir, variants, runs_per):
    rubric = load_rubric(rubric_path)
    fixtures = load_fixtures(fixture_path)
    missing = []
    for v in variants:
        for fx in fixtures:
            d = Path(out_dir) / str(fx["id"])
            if not d.exists():
                missing.append("%s/%s (no output dir)" % (v, fx["id"]))
            else:
                for n in range(1, runs_per + 1):
                    p = d / ("%s-run%d.txt" % (v, n))
                    if not p.exists() and not any(d.glob("%s-run*.txt" % v)):
                        missing.append(str(p))
    if missing:
        sys.stderr.write("warning: missing outputs, will be scored as empty:\n  " +
                         "\n  ".join(missing) + "\n")
    for v in variants:
        try:
            ex_path = Path(out_dir) / v / "scores.json"
            existing = json.loads(ex_path.read_text(encoding="utf-8")) if ex_path.exists() else None
        except json.JSONDecodeError as e:
            fail(1, "existing scores.json for %s is malformed: %s" % (v, e))
        doc = compute_scores(v, rubric, fixtures, out_dir, runs_per, existing)
        tgt = write_scores(out_dir, v, doc)
        print("wrote %s  (total=%s, pending_manual=%d)" %
              (tgt, doc["total"], doc["pending_manual"]))
    # audit trail check
    print("reminder: keep each <out-dir>/<fixture>/runs.json so the contamination "
          "audit trail is complete.")
    sys.exit(0)


def cmd_aggregate(rubric_path, out_dir, variants):
    docs = []
    for v in variants:
        p = Path(out_dir) / v / "scores.json"
        if not p.exists():
            fail(1, "no scores.json for variant %s (run the scoring step first)" % v)
        docs.append(json.loads(p.read_text(encoding="utf-8")))
    docs.sort(key=lambda d: d["total"], reverse=True)
    print("Aggregate (manual criteria already judged are included):\n")
    for d in docs:
        print("  Variant %-3s  total=%s  pending_manual=%d" %
              (d["variant"], d["total"], d["pending_manual"]))
    print("\nPer-fixture:")
    ids = [f["id"] for f in docs[0]["fixtures"]]
    print("  %-12s %s" % ("fixture", "  ".join("%-6s" % d["variant"] for d in docs)))
    for fid in ids:
        row = []
        for d in docs:
            m = {f["id"]: f for f in d["fixtures"]}
            row.append("%-6s" % (m.get(fid, {}).get("score", "-")))
        print("  %-12s %s" % (fid, "  ".join(row)))
    top = docs[0]
    bottom = docs[-1]
    if len(docs) == 2 and top["total"] - bottom["total"] < 0.1:
        print("\nVerdict: within 0.10 of each other; treat as a draw at this run count "
              "unless runs_per_fixture was raised.")
    else:
        print("\nVerdict: %s leads by %.3f over %s." %
              (top["variant"], top["total"] - bottom["total"], bottom["variant"]))
    sys.exit(0)


def main():
    ap = argparse.ArgumentParser(
        description="Deterministic scoring for a prompt/skill A/B harness.")
    ap.add_argument("--check-rubric", metavar="FILE", help="validate and list rubric criteria")
    ap.add_argument("--check-fixtures", metavar="FILE_OR_DIR", help="validate and list fixtures")
    ap.add_argument("--rubric", metavar="FILE", help="rubric JSON")
    ap.add_argument("--fixture", metavar="FILE_OR_DIR", help="fixture JSON or directory")
    ap.add_argument("--out-dir", metavar="DIR", help="harness workdir")
    ap.add_argument("--aggregate", action="store_true", help="re-read scores.json and print a comparison")
    ap.add_argument("--variants", nargs="*", default=["A", "B"], help="variant labels (default A B)")
    ap.add_argument("--runs-per", type=int, default=1, help="runs per fixture (default 1)")
    a = ap.parse_args()

    if a.check_rubric:
        cmd_check_rubric(a.check_rubric)
        return
    if a.check_fixtures:
        cmd_check_fixtures(a.check_fixtures)
        return

    if not a.rubric:
        fail(2, "need --rubric (or --check-rubric / --check-fixtures)")
    if not a.variants:
        fail(2, "--variants must list at least one label")

    if a.aggregate:
        if not a.out_dir:
            fail(2, "--aggregate needs --out-dir")
        cmd_aggregate(a.rubric, a.out_dir, a.variants)
        return

    if not a.out_dir:
        fail(2, "scoring needs --out-dir")
    if not a.fixture:
        fail(2, "scoring needs --fixture (a file or directory of fixture files)")
    cmd_score(a.rubric, a.fixture, a.out_dir, a.variants, a.runs_per)


if __name__ == "__main__":
    main()
