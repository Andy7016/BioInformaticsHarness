#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read Harness identities and check a non-destructive study adoption delta.

Never pulls, merges, copies .git or edits research. CLI creates new receipts.
Structural checks leave scientific acceptance and semantic reading unassessed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from scripts.research_support._common import (  # noqa: E402
    SupportError, _read_bounded, dump_new, fingerprint, load_json_with_identity, utc_now,
)

SNAPSHOT = 'harness.guidance-snapshot/v1'
PLAN = 'harness.guidance-update-plan/v1'
RECEIPT = 'harness.guidance-adoption/v1'
REPOSITORY = 'https://github.com/vvcchh0/BioInformaticsHarness'


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _id(value, key):
    return _digest({k: v for k, v in value.items() if k not in {key, 'observed_at_utc'}})


def _assert(condition, message):
    if not condition:
        raise SupportError(message, 'fail')


def _rel(value):
    _assert(isinstance(value, str) and bool(value), 'empty relative path')
    p = PurePosixPath(value)
    _assert(not p.is_absolute() and str(p) == value and '\\' not in value
            and not any(x in {'.', '..', ''} or any(c in x for c in ':\x00<>"|?*')
                        or any(ord(c) < 32 for c in x) or x.endswith(('.', ' '))
                        or x.lower() == '.git'
                        or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', x)
                        for x in value.split('/')), 'unsafe path')
    return value


def _path(root, relative):
    root = Path(root).resolve(strict=True)
    p = root
    for part in _rel(relative).split('/'):
        p = p / part
        if p.exists() or p.is_symlink():
            info = p.lstat()
            _assert(not p.is_symlink() and not (getattr(info, 'st_file_attributes', 0) & 0x400),
                    'links and junctions are not payload paths')
            canonical = p.resolve(strict=True)
            _assert(canonical.is_relative_to(root), 'canonical path escapes root')
            _assert(not any(x.casefold() == '.git' for x in canonical.relative_to(root).parts),
                    'canonical path aliases protected Git metadata')
    _assert(p.resolve(strict=False).is_relative_to(root), 'path escapes root')
    return p


def _identity(entry):
    n, h = entry.get('bytes'), entry.get('sha256')
    _assert(type(n) is int and n >= 0 and isinstance(h, str) and
            re.fullmatch('[0-9a-f]{64}', h), 'invalid file identity')
    return {'bytes': n, 'sha256': h}


