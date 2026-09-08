#!/usr/bin/env python3
"""Durably append one JSON cost record using the framework's shared journal lock."""

import argparse
import json
import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import partner_telemetry


def note(log_dir, message):
    try:
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "telemetry-errors.log")
        with open(path, "a") as error_fh:
            error_fh.write("%s append-cost: %s\n" % (partner_telemetry.now(), message))
            error_fh.flush()
            os.fsync(error_fh.fileno())
    except Exception:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_dir", help="directory containing cost.jsonl")
    args = parser.parse_args(argv)
    try:
        record = json.load(sys.stdin)
    except Exception as exc:
        note(args.log_dir, "stdin was not JSON: %s" % exc)
        return 2
    if not isinstance(record, dict):
        note(args.log_dir, "cost record must be a JSON object")
        return 2
    try:
        diagnostics = partner_telemetry.append_journal_record(args.log_dir, record)
    except Exception as exc:
        note(args.log_dir, "could not append to cost.jsonl: %s" % exc)
        return 2
    for diagnostic in diagnostics:
        note(args.log_dir, diagnostic)
    return 0


if __name__ == "__main__":
    sys.exit(main())
