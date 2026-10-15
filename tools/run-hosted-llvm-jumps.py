import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':
    raise SystemExit('Hosted runners only')
ctypes.windll.kernel32.SetErrorMode(0x0001|0x0002|0x8000)
area=Path('jump-controls');area.mkdir()
inputs=Path('tracker/tracking/hosted-llvm-inputs')
rows=[]
proof={'status':'running','scope':'Actual reconstructed PostgreSQL c.h and generated configuration headers, O2, fixed Clang and native GCC, four caller/callee combinations','rows':rows}
dest=Path('evidence/jump-verification.json')
def save():dest.write_text(json.dumps(proof,indent=2)+'\n')
def run(name,command):
    p=subprocess.run(command,capture_output=True,text=True,timeout=120)
    log=Path('evidence')/(name+'.log');log.write_text(p.stdout+p.stderr)
    rows.append(dict(name=name,command=command,exit=p.returncode,log=str(log),log_sha256=hashlib.sha256(log.read_bytes()).hexdigest()));save()
    if p.returncode:raise RuntimeError(f'{name}: {p.returncode}')
    return p.stdout
try:
    compilers={name:shutil.which(name) for name in ['gcc','clang']}
    assert all(compilers.values()),compilers
    proof['compiler_sha256']={name:hashlib.sha256(Path(exe).read_bytes()).hexdigest() for name,exe in compilers.items()}
    for compiler,exe in compilers.items():
        for name in ['main','jump']:
            run(f'{compiler}-{name}',[exe,'-O2','-Werror=incompatible-pointer-types','-DWIN32','-Ipostgres/build/src/include','-Ipostgres/src/include','-Ipostgres/src/include/port/win32','-c',str(inputs/(name+'.c')),'-o',str(area/f'{compiler}-{name}.o')])
    for caller,callee in [('gcc','gcc'),('clang','clang'),('gcc','clang'),('clang','gcc')]:
        name=f'{caller}-to-{callee}';exe=area/(name+'.exe')
        run(name+'-link',[compilers['gcc'],str(area/f'{caller}-main.o'),str(area/f'{callee}-jump.o'),'-o',str(exe)])
        output=run(name+'-runtime',[str(exe.resolve())])
        assert '1000 nonlocal jumps passed' in output,output
    proof['status']='passed';save()
except BaseException as error:
    proof['status']='failed';proof['error']=str(error);save();raise
