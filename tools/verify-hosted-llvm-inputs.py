import hashlib
import json
import os
from pathlib import Path
import subprocess

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise SystemExit('Hosted runners only')
inputs=Path('tracker/tracking/hosted-llvm-inputs')
manifest=json.loads((inputs/'manifest.json').read_text())
for item in manifest['series']:
    file=inputs/item['file']
    assert hashlib.sha256(file.read_bytes()).hexdigest()==item['sha256'],item['file']
    subprocess.run(['git','-C','postgres','apply','--check',str(file.resolve())],check=True)
    subprocess.run(['git','-C','postgres','apply',str(file.resolve())],check=True)
for name,digest in manifest['controls'].items():
    assert hashlib.sha256((inputs/name).read_bytes()).hexdigest()==digest,name
proof=dict(manifest=manifest,run_id=os.environ.get('GITHUB_RUN_ID'),attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),tracker_revision=os.environ.get('GITHUB_SHA'),runner_arch=os.environ.get('RUNNER_ARCH'),scope='Exact source reconstruction. Native hosted MinGW package versions recorded separately; historical compiler and SSL/ICU versions are not relabeled.')
Path('evidence/provenance.json').write_text(json.dumps(proof,indent=2)+'\n')
