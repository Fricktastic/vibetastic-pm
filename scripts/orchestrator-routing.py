#!/usr/bin/env python3
"""Session routing policy. Exit 31 is a policy stop, never a builder failure."""
import argparse
import json
import re
import sys
from pathlib import Path

ROLES = ('build', 'designer', 'tech-lead', 'architect', 'investigator', 'reviewer', 'critic', 'adjudicator')


def family(model):
    name = model.lower().removeprefix('openrouter/').split('@')[0]
    if name.startswith(('gpt-', 'codex-', 'openai/')):
        return 'openai'
    if name in ('sonnet', 'opus', 'haiku', 'fable') or name.startswith(('claude-', 'anthropic/')):
        return 'anthropic'
    for prefix, group in [('deepseek', 'deepseek'), ('z-ai/', 'z-ai'), ('glm-', 'z-ai'),
                          ('qwen', 'qwen'), ('minimax', 'minimax'), ('moonshotai/', 'moonshot'),
                          ('kimi-', 'moonshot'), ('google/', 'google'), ('gemini-', 'google'),
                          ('mistral', 'mistral'), ('x-ai/', 'x-ai'), ('grok-', 'x-ai')]:
        if name.startswith(prefix):
            return group
    return None


def validate(profile, backend, model, fallback_model='', role='build', author_model='',
             security=False, exceptional_adjudication=False, tier=''):
    models = [m for m in (model, fallback_model) if m]
    if not models:
        raise ValueError('model is required')
    for candidate in models:
        group = family(candidate)
        if backend == 'opencode' and group == 'anthropic':
            raise ValueError('Anthropic models must use subscription Claude, never OpenRouter')
        if backend == 'codex' and group != 'openai':
            raise ValueError('Codex backend requires an OpenAI model')
        if backend == 'claude' and group != 'anthropic':
            raise ValueError('Claude backend requires an Anthropic model')
    exception = exceptional_adjudication and role in ('reviewer', 'adjudicator') and backend == 'claude'
    if profile == 'codex-fallback' and backend != 'opencode' and not exception:
        raise ValueError('codex-fallback reserves subscription lanes; use OpenCode or explicit exceptional review/adjudication')
    if role in ('reviewer', 'critic'):
        author = family(author_model)
        if not author:
            raise ValueError('review/critique requires a known author model family')
        if any(not family(m) or family(m) == author for m in models):
            raise ValueError('primary AND fallback reviewer/critic must be family-diverse from the author')
    if role == 'adjudicator' and security:
        if backend != 'claude' or any('opus' not in m.lower() for m in models):
            raise ValueError('security adjudication requires Opus; remain blocked if unavailable')
    if role == 'reviewer' and security:
        if backend != 'claude' or any(not any(n in m.lower() for n in ('sonnet', 'opus')) for m in models):
            raise ValueError('security first-pass review requires Sonnet/Opus; OpenRouter is supplemental only')
    if role == 'critic' and security and (tier != 'heavy' or any('fable' in m.lower() for m in models)):
        raise ValueError('security critique requires a family-diverse heavy rung')


def backend_order(project, profile, role):
    if profile == 'codex-fallback':
        return ['opencode']
    text = Path(project).read_text()
    field = {'reviewer': 'reviewer_backends', 'critic': 'critic_backends'}.get(role, 'builder_backends')
    fm = re.match(r'\A---\n(.*?)\n---', text, re.S)
    match = re.search(r'^' + field + r':\s*\[([^\]]*)\]', fm.group(1) if fm else '', re.M)
    values = [x.strip().strip('\"\'') for x in match.group(1).split(',')] if match else []
    if not values or any(x not in ('claude', 'codex', 'opencode') for x in values):
        raise ValueError('missing or invalid ' + field + ' in PROJECT.md')
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check', 'order'])
    parser.add_argument('--profile', choices=['normal', 'codex-fallback'], default='normal')
    parser.add_argument('--role', choices=ROLES, required=True)
    parser.add_argument('--backend', choices=['claude', 'codex', 'opencode'])
    parser.add_argument('--model', default='')
    parser.add_argument('--fallback-model', default='')
    parser.add_argument('--author-model', default='')
    parser.add_argument('--tier', default='')
    parser.add_argument('--security', action='store_true')
    parser.add_argument('--exceptional-adjudication', action='store_true')
    parser.add_argument('--project')
    args = parser.parse_args()
    try:
        if args.command == 'order':
            print(json.dumps(backend_order(args.project, args.profile, args.role)))
        else:
            if not args.backend:
                raise ValueError('--backend is required')
            validate(args.profile, args.backend, args.model, args.fallback_model, args.role,
                     args.author_model, args.security, args.exceptional_adjudication, args.tier)
            print(json.dumps({'allowed': True, 'profile': args.profile, 'role': args.role}))
    except (ValueError, OSError, TypeError) as exc:
        print('[orchestrator-routing] ' + str(exc), file=sys.stderr)
        return 31
    return 0

if __name__ == '__main__':
    sys.exit(main())
