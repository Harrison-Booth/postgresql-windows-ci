"""Retain each regression phase's logs before the next phase reuses test paths."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

parser = argparse.ArgumentParser()
parser.add_argument('--phase', choices=['default', 'jit-forced'], required=True)
parser.add_argument('--exit-status', type=int, required=True)
args = parser.parse_args()
workspace = Path.cwd().resolve()
test_root = workspace / 'postgres/build/testrun'
evidence = workspace / 'evidence'
if not test_root.is_dir() or not evidence.is_dir():
    raise RuntimeError('Existing owned test output and evidence required')
target = evidence / ('regression-' + args.phase + '-snapshot')
if target.exists():
    raise RuntimeError('Preserve existing regression snapshot; do not overwrite')
target.mkdir()
files = {}
for source in sorted(test_root.rglob('*')):
    if not source.is_file():
        continue
    relative = source.relative_to(test_root)
    if source.name not in ['regression.out', 'regression.diffs'] and 'log' not in relative.parts[:-1]:
        continue
    if source.is_symlink() or test_root not in source.resolve().parents:
        raise RuntimeError('Test log escapes its owned output directory')
    destination = target / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        raise RuntimeError('Regression snapshot bytes differ')
    files[relative.as_posix()] = digest
if not files:
    raise RuntimeError('No regression evidence retained')
receipt = {'phase': args.phase, 'meson_exit_status': args.exit_status, 'log_sha256': files,
           'scope': 'Exact log snapshot and observed phase exit only; failures remain failures.'}
(target / 'snapshot.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'phase': args.phase, 'exit_status': args.exit_status, 'retained_logs': len(files)}))
