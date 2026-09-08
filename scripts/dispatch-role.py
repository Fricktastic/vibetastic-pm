#!/usr/bin/env python3
"""Dispatch a planning role, retain raw output, and register artifacts without body round-trips."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid


def yaml_block(text):
    match = re.search(r'```(?:yaml)?\s*\n(.*?)\n```', text, re.S)
    if not match or len(match.group(1).splitlines()) > 60:
        raise ValueError('missing or oversized result metadata')
    return match.group(1)


def metadata(reply, role, staged, promoted):
    start = '<!-- ' + role + '_RESULT_START -->'
    end = '<!-- ' + role + '_RESULT_END -->'
    if reply.count(start) != 1 or reply.count(end) != 1:
        raise ValueError('missing or duplicate role result delimiter')
    section = reply.split(start, 1)[1].split(end, 1)[0]
    result = yaml_block(section)
    path = re.search(r'^spec_path:\s*[\"\']?([^\n\"\']+)', result, re.M)
    if not path or path.group(1).strip() != staged:
        raise ValueError('Tech Lead metadata does not identify its staged spec')
    for field in ('task_title', 'suggested_tier', 'security'):
        if not re.search(r'^' + field + r':\s*\S', result, re.M):
            raise ValueError('missing Tech Lead metadata: ' + field)
    result = re.sub(r'^spec_path:.*$', 'spec_path: ' + json.dumps(promoted), result, flags=re.M)
    return '```yaml\n' + result + '\n```\n'


def architect_result(reply):
    delimiter = '<!-- ARCHITECT_RESULT_START -->'
    if reply.count(delimiter) != 1:
        raise ValueError('missing or duplicate Architect result delimiter')
    body, result = reply.split(delimiter)
    if not body.strip():
        raise ValueError('empty Architect spec')
    return body, '```yaml\n' + yaml_block(result) + '\n```\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pm-dir', default=os.environ.get('PM_DIR', '.'))
    parser.add_argument('--role', choices=['designer', 'architect', 'tech-lead'], required=True)
    parser.add_argument('--code-dir', required=True)
    parser.add_argument('--prompt', type=Path, required=True, help='rendered role prompt; leave SPEC_OUTPUT_PATH for the Tech Lead wrapper')
    parser.add_argument('--output', required=True, help='relative prompts/ artifact path')
    parser.add_argument('--backend', choices=['claude','codex','opencode'], required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--fallback-model', default='')
    parser.add_argument('--tier', default='standard', choices=['fast','standard','heavy'])
    args = parser.parse_args()
    from pm_state import PMState, StateError
    pm = Path(args.pm_dir).resolve()
    state = PMState(pm)
    token = os.environ.get('PM_ORCHESTRATOR_TOKEN')
    operation = 'role-' + str(uuid.uuid4())
    try:
        state.check(token)
        # Reject invalid destinations before dispatching paid work.
        if not args.output.startswith('prompts/'):
            raise ValueError('--output must be a relative prompts/ artifact')
        state._target(args.output)
        log_dir = pm/'logs'
        log_dir.mkdir(exist_ok=True)
        report = log_dir/(operation + '.report.md')
        # A narrow writable staging root; durable PM state is never a builder grant.
        with tempfile.TemporaryDirectory(prefix='pm-role-') as temporary:
            stage = Path(temporary)
            spec = stage/'spec.md'
            prompt = stage/(operation+'.md')
            rendered = args.prompt.read_text()
            if args.role == 'tech-lead':
                if '{{SPEC_OUTPUT_PATH}}' not in rendered:
                    raise ValueError('Tech Lead prompt must retain {{SPEC_OUTPUT_PATH}} for isolated staging')
                rendered = rendered.replace('{{SPEC_OUTPUT_PATH}}', str(spec))
            prompt.write_text(rendered)
            env = {**os.environ, 'PM_DIR': str(pm), 'OPENCODE_DISPATCH_LOG_DIR': str(log_dir)}
            extra = env.get('CODEX_EXTRA_WRITABLE_ROOTS', '')
            env['CODEX_EXTRA_WRITABLE_ROOTS'] = str(stage) + (':' + extra if extra else '')
            command = ['bash',str(Path(__file__).resolve().parents[1]/'dispatch.sh'), '--read-only', '--role', args.role,
                       '--backend',args.backend,args.model,args.code_dir,str(prompt),args.fallback_model,'','1',args.tier]
            with report.open('w') as output:
                result = subprocess.run(command, env=env, stdout=output)
            if result.returncode:
                print(f'Role failed (exit {result.returncode}); report retained at {report}',file=sys.stderr)
                return result.returncode
            reply = report.read_text()
            if args.role == 'tech-lead':
                returned = metadata(reply,'TECH_LEAD',str(spec),str(pm/args.output))
                if not spec.is_file() or not spec.read_text().strip():
                    raise ValueError('Tech Lead did not write a nonempty staged spec')
                body = spec.read_text()
            elif args.role == 'architect':
                body, returned = architect_result(reply)
            else:
                body, returned = reply, ''
                if not body.strip():
                    raise ValueError('Designer returned an empty spec')
            state.write(token,args.output,body,operation)
            print(json.dumps({'artifact':str(pm/args.output),'report':str(report),'operation_id':operation}))
            if returned:
                print(returned,end='')
            return 0
    except (ValueError,OSError,StateError) as exc:
        print('[dispatch-role] '+str(exc),file=sys.stderr)
        return 31

if __name__ == '__main__':
    sys.exit(main())
