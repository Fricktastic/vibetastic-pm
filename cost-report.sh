#!/bin/bash
# Cost rollup for the PM framework. Reads:
#   - logs/cost.jsonl          (one record per OpenCode dispatch, written by dispatch.sh)
#   - logs/agent-spawns.jsonl  (one record per Agent tool spawn, written mechanically by the
#                               PostToolUse hook scripts/log-agent-spawn.py)
#   - TASK_LOG.md        (cost_event entries the PM logs per planning-agent spawn)
#   - MODELS.md          (the Pricing table → $/Mtok, source of truth)
# and prints per-model rollups: runs, attempts, wall-clock, tokens, and $ estimate
# (only where tokens and a price are both known).
#
# Usage: bash framework/cost-report.sh [logs-dir] [models-file] [task-log]
# Defaults: logs/  MODELS.md  TASK_LOG.md  (relative to the PM project dir)
set -euo pipefail

LOG_DIR="${1:-logs}"
MODELS_FILE="${2:-MODELS.md}"
TASK_LOG="${3:-TASK_LOG.md}"

python3 - "$LOG_DIR/cost.jsonl" "$MODELS_FILE" "$TASK_LOG" <<'PY'
import json, re, sys, os
from collections import defaultdict

cost_jsonl, models_file, task_log = sys.argv[1:4]

# --- Parse the Pricing table from MODELS.md: slug -> (in_per_mtok, out_per_mtok) ---
prices = {}
if os.path.exists(models_file):
    in_pricing = False
    for line in open(models_file):
        if line.strip().startswith("## Pricing"):
            in_pricing = True; continue
        if in_pricing and line.startswith("## "):
            break
        if in_pricing and line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3 or cells[0] in ("Model slug", "") or set(cells[0]) <= set("-"):
                continue
            slugs = [s.strip().strip("`") for s in cells[0].split("/")]
            try:
                pin, pout = float(cells[1]), float(cells[2])
            except ValueError:
                continue  # "?" or non-numeric
            for s in slugs:
                prices[s] = (pin, pout)

def price_for(model):
    if model in prices:
        return prices[model]
    # last path segment (e.g. openrouter/anthropic/claude-opus-4.8 -> claude-opus-4.8)
    return prices.get(model.rsplit("/", 1)[-1])

# --- Aggregate OpenCode dispatch records ---
agg = defaultdict(lambda: {"runs":0,"attempts":0,"dur":0,"in":0,"out":0,"cache":0,"fails":0,"cost":0.0,"costed":0,"role":"opencode"})
# Primary reliability: keyed by the ORIGINALLY-requested primary model (r["primary_model"]),
# so a primary that failed and was rescued by its fallback is counted against the PRIMARY,
# not attributed to the fallback in `model`. "rescued" = the dispatch needed a same-model
# stall retry and/or a fallback switch. Only records that carry primary_model (dispatch.sh
# after the telemetry fix) are counted — older records are silently skipped.
prim = defaultdict(lambda: {"runs":0,"stall_retried":0,"fell_back":0,"rescued":0})
if os.path.exists(cost_jsonl):
    for line in open(cost_jsonl):
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except json.JSONDecodeError: continue
        pm = r.get("primary_model")
        if pm:
            p = prim[f"{r.get('backend','opencode')}: {pm}"]
            p["runs"] += 1
            sr = (r.get("stall_retries") or 0) > 0
            fb = bool(r.get("fallback_used"))
            if sr: p["stall_retried"] += 1
            if fb: p["fell_back"] += 1
            if sr or fb: p["rescued"] += 1
        a = agg[f"{r.get('backend','opencode')}: {r.get('model','?')}"]
        a["role"] = r.get("role","opencode")
        a["runs"] += 1
        a["attempts"] += r.get("attempts",1) or 1
        a["dur"] += r.get("duration_s",0) or 0
        if isinstance(r.get("input_tokens"), int):  a["in"]  += r["input_tokens"]
        if isinstance(r.get("output_tokens"), int): a["out"] += r["output_tokens"]
        if isinstance(r.get("cache_read_tokens"), int): a["cache"] += r["cache_read_tokens"]
        if isinstance(r.get("cost_usd"), (int, float)):
            a["cost"] += r["cost_usd"]; a["costed"] += 1
        # verify_passed: false = verify exhausted; null/absent = no verifier configured (not a failure)
        if r.get("verify_passed") is False: a["fails"] += 1

