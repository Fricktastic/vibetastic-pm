#!/usr/bin/env python3
"""Project-owned review policy: verify-tier meanings, risk triggers and round caps (issue #50).

The framework owns the mechanisms (the critique gate, the round caps, the verdict ledger); the
project owns the policy those mechanisms apply. The policy lives in the project's own
PROJECT.md, which setup writes once and the installer never touches:

  * frontmatter keys (machine-enforced numbers, same flat style as ``reviewer_backends``):
        critic_round_cap: 2            # pre-build critique rounds per task
        reviewer_fixup_round_cap: 3    # reviewer-driven fixup rounds per task
  * body sections (policy text rendered into the Tech Lead / Architect / critic / reviewer
    prompts through the ``{{PROJECT_POLICY}}`` placeholder):
        ## Verify tiers        one bullet per tier: ``- R0: <what evidence proves this>``
        ## Risk triggers       one bullet per trigger that forces pre-build critique

Every part is optional. Anything absent falls back to the generic framework default below, so
a project that has never declared a policy behaves exactly like one that declared the
defaults. ``validate`` (used by orchestrator-doctor.py) reports malformed declarations; the
enforcement path never crashes on them — it uses the default and warns.

CLI:
    project_policy.py --pm-dir . show [--json]
    project_policy.py --pm-dir . validate          # exit 1 on errors
    project_policy.py --pm-dir . render            # markdown for {{PROJECT_POLICY}}
    project_policy.py --pm-dir . cap --role critic|reviewer
"""
import argparse
import json
from pathlib import Path
import re
import sys

TIERS = ('R0', 'R1', 'R2')
CAP_KEYS = {'critic': 'critic_round_cap', 'reviewer': 'reviewer_fixup_round_cap'}
DEFAULT_CAPS = {'critic_round_cap': 2, 'reviewer_fixup_round_cap': 3}

# Generic defaults describe KINDS OF EVIDENCE, not a platform. A project that needs
# domain wording (iOS views, simulators, audio ownership) declares it in its own PROJECT.md;
# Docs/examples/policy-ios.md is a copyable example.
DEFAULT_VERIFY_TIERS = {
    'R0': 'Logic evidence. The change is fully proven by compiling and running unit tests '
          'that exercise the changed logic; it crosses no external boundary.',
    'R1': 'Real-path integration evidence. The change crosses a boundary (serialization, '
          'network, persistence, IPC, configuration, a third-party API); prove it by pushing a '
          'representative real input through the production code path, not a mock of it.',
    'R2': 'Run-and-observe evidence. Correctness is only visible by running the product '
          '(rendered output, timing, runtime or device behaviour); prove it by running it, '
          'driving it to the state in question and observing the outcome.',
}
DEFAULT_RISK_TRIGGERS = [
    'Changes shared state, a shared writer, or an invariant other components rely on '
    '(blast radius beyond the edited files).',
    'Changes a persisted format, wire contract, schema or public API.',
    'Changes concurrency, ordering, timing or lifecycle behaviour.',
    'Leaves a design decision open that the builder would have to guess.',
]

FRONTMATTER = re.compile(r'\A---\n(.*?)\n---', re.S)
SECTION_TITLES = {'verify tiers': 'verify_tiers', 'risk triggers': 'risk_triggers'}


def _frontmatter_value(frontmatter, key):
    match = re.search(r'^' + re.escape(key) + r':[ \t]*([^\n#]*)', frontmatter, re.M)
    return match.group(1).strip().strip('"\'') if match else None


def _sections(body):
    """Map policy section key -> list of (bullet text) and count duplicate headings."""
    found, counts, current = {}, {}, None
    for line in body.splitlines():
        heading = re.match(r'^##\s+(.+?)\s*$', line)
        if heading:
            current = SECTION_TITLES.get(heading.group(1).strip().lower())
            if current:
                counts[current] = counts.get(current, 0) + 1
                found.setdefault(current, [])
            continue
        if current is None or line.strip().startswith('<!--'):
            continue
        bullet = re.match(r'^\s*[-*]\s+(.*\S)\s*$', line)
        if bullet:
            found[current].append(bullet.group(1))
        elif line.strip() and found[current] and line.startswith((' ', '\t')):
            found[current][-1] += ' ' + line.strip()   # wrapped continuation of a bullet
    return found, counts


def _strip_comments(text):
    return re.sub(r'<!--.*?-->', '', text, flags=re.S)


