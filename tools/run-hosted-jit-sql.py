import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess

if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':
    raise SystemExit('Hosted runners only')
bin=Path('pg-install/bin').resolve();data=Path('jit-data').resolve()
assert not data.exists()
with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
env={k:v for k,v in os.environ.items() if not k.startswith('PG')}
env['PATH']=str(bin)+';'+env['PATH']
env['PGCONNECT_TIMEOUT']='10'
proof=dict(status='running',scope='Installed LLVM-enabled native x64 server: forced JIT, inlining/optimization, JIT-off result comparison and error recovery',port=port,server_sha256=hashlib.sha256((bin/'postgres.exe').read_bytes()).hexdigest(),cases=[])
dest=Path('evidence/jit-runtime.json')
def save():dest.write_text(json.dumps(proof,indent=2)+'\n')
def run(name,command,expect=0):
    stdout_path=Path('evidence',name+'.stdout.log')
    stderr_path=Path('evidence',name+'.stderr.log')
    row=dict(name=name,command=command,status='running')
    proof['cases'].append(row);save()
    try:
        with stdout_path.open('wb') as stdout, stderr_path.open('wb') as stderr:
            p=subprocess.run(command,env=env,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,timeout=120)
    except BaseException as error:
        row['status']='failed';row['error']=str(error);save()
        Path('evidence',name+'.log').write_bytes(stdout_path.read_bytes()+stderr_path.read_bytes())
        raise
    output=stdout_path.read_text(errors='replace')
    errors=stderr_path.read_text(errors='replace')
    Path('evidence',name+'.log').write_text(output+errors)
    row.update(exit=p.returncode,status='completed');save()
    assert p.returncode==expect,(name,p.returncode,errors)
    return output
def sql(name,text,expect=0):
    return run(name,[str(bin/'psql.exe'),'-X','-At','-v','ON_ERROR_STOP=1','-h','127.0.0.1','-p',str(port),'-U','fixture','-d','postgres','-c',text],expect)
started=False
try:
    run('initdb',[str(bin/'initdb.exe'),'-D',str(data),'-U','fixture','-A','trust','--no-locale','--no-sync'])
    run('start',[str(bin/'pg_ctl.exe'),'-D',str(data),'-l',str(Path('evidence/server.log').resolve()),'-o',f'-h 127.0.0.1 -p {port}','-w','-t','60','start']);started=True
    assert sql('provider','SELECT pg_jit_available();').strip()=='t'
    sql('setup','CREATE TABLE jit_fixture AS SELECT i, i%17 AS k FROM generate_series(1,10000) i; ANALYZE jit_fixture;')
    query='SELECT sum(i::bigint*i),sum(k),count(*) FROM jit_fixture WHERE i%3<>0'
    prefix='SET jit=on; SET jit_above_cost=0; SET jit_inline_above_cost=0; SET jit_optimize_above_cost=0; SET max_parallel_workers_per_gather=0; '
    on=sql('jit-on',prefix+query).strip().splitlines()[-1]
    off=sql('jit-off','SET jit=off; '+query).strip().splitlines()[-1]
    assert on==off,(on,off)
    raw=sql('jit-explain',prefix+'EXPLAIN (ANALYZE,FORMAT JSON) '+query)
    plan=json.loads(raw[raw.index('['):])[0]
    assert plan['JIT']['Functions']>0,plan
    assert plan['JIT']['Options']['Inlining'] and plan['JIT']['Options']['Optimization'],plan['JIT']
    session=Path('evidence/error-recovery.sql').resolve()
    session.write_text(prefix+"SELECT 'before='||pg_backend_pid();\nSELECT sum(1/(i-i)) FROM jit_fixture;\nSELECT 'after='||pg_backend_pid(),42;\n")
    output=run('same-backend-error-recovery',[str(bin/'psql.exe'),'-X','-At','-v','ON_ERROR_STOP=0','-h','127.0.0.1','-p',str(port),'-U','fixture','-d','postgres','-f',str(session)])
    before=next(line.split('=',1)[1] for line in output.splitlines() if line.startswith('before='))
    assert f'after={before}|42' in output,output
    assert 'division by zero' in Path('evidence/same-backend-error-recovery.log').read_text(),output
    proof['result']=on;proof['jit']=plan['JIT'];proof['status']='passed'
except BaseException as error:
    proof['status']='failed';proof['error']=str(error);raise
finally:
    if started or (data/'postmaster.pid').exists():
        try:run('stop',[str(bin/'pg_ctl.exe'),'-D',str(data),'-m','fast','-w','-t','60','stop'])
        except BaseException as error:proof['status']='failed';proof['cleanup_error']=str(error)
    proof['pidfile_removed']=not (data/'postmaster.pid').exists();save()
    if proof['status']!='passed' or not proof['pidfile_removed']:raise SystemExit(1)