# --- Parse cost_event blocks from TASK_LOG into dicts (task_id, role, model, burn_proxy) ---
cost_events = []
if os.path.exists(task_log):
    txt = open(task_log).read()
    # Split on each cost_event header, then read the fenced yaml body that follows it.
    for chunk in re.split(r"·\s*cost_event", txt)[1:]:
        body = re.search(r"```(?:yaml)?\s*(.*?)```", chunk, re.S)
        if not body: continue
        ev = {}
        for kv in re.finditer(r"^\s*(\w+):\s*(.*?)\s*$", body.group(1), re.M):
            ev[kv.group(1)] = kv.group(2).strip().strip('"`')
        cost_events.append(ev)

# --- Count planning cost_events (frequency only; tokens rarely known) ---
plan = defaultdict(int)
for ev in cost_events:
    role, model = ev.get("role"), ev.get("model")
    if role and model:
        plan[(role, model)] += 1

def est(model, tin, tout):
    p = price_for(model)
    if not p or (tin == 0 and tout == 0): return None
    return tin/1e6*p[0] + tout/1e6*p[1]

# --- Weekly burn proxy for subscription backends (codex weekly cap; claude 5h windows) ---
# Codex exposes no in-band quota figure: tokens (esp. reasoning) are a PROXY for weekly-cap
# burn. Reconcile against the ChatGPT usage UI — this report paces, it is not authoritative.
weekly = defaultdict(lambda: {"runs":0,"in":0,"out":0,"reason":0,"cache":0})
if os.path.exists(cost_jsonl):
    import datetime
    for line in open(cost_jsonl):
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except json.JSONDecodeError: continue
        be = r.get("backend")
        if be not in ("codex", "claude"): continue
        try:
            wk = datetime.datetime.fromisoformat(r["ts"].replace("Z","+00:00")).strftime("%G-W%V")
        except Exception:
            wk = "?"
        w = weekly[(be, wk)]
        w["runs"] += 1
        for k, f in (("in","input_tokens"),("out","output_tokens"),
                     ("reason","reasoning_tokens"),("cache","cache_read_tokens")):
            if isinstance(r.get(f), int): w[k] += r[f]

print("=== Subscription-backend weekly burn (PROXY — reconcile vs provider usage UI) ===")
if not weekly:
    print("  (no codex/claude dispatch records yet)")
for (be, wk), w in sorted(weekly.items(), key=lambda kv: (kv[0][1], kv[0][0])):
    print(f"  {wk}  {be:7s} runs={w['runs']}  in={w['in']:,}  out={w['out']:,}  "
          f"reasoning={w['reason']:,}  cache={w['cache']:,}")
print()

# --- Burn-gate audit: every gpt-5.6-sol@high dispatch must log the burn-proxy it consulted ---
# Rule (MODELS.md § codex_weekly_burn_threshold, state.md § burn-gate audit): an @high
# dispatch whose cost_event carries no burn_proxy figure is an auditable violation.
def is_high(model): return model and "sol@high" in model.lower()
def has_burn(ev):
    v = ev.get("burn_proxy", "")
    return v not in ("", "null", "None", "~")

high_dispatches = []  # (task_id, ts)
if os.path.exists(cost_jsonl):
    for line in open(cost_jsonl):
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except json.JSONDecodeError: continue
        if is_high(r.get("model")):
            high_dispatches.append((r.get("task_id"), r.get("ts", "?")))

evidence_task_ids = {ev.get("task_id") for ev in cost_events
                     if is_high(ev.get("model")) and has_burn(ev)}

print("=== Burn-gate audit (codex sol@high) ===")
if not high_dispatches:
    print("  (no sol@high dispatches — nothing to audit)")
else:
    violations = [(tid, ts) for (tid, ts) in high_dispatches
                  if tid not in evidence_task_ids]
    ok = len(high_dispatches) - len(violations)
    print(f"  sol@high dispatches: {len(high_dispatches)}  with logged burn_proxy: {ok}")
    for tid, ts in violations:
        print(f"  ⚠ VIOLATION: sol@high dispatch task_id={tid} ts={ts} — "
              f"no cost_event with a burn_proxy reading (see state.md burn-gate audit rule)")
