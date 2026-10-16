import hashlib
import json
import os
from pathlib import Path
import subprocess

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise SystemExit('Hosted runners only')
inputs=Path('tracker/tracking/hosted-llvm-inputs')
manifest=json.loads((inputs/'manifest.json').read_text())
assert manifest['series'] and manifest['llvm_series'], 'Both source patch stacks are required'
assert set(manifest['controls']) == {'main.c', 'jump.c'}, 'Both jump control sources are required'
stacks = [('postgres', 'postgresql_base', manifest['series']),
          ('llvm-source', 'llvm_revision', manifest['llvm_series'])]
# Check all bytes and both source identities before modifying either checkout.
for source, pin, series in stacks:
    head=subprocess.run(['git','-C',source,'rev-parse','HEAD'],
                        capture_output=True,text=True,check=True).stdout.strip()
    assert head==manifest[pin],source
    assert not subprocess.run(['git','-C',source,'status','--porcelain'],
                              capture_output=True,check=True).stdout,source
    for item in series:
        assert Path(item['file']).name==item['file'],item['file']
        assert hashlib.sha256((inputs/item['file']).read_bytes()).hexdigest()==item['sha256'],item['file']
for name,digest in manifest['controls'].items():
    assert Path(name).name==name,name
    assert hashlib.sha256((inputs/name).read_bytes()).hexdigest()==digest,name
application=[]
for source, pin, series in stacks:
    for item in series:
        file=inputs/item['file']
        subprocess.run(['git','-C',source,'apply','--check',str(file.resolve())],check=True)
        subprocess.run(['git','-C',source,'apply',str(file.resolve())],check=True)
        application.append({'source':source,'file':item['file'],'sha256':item['sha256']})
proof=dict(manifest=manifest,application=application,run_id=os.environ.get('GITHUB_RUN_ID'),attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),tracker_revision=os.environ.get('GITHUB_SHA'),runner_arch=os.environ.get('RUNNER_ARCH'),scope='Separate exact LLVM/PostgreSQL source reconstruction. Native hosted MinGW package versions recorded separately; historical compiler and SSL/ICU versions are not relabeled.')
Path('evidence/provenance.json').write_text(json.dumps(proof,indent=2)+'\n')
