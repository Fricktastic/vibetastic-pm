#!/usr/bin/env python3
"""Every ``<file>.md § <Section>`` reference in the framework must resolve (issues #34, #35).

Rules, prompts and scripts point at each other by section: "VERIFY.md § Who runs what",
"`.claude/rules/dispatch.md` § Round caps". A renamed heading silently turns such a pointer
into a dead end, and a reader (human or model) who follows it finds nothing — the same
quiet failure as a check that cannot fail. This makes the references checkable: each one
must name a framework file that exists and a heading in it that the reference's leading words
match (the first up to three words, case-insensitive; a reference may run on into prose).

References to project-owned files (PROJECT.md, HANDOFF.md, SPEC.md, ...) are skipped: their
sections live in each deployment, not here.

Usage: check-refs.py [--root DIR] [FILE ...]      (default: the framework's own text files)
Exit:  0 all resolve; 1 at least one dangling reference.
"""
import argparse
from pathlib import Path
import re
import sys

PROJECT_OWNED = {'PROJECT.md', 'HANDOFF.md', 'CLAUDE.md', 'AGENTS.md', 'SPEC.md', 'PLAN.md',
                 'TASK_LOG.md', 'PROPOSALS.md'}
EXCLUDED_DIRS = ('Docs/design-history/', 'Docs/implementation/', 'tests/fixtures/')
# A reference may wrap onto the next line on either side of the section sign; comment leaders
# and quote markers at the start of the continuation line are skipped.
# The section is read by lookahead so that the match ends at the section sign and a second
# reference later on the same line is still found.
REFERENCE = re.compile(r'`?(?P<path>[A-Za-z0-9_./-]*[A-Za-z0-9_-]\.md)`?[ \t]*(?:\n[ \t#>]*)?§'
                       r'(?=[ \t]*(?:\n[ \t#>]*)?`?(?P<section>[^\n]{1,80}))')
SECTION_END = re.compile(r'[`().;,:|"*\[\]]| — | - |\s§|\s{2}')


def words(text):
    text = re.sub(r'[`*_]', '', text).lower()
    return [w for w in re.split(r'\s+', text) if w and re.search(r'[a-z0-9]', w)]


def anchor_words(title):
    """A heading's name: parentheticals and an em-dash subtitle are not part of it."""
    title = re.sub(r'\([^)]*\)', ' ', title)
    return words(re.split(r'\s—\s', title, maxsplit=1)[0])


def headings(path):
    """Markdown headings, plus bold lead-ins (``**codex + iOS/Xcode tasks**``) that rules
    files use as sub-section anchors. Fenced code is skipped (it holds sample headings)."""
    found, fenced = [], False
    for line in path.read_text(errors='replace').splitlines():
        if line.lstrip().startswith('```'):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = (re.match(r'^#{1,6}\s+(.*?)\s*#*\s*$', line)
                 or re.match(r'^\s*(?:[-*]\s+)?\*\*(.+?)\*\*', line))
        if match:
            found.append(anchor_words(match.group(1)))
    return found


def default_files(root):
    patterns = ('*.md', '*.sh', 'prompts/*.md', 'scripts/*.py', 'scripts/*.sh',
                '.claude/rules/*.md', 'Docs/**/*.md')
    files = set()
    for pattern in patterns:
        files.update(p for p in root.glob(pattern) if p.is_file())
    return sorted(p for p in files
                  if not any(str(p.relative_to(root)).startswith(d) for d in EXCLUDED_DIRS)
                  and p.name not in PROJECT_OWNED)


def resolve(root, ref_path, index):
    candidate = ref_path[len('framework/'):] if ref_path.startswith('framework/') else ref_path
    if (root / candidate).is_file():
        return root / candidate
    matches = index.get(Path(candidate).name, [])
    return matches[0] if len(matches) == 1 else None


def check(root, files):
    index = {}
    for path in default_files(root):
        index.setdefault(path.name, []).append(path)
    problems, count = [], 0
    for path in files:
        text = path.read_text(errors='replace')
        for match in REFERENCE.finditer(text):
            if Path(match.group('path')).name in PROJECT_OWNED:
                continue
            count += 1
            section = SECTION_END.split(match.group('section'), 1)[0].strip()
            ref_words = words(section)[:3]
            where = f'{path.relative_to(root)}:{text.count(chr(10), 0, match.start()) + 1}'
            target = resolve(root, match.group('path'), index)
            if target is None:
                problems.append(f'{where}: {match.group("path")} is not a framework file')
            elif not ref_words:
                problems.append(f'{where}: empty section after § in {match.group(0)!r}')
            elif not any(h and h[:len(ref_words)] == ref_words[:len(h)] for h in headings(target)):
                # The heading's leading words and the reference's must agree for as many
                # words as the shorter of the two has (up to three): a reference may run
                # on into prose, and a heading may carry a subtitle.
                problems.append(f'{where}: {target.relative_to(root)} has no heading '
                                f'matching "§ {section}"')
    return problems, count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('files', nargs='*')
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    files = [Path(f).resolve() for f in args.files] if args.files else default_files(root)
    problems, count = check(root, files)
    for problem in problems:
        print('check-refs: ' + problem, file=sys.stderr)
    print(f'check-refs: {count} section reference(s), {len(problems)} dangling')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