def _git(root, *args, timeout_seconds=30):
    try:
        result = subprocess.run(['git', '--no-optional-locks', '-c', f'safe.directory={root}',
                                 '-c', 'core.fsmonitor=false', '-C', str(root), *args],
                                capture_output=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise SupportError('local Git metadata query timed out; source identity remains unresolved', 'unknown') from exc
    except OSError as exc:
        raise SupportError(f'local Git query could not start: {type(exc).__name__}', 'unknown') from exc
    if result.returncode:
        raise SupportError('local Git metadata query failed; source identity remains unresolved', 'unknown')
    return result.stdout


def _source(root, legacy, git_timeout_seconds=30):
    def query(*args):
        return _git(root, *args, timeout_seconds=git_timeout_seconds)
    if legacy or not (root / '.git').exists():
        return {'expected_repository': REPOSITORY, 'repository': None,
                'kind': 'legacy_snapshot' if legacy else 'gitless_snapshot',
                'git_available': False, 'commit': None, 'dirty': None, 'exact_commit': False}
    top = Path(os.fsdecode(query('rev-parse', '--show-toplevel').strip())).resolve()
    _assert(top == root.resolve(), 'Git root differs from payload root')
    status = query('status', '--porcelain=v1', '-z', '--untracked-files=all')
    urls = [query('remote', 'get-url', '--', name).decode().strip()
            for name in query('remote').decode().splitlines()]
    def matches(url):
        url = url.rstrip('/')
        if url.endswith('.git'):
            url = url[:-4]
        return url.casefold() in {REPOSITORY.casefold(), 'git@github.com:vvcchh0/bioinformaticsharness'}
    upstream = any(matches(url) for url in urls)
    local_commit = query('rev-parse', 'HEAD').decode().strip()
    return {'expected_repository': REPOSITORY, 'repository': REPOSITORY if upstream else None,
            'kind': 'git_snapshot', 'git_available': True,
            'commit': local_commit if upstream else None, 'local_commit': local_commit,
            'tree': query('rev-parse', 'HEAD^{tree}').decode().strip(),
            'dirty': bool(status), 'status_digest': hashlib.sha256(status).hexdigest(),
            'upstream_remote_declared': upstream,
            'provenance_basis': 'local Git and configured remote; not an authenticated upstream release',
            'exact_commit': False}


MATERIAL_DIRS = ('core/docs/methods/', 'core/scripts/', 'core/machine/design/',
                 'machine/schemas/', 'tools/')
MATERIAL_FILES = {'AGENTS.md', 'README.md', 'CHANGELOG.md', 'docs/harness-updates.md',
                  'machine/AGENT-INSTRUCTIONS.md', 'machine/guidance-update-contract.json',
                  'machine/time-budget-recovery-contract.json'}


def _material(path):
    return path.startswith(MATERIAL_DIRS) or path in MATERIAL_FILES


def _check_coverage(payload, files):
    payload = Path(payload)
    for directory in [*MATERIAL_DIRS, 'machine/changes/']:
        area = payload / directory
        if area.is_dir():
            for p in area.rglob('*'):
                if p.is_file() and '__pycache__' not in p.parts:
                    _assert(p.relative_to(payload).as_posix() in files, 'unlisted guidance: refresh inventory')
    for relative in MATERIAL_FILES:
        if (payload / relative).exists():
            _assert(relative in files, 'unlisted top-level guidance: refresh inventory')


def _note(value, path, files):
    for field in ['id', 'summary', 'impact', 'revisit', 'acceptance']:
        _assert(isinstance(value.get(field), str) and bool(value[field].strip()),
                f'change note missing {field}')
    _assert(value['impact'] in {'guidance', 'interface', 'editorial', 'scientific_correction'}, 'bad impact')
    for field in ['paths', 'reading_paths']:
        _assert(isinstance(value.get(field), list) and bool(value[field]), f'note missing {field}')
        for item in value[field]:
            _rel(item)
    return {**value, 'note_path': path}


def capture(root, inventory='CHECKPOINT-MANIFEST.json', payload_prefix='', legacy=False,
            git_timeout_seconds=30, git_timeout_reason=None):
    """Rehash a declared checkpoint/legacy inventory; never infer legacy commits."""
    root = Path(root).resolve(strict=True)
    _assert(type(git_timeout_seconds) in {int, float} and math.isfinite(git_timeout_seconds)
            and git_timeout_seconds > 0, 'Git query timeout must be positive and finite')
    _assert(git_timeout_reason is None or isinstance(git_timeout_reason, str) and git_timeout_reason.strip(),
            'Git query timeout reason must be nonempty when provided')
    def query(*args):
        return _git(payload, *args, timeout_seconds=git_timeout_seconds)
    payload = _path(root, payload_prefix).resolve(strict=True) if payload_prefix else root
    data, inv_identity = load_json_with_identity(_path(root, inventory), 4 * 1024 * 1024)
    expected = 'harness.current-guidance-freeze/v1' if legacy else 'bioinformatics-harness-public-checkpoint/v1'
    _assert(data.get('schema') == expected, 'unsupported inventory schema')
    source = _source(payload, legacy, git_timeout_seconds)
    entries = data.get('files')
    _assert(isinstance(entries, list) and 0 < len(entries) <= 10000, 'invalid inventory')
    files, blobs, folded, total = {}, {}, set(), 0
    inv_path = _path(root, inventory)
    inventory_in_payload = inv_path.relative_to(payload).as_posix() if inv_path.is_relative_to(payload) else None
    for entry in entries:
        relative = _rel(entry['path'])
        _assert(relative.casefold() not in folded, 'duplicate/case-colliding payload path')
        folded.add(relative.casefold())
        if relative == inventory_in_payload and entry.get('self_hashed') is False:
            _assert(entry.get('bytes') is None and entry.get('sha256') is None, 'invalid inventory self entry')
            continue
        total += _identity(entry)['bytes']
        _assert(total <= 128 * 1024 * 1024, 'payload exceeds capture byte budget')
        raw = _read_bounded(_path(payload, relative), 32 * 1024 * 1024)
        identity = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        _assert(identity == _identity(entry), f'inventory identity mismatch: {relative}')
        files[relative] = {'path': relative, **identity}
        blobs[relative] = raw
    if inv_path.is_relative_to(payload):
        relative = inv_path.relative_to(payload).as_posix()
        if relative not in files:
            total += inv_identity['bytes']
            _assert(total <= 128 * 1024 * 1024, 'payload exceeds capture byte budget')
            files[relative] = {'path': relative, **inv_identity}
            blobs[relative] = inv_path.read_bytes()
    _check_coverage(payload, files)
    notes = []
    for relative in sorted(files):
        if relative.startswith('machine/changes/') and relative.endswith('.json'):
            value, identity = load_json_with_identity(_path(payload, relative))
            _assert(identity == _identity(files[relative]), 'change note moved while reading')
            note = _note(value, relative, files)
            note['introduced_commit'] = None
            if source['git_available']:
                note['introduced_commit'] = query('log', '-1', '--format=%H', '--diff-filter=A',
                                                   source['local_commit'], '--', relative).decode().strip() or None
            notes.append(note)
    _assert(len({n['id'] for n in notes}) == len(notes), 'duplicate change ID')
    if source['git_available']:
        tree = {}
        for item in query('ls-tree', '-r', '-z', '--full-tree', 'HEAD').split(b'\0'):
            if item:
                header, relative = item.split(b'\t', 1)
                mode, kind, oid = header.split()
                tree[relative.decode('utf-8')] = (kind, oid.decode())
        algorithm = query('rev-parse', '--show-object-format').decode().strip()
        _assert(algorithm in {'sha1', 'sha256'}, 'unsupported Git object format')
        matched = all(tree.get(p) == (b'blob', hashlib.new(algorithm, b'blob ' + str(len(raw)).encode()
                                                         + b'\0' + raw).hexdigest()) for p, raw in blobs.items())
        source['exact_commit'] = source['upstream_remote_declared'] and not source['dirty'] and matched
        source['exact_local_commit'] = not source['dirty'] and matched
        source['payload_matches_commit_bytes'] = matched
    for relative, entry in files.items():
        _assert(fingerprint(_path(payload, relative)) == _identity(entry), 'payload changed during capture')
    after = _source(payload, legacy, git_timeout_seconds)
    _assert(all(after.get(k) == source.get(k) for k in ['commit', 'local_commit', 'dirty', 'status_digest',
                                                       'upstream_remote_declared']), 'Git changed during capture')
    result = {'schema': SNAPSHOT, 'observed_at_utc': utc_now(), 'source': source,
              'inventory_identity': inv_identity, 'files': [files[p] for p in sorted(files)],
              'payload_id': _digest([files[p] for p in sorted(files)]), 'change_notes': notes,
              'evidence_scope': 'declared payload, stable reads; no signature or semantic-reading proof',
              'observation_policy': {'git_query_timeout_seconds': git_timeout_seconds,
                                     'basis': git_timeout_reason or 'operational query guard; not research completion or authorization expiry'},
              'scientific_acceptance': 'not_assessed'}
    result['package_id'] = _id(result, 'package_id')
    return result


def _snapshot(value):
    _assert(value.get('schema') == SNAPSHOT and value.get('package_id') == _id(value, 'package_id'),
            'snapshot metadata identity mismatch')
    _assert(value.get('payload_id') == _digest(value.get('files')), 'payload identity mismatch')
    paths = [_rel(e['path']) for e in value['files']]
    _assert(len({p.casefold() for p in paths}) == len(paths), 'duplicate payload path')


def verify_snapshot(payload_root, snapshot):
    errors = []
    try:
        _snapshot(snapshot)
        for entry in snapshot['files']:
            _assert(fingerprint(_path(Path(payload_root), entry['path'])) == _identity(entry), 'received payload mismatch')
        _check_coverage(payload_root, {e['path'] for e in snapshot['files']})
    except (SupportError, KeyError, TypeError, OSError, ValueError) as exc:
        errors.append(str(exc))
    return {'overall': 'fail' if errors else 'pass', 'errors': errors,
            'source_commit_binding': 'source snapshot attestation; copied .git not required',
            'semantic_reading': 'not_assessed', 'scientific_acceptance': 'not_assessed'}


def make_plan(old, new, preserve=None):
    _snapshot(old); _snapshot(new)
    before = {e['path']: e for e in old['files']}
    after = {e['path']: e for e in new['files']}
    changes, reviews, unindexed = [], [], []
    for note in old['change_notes']:
        _assert(before.get(note['note_path']) == after.get(note['note_path']),
                'historical change notes are append-only; add a superseding note instead')
    active_notes = [n for n in new['change_notes']
                    if before.get(n['note_path']) != after.get(n['note_path'])]
    for path in sorted(before.keys() | after.keys()):
        if before.get(path) == after.get(path):
            continue
        kind = 'added' if path not in before else 'removed' if path not in after else 'modified'
        changes.append({'path': path, 'kind': kind, 'before': before.get(path), 'after': after.get(path)})
    targets = {c['path']: c['kind'] for c in changes}
    for note in active_notes:
        _assert(all(p in after for p in note['reading_paths']), 'active note reading target absent from new payload')
        for path in note['paths']:
            _assert(path in before or path in after, 'note target absent from both snapshots')
            targets.setdefault(path, 'reappraise')
    for path, kind in sorted(targets.items()):
        annotations = [n for n in active_notes if path in n['paths']]
        if _material(path) or annotations:
            indexed = bool(annotations)
            if not indexed:
                unindexed.append(path)
            reading_paths = {path} if path in after else set()
            for note in annotations:
                reading_paths.update(note['reading_paths'])
                reading_paths.add(note['note_path'])
            reviews.append({'id': 'review-' + _digest(path)[:16], 'path': path, 'kind': kind,
                            'reading_required': path in after, 'after': after.get(path),
                            'required_readings': [after[p] for p in sorted(reading_paths)],
                            'annotations': annotations, 'indexed': indexed,
                            'required_action': 'Read changed text; appraise current/next study impact; preserve old evidence.'})
    result = {'schema': PLAN, 'from_package_id': old['package_id'], 'to_package_id': new['package_id'],
              'from_source': old['source'], 'to_source': new['source'], 'changes': changes,
              'reviews': reviews, 'unindexed_paths': unindexed, 'preserve': preserve or [],
              'state': 'pending_study_appraisal', 'semantic_reading': 'not_assessed',
              'scientific_acceptance': 'not_assessed',
              'history_range': 'exact commits available' if old['source']['exact_commit'] and new['source']['exact_commit']
                               else 'file delta authoritative; legacy or working-tree provenance retained'}
    result['plan_id'] = _id(result, 'plan_id')
    return result


def check_adoption(study_root, plan, receipt):
    errors, pending, accepted_refs = [], [], []
    try:
        _assert(plan.get('schema') == PLAN and plan.get('plan_id') == _id(plan, 'plan_id'), 'plan identity mismatch')
        _assert(receipt.get('schema') == RECEIPT, 'bad adoption schema')
        for key in ['plan_id', 'from_package_id', 'to_package_id']:
            _assert(receipt.get(key) == plan[key], f'adoption {key} mismatch')
        for key in ['actor', 'scope']:
            _assert(isinstance(receipt.get(key), str) and receipt[key].strip(), f'missing {key}')
        for entry in plan['preserve']:
            _assert(fingerprint(_path(Path(study_root), entry['path'])) == _identity(entry), 'protected study baseline changed')
        decisions = receipt.get('decisions', [])
        _assert(isinstance(decisions, list), 'decisions must be a list')
        by_id = {d['review_id']: d for d in decisions}
        expected = {r['id'] for r in plan['reviews']}
        _assert(len(by_id) == len(decisions) and set(by_id) == expected, 'missing/extra/duplicate impact decisions')
        for review in plan['reviews']:
            decision = by_id[review['id']]
            disposition = decision.get('disposition')
            _assert(disposition in {'retain', 'revise', 'qualify', 'defer', 'not_applicable'}, 'bad disposition')
            _assert(isinstance(decision.get('reason'), str) and decision['reason'].strip(), 'missing impact reason')
            readings = decision.get('readings', [])
            _assert(isinstance(readings, list), 'readings must be a list')
            read_map = {_rel(r['path']): r['sha256'] for r in readings}
            required = {r['path']: r['sha256'] for r in review['required_readings']}
            _assert(len(read_map) == len(readings) and read_map == required,
                    'required reading identities missing, extra or mismatched')
            if disposition == 'defer':
                pending.append(review['id'])
            if disposition in {'revise', 'qualify'}:
                artifacts = decision.get('artifacts')
                _assert(isinstance(artifacts, list) and bool(artifacts), 'revision needs derived artifacts')
                for artifact in artifacts:
                    _assert(_rel(artifact['path']).casefold() not in
                            {x['path'].casefold() for x in plan['preserve']}, 'revision overwrites protected artifact')
                    _assert(fingerprint(_path(Path(study_root), artifact['path'])) == _identity(artifact), 'derived artifact mismatch')
                ref = decision.get('acceptance_ref')
                _assert(isinstance(ref, str) and bool(ref), 'revision needs acceptance reference')
                accepted_refs.append({'path': ref, **fingerprint(_path(Path(study_root), ref))})
    except (SupportError, KeyError, TypeError, OSError, ValueError) as exc:
        errors.append(str(exc))
    return {'schema': 'harness.guidance-adoption-check/v1', 'overall': 'fail' if errors else 'unknown' if pending else 'pass',
            'errors': errors, 'pending_reviews': pending, 'acceptance_references_observed': accepted_refs,
            'adoption_readiness': 'pending' if errors or pending else 'records_complete_for_declared_scope',
            'reading_evidence': 'actor-declared digest, not semantic-reading proof',
            'preservation_scope': 'explicit protected files only; no whole-project coverage claim',
            'scientific_acceptance': 'not_assessed', 'active_version_written': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-root', type=Path, required=True)
    parser.add_argument('--out', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('capture'); p.add_argument('--root', type=Path, required=True)
    p.add_argument('--inventory', default='CHECKPOINT-MANIFEST.json'); p.add_argument('--payload-prefix', default='')
    p.add_argument('--legacy', action='store_true')
    p.add_argument('--git-timeout-seconds', type=float, default=30)
    p.add_argument('--git-timeout-reason')
    p = sub.add_parser('verify'); p.add_argument('--root', type=Path, required=True); p.add_argument('--snapshot', required=True)
    p = sub.add_parser('plan'); p.add_argument('--old', required=True); p.add_argument('--new', required=True)
    p.add_argument('--preserve-manifest')
    p = sub.add_parser('check'); p.add_argument('--study-root', type=Path, required=True)
    p.add_argument('--plan', required=True); p.add_argument('--receipt', required=True)
    args = parser.parse_args(argv)
    target = None
    error_target_validated = False
    try:
        root = args.work_root.resolve(strict=True)
        target = _path(root, args.out)
        _assert(not target.exists(), 'output already exists')
        error_target_validated = args.command != 'capture'
        def load(name):
            return load_json_with_identity(_path(root, name), 8 * 1024 * 1024)[0]
        if args.command == 'capture':
            payload = _path(args.root, args.payload_prefix) if args.payload_prefix else args.root.resolve(strict=True)
            _assert(not target.is_relative_to(payload), 'capture output must be outside payload')
            error_target_validated = True
            result = capture(args.root, args.inventory, args.payload_prefix, args.legacy,
                             args.git_timeout_seconds, args.git_timeout_reason)
        elif args.command == 'verify':
            result = verify_snapshot(args.root, load(args.snapshot))
        elif args.command == 'plan':
            result = make_plan(load(args.old), load(args.new),
                               load(args.preserve_manifest)['files'] if args.preserve_manifest else None)
        else:
            result = check_adoption(args.study_root, load(args.plan), load(args.receipt))
        dump_new(target, result)
        status = result.get('overall', 'pass')
        print(json.dumps({'status': status, 'out': args.out}))
        return {'pass': 0, 'fail': 1, 'unknown': 2}.get(status, 2)
    except (SupportError, FileExistsError, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as exc:
        status = exc.status if isinstance(exc, SupportError) else 'unknown' if isinstance(exc, (OSError, KeyboardInterrupt)) else 'fail'
        if isinstance(exc, FileExistsError):
            status = 'fail'
        report = {'schema': 'harness.guidance-operation-error/v1', 'overall': status,
                  'error': str(exc) or type(exc).__name__, 'operation': args.command,
                  'interrupted': isinstance(exc, KeyboardInterrupt),
                  'adoption_readiness': 'pending', 'active_version_written': False,
                  'scientific_acceptance': 'not_assessed', 'automatic_retry': False,
                  'next_action': 'Resolve the affected query/input within existing authority; use a new output path for another attempt.'}
        if args.command == 'capture':
            report['observation_policy'] = {'git_query_timeout_seconds': args.git_timeout_seconds,
                                            'basis': args.git_timeout_reason or 'operational query guard'}
        written = False
        if error_target_validated and target is not None and not target.exists():
            try:
                dump_new(target, report)
                written = True
            except (SupportError, OSError, ValueError):
                pass
        print(json.dumps({'status': status, 'error': str(exc), 'error_receipt_written': written}))
        return 130 if isinstance(exc, KeyboardInterrupt) else 2 if status == 'unknown' else 1


if __name__ == '__main__':
    raise SystemExit(main())