def load(pm_dir):
    """Return the effective policy plus the errors found while reading it."""
    policy = {
        **DEFAULT_CAPS,
        'verify_tiers': dict(DEFAULT_VERIFY_TIERS),
        'risk_triggers': list(DEFAULT_RISK_TRIGGERS),
        'sources': {key: 'default' for key in
                    ('critic_round_cap', 'reviewer_fixup_round_cap', 'verify_tiers', 'risk_triggers')},
        'errors': [],
    }
    path = Path(pm_dir) / 'PROJECT.md'
    try:
        text = path.read_text()
    except OSError:
        policy['sources']['project_md'] = 'missing'
        return policy
    errors = policy['errors']
    match = FRONTMATTER.match(text)
    frontmatter = match.group(1) if match else ''
    body = text[match.end():] if match else text
    for key in DEFAULT_CAPS:
        raw = _frontmatter_value(frontmatter, key)
        if raw is None or raw in ('', 'null', '~'):
            continue
        if not re.fullmatch(r'[0-9]+', raw) or int(raw) < 1:
            errors.append(f'{key} must be a positive integer, got {raw!r} (using default '
                          f'{DEFAULT_CAPS[key]})')
            continue
        policy[key] = int(raw)
        policy['sources'][key] = 'project'

    sections, counts = _sections(_strip_comments(body))
    for key, count in counts.items():
        if count > 1:
            errors.append(f'PROJECT.md declares the "{key.replace("_", " ")}" section {count} times')
    if 'verify_tiers' in sections:
        tiers, tier_errors = {}, []
        for item in sections['verify_tiers']:
            entry = re.match(r'^\**`?(R\d+)`?\**\s*[:—-]\s*(.+)$', item)
            if not entry:
                tier_errors.append(f'verify tier bullet is not "- R<n>: <definition>": {item!r}')
                continue
            tier, text_ = entry.group(1), entry.group(2).strip()
            if tier not in TIERS:
                tier_errors.append(f'unknown verify tier {tier} (the framework enforces {"/".join(TIERS)})')
            elif tier in tiers:
                tier_errors.append(f'verify tier {tier} is defined twice')
            else:
                tiers[tier] = text_
        missing = [tier for tier in TIERS if tier not in tiers]
        if missing:
            tier_errors.append('verify tiers section must define every tier; missing ' + ', '.join(missing))
        errors.extend(tier_errors)
        if not tier_errors:
            policy['verify_tiers'] = tiers
            policy['sources']['verify_tiers'] = 'project'
    if 'risk_triggers' in sections:
        if sections['risk_triggers']:
            policy['risk_triggers'] = sections['risk_triggers']
            policy['sources']['risk_triggers'] = 'project'
        else:
            errors.append('risk triggers section is present but lists no "- <trigger>" bullets')
    return policy


def validate(pm_dir):
    return load(pm_dir)['errors']


def cap(pm_dir, role):
    return load(pm_dir)[CAP_KEYS[role]]


def render(policy):
    """Markdown substituted for {{PROJECT_POLICY}} in role prompts."""
    source = lambda key: '' if policy['sources'][key] == 'project' else ' (framework default)'
    lines = [f'**Verify tiers — what evidence proves a change{source("verify_tiers")}:**', '']
    lines += [f'- **{tier}** — {policy["verify_tiers"][tier]}' for tier in TIERS]
    lines += ['', f'**Risk triggers — any one sets `risk: true` and forces pre-build critique'
                  f'{source("risk_triggers")}:**', '']
    lines += [f'- {trigger}' for trigger in policy['risk_triggers']]
    lines += ['- Anything that would set `security: true` (security always forces critique).', '',
              f'**Round caps:** {policy["critic_round_cap"]} pre-build critique round(s) and '
              f'{policy["reviewer_fixup_round_cap"]} reviewer fixup round(s) per task; '
              '`dispatch.sh` refuses more (exit 31) until the operator decides.']
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--pm-dir', default='.')
    sub = parser.add_subparsers(dest='command', required=True)
    show = sub.add_parser('show')
    show.add_argument('--json', action='store_true')
    sub.add_parser('validate')
    sub.add_parser('render')
    cap_parser = sub.add_parser('cap')
    cap_parser.add_argument('--role', choices=sorted(CAP_KEYS), required=True)
    args = parser.parse_args(argv)
    policy = load(args.pm_dir)
    if args.command == 'validate':
        for error in policy['errors']:
            print('project-policy: ' + error, file=sys.stderr)
        print('project policy: ' + ('ok' if not policy['errors'] else f'{len(policy["errors"])} error(s)'))
        return 1 if policy['errors'] else 0
    for error in policy['errors']:
        print('project-policy: warning: ' + error, file=sys.stderr)
    if args.command == 'cap':
        print(policy[CAP_KEYS[args.role]])
    elif args.command == 'render':
        sys.stdout.write(render(policy))
    elif args.json:
        print(json.dumps(policy, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render(policy))
    return 0


if __name__ == '__main__':
    sys.exit(main())
