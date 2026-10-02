#!/usr/bin/env python3
"""Ground-truth checker for one field-test worktree (issue #69).

Independent of the builder-visible verifier: computes the expected tier map from MODELS.md
itself, then runs the candidate's scripts/tier-table.py on the real file and on mutated
copies. Prints one JSON line {worktree, passed, total, failures}.
"""
import json, re, subprocess, sys, tempfile, pathlib

wt = pathlib.Path(sys.argv[1]).resolve()
models = (wt / "MODELS.md").read_text()
script = wt / "scripts" / "tier-table.py"
TIERS = ("fast", "standard", "heavy")


def section_rows(text, heading):
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == heading)
    depth = heading.count("#")
    rows = {}
    for l in lines[start + 1:]:
        if l.startswith("#") and len(l) - len(l.lstrip("#")) <= depth:
            break
        cells = [c.strip() for c in l.strip().strip("|").split("|")]
        if len(cells) >= 2:
            t = re.match(r"`([^`]+)`", cells[0])
            if t and t.group(1) in TIERS:
                slugs = [re.match(r"`([^`]+)`", c) for c in cells[1:3]]
                rows[t.group(1)] = [s.group(1) if s else None for s in slugs] + [None]
    return rows


def expected(text):
    out = {"codex": {}, "opencode": {}, "claude": {}}
    for t, (m, _, _) in section_rows(text, "### Codex tier column").items():
        base, _, eff = m.partition("@")
        out["codex"][t] = {"model": base, "effort": eff or None, "fallback": None}
    for t, (m, fb, _) in section_rows(text, "## OpenCode Tiers").items():
        out["opencode"][t] = {"model": m, "effort": None, "fallback": fb}
    for t in TIERS:
        out["claude"][t] = {"model": "opus" if t == "heavy" else "sonnet", "effort": None, "fallback": None}
    return out


def run(*args):
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, timeout=30)


failures, total = [], 0


def check(name, ok):
    global total
    total += 1
    if not ok:
        failures.append(name)


if not script.exists():
    print(json.dumps({"worktree": str(wt), "passed": 0, "total": 1, "failures": ["no script"]}))
    sys.exit(0)

exp = expected(models)
r = run()
try:
    got = json.loads(r.stdout)
except Exception:
    got = None
check("real MODELS.md: exit 0", r.returncode == 0)
check("real MODELS.md: full map matches", got == exp)
check("real MODELS.md: key order", got is not None and list(got) == ["codex", "opencode", "claude"])
for b in exp:
    for t in TIERS:
        r = run("--tier", b, t)
        check(f"--tier {b} {t}", r.returncode == 0 and r.stdout.strip() == exp[b][t]["model"])
check("--tier unknown backend exits 1", run("--tier", "nope", "fast").returncode == 1)
check("--tier unknown tier exits 1", run("--tier", "codex", "ultra").returncode == 1)

with tempfile.TemporaryDirectory() as d:
    # Mutation 1: codex heading removed -> exit 1.
    p = pathlib.Path(d, "no-codex.md")
    p.write_text(models.replace("### Codex tier column", "### Codex tiers (renamed)"))
    check("missing codex section exits 1", run(str(p)).returncode == 1)
    # Mutation 2: Previous Tier Models table moved ABOVE the live sections -> must be ignored.
    prev = models[models.index("## Previous Tier Models"):]
    p = pathlib.Path(d, "prev-first.md")
    p.write_text(prev + "\n\n" + models[:models.index("## Previous Tier Models")])
    r = run(str(p))
    try:
        check("Previous Tier Models ignored when first", json.loads(r.stdout) == exp)
    except Exception:
        check("Previous Tier Models ignored when first", False)
    # Mutation 3: unreadable path -> exit 1.
    check("unreadable file exits 1", run(str(pathlib.Path(d, "absent.md"))).returncode == 1)

# Changed files: exactly the one new script.
# Committed or not (codex's sandbox cannot write the worktree index), vs main.
st = subprocess.run(["git", "-C", str(wt), "status", "--porcelain", "-uall"],
                    capture_output=True, text=True).stdout.splitlines()
diff = sorted(set(l[3:] for l in st) | set(subprocess.run(
    ["git", "-C", str(wt), "diff", "--name-only", "main...HEAD"],
    capture_output=True, text=True).stdout.split()))
check("only scripts/tier-table.py changed", diff == ["scripts/tier-table.py"])

print(json.dumps({"worktree": str(wt), "passed": total - len(failures), "total": total, "failures": failures}))