print()

# [0h] Partner-session records (role=partner, written by the Stop hook) are NOT builder
# dispatches — they are the orchestrator's own burn. They must still land in the weekly-burn
# roll-up above, because that is what the claude_window / codex_weekly gates read, but
# listing them as builders would misattribute the framework's most expensive context.
print("=== Orchestrator (partner) sessions ===")
_partner = {k: v for k, v in agg.items() if v.get("role") == "partner"}
if not _partner:
    print("  (no partner records — is the Stop hook wired? see setup.sh)")
for k, a in sorted(_partner.items()):
    print(f"  {k}: sessions={a['runs']}  in={a['in']:,}  out={a['out']:,}  cache={a['cache']:,}")
print()

print("=== Builder dispatches (from logs/cost.jsonl) ===")
if not agg:
    print("  (no records yet)")
total = 0.0
for model, a in sorted(agg.items()):
    if a.get("role") == "partner":
        continue          # [0h] reported above under Orchestrator (partner) sessions
    # Subscription backends (codex/claude) have no per-token spend — don't fake a $ figure.
    if model.startswith(("codex: ", "claude: ")):
        d, dollar = None, "(subscription — see weekly burn)"
    # Prefer the actual billed cost (recorded from opencode's session store) over a
    # price-table estimate; estimate only fills in for records that predate cost_usd.
    elif a["costed"] == a["runs"] and a["runs"] > 0:
        d, src = a["cost"], "actual"
        dollar = f"${d:,.4f} ({src})"
    else:
        e = est(model.split(": ", 1)[-1], a["in"], a["out"])
        d, src = ((a["cost"] + (e or 0)) or None), "actual+est" if a["costed"] else "est"
        dollar = f"${d:,.4f} ({src})" if d is not None else "(tokens/price n/a)"
    if d: total += d
    print(f"  {model}")
    print(f"     runs={a['runs']}  verify-fails={a['fails']}  attempts={a['attempts']}  "
          f"wall={a['dur']}s  in={a['in']:,}  out={a['out']:,}  cache={a['cache']:,}  {dollar}")
if total:
    print(f"  ── Metered (OpenRouter) total: ${total:,.4f}")

print("\n=== Primary reliability (per originally-requested primary model) ===")
print("  rescued = primary needed a same-model stall retry and/or a fallback switch.")
print("  A high rescue rate on a non-outage window is the signal to demote a tier primary.")
if not prim:
    print("  (no records carry primary_model yet — pre-dates the telemetry fix; will populate")
    print("   as new dispatches run through dispatch.sh)")
for model, p in sorted(prim.items()):
    rate = (100.0 * p["rescued"] / p["runs"]) if p["runs"] else 0.0
    print(f"  {model}")
    print(f"     dispatches={p['runs']}  rescued={p['rescued']} ({rate:.0f}%)  "
          f"[stall-retried={p['stall_retried']}  fell-back={p['fell_back']}]")

print("\n=== Planning / PM agent spawns (from TASK_LOG cost_event) ===")
if not plan:
    print("  (no cost_event entries — see state.md to enable PM-side logging)")
for (role, model), n in sorted(plan.items()):
    print(f"  {role:10s} {model:14s} ×{n}")

# --- Agent tool spawns recorded mechanically by the PostToolUse hook ---
spawns = defaultdict(int)
spawn_log = os.path.join(os.path.dirname(cost_jsonl), "agent-spawns.jsonl")
if os.path.exists(spawn_log):
    for line in open(spawn_log):
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except json.JSONDecodeError: continue
        spawns[(r.get("subagent_type") or "?", r.get("model") or "(inherited)")] += 1

print("\n=== Agent tool spawns (from logs/agent-spawns.jsonl, hook-recorded) ===")
if not spawns:
    print("  (no records — hook not wired or no spawns yet; see setup.sh hooks block)")
for (stype, model), n in sorted(spawns.items()):
    print(f"  {stype:18s} {model:14s} ×{n}")
PY
