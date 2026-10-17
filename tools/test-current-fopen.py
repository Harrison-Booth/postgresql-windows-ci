"""Native frontend-only fopen verification; hosted execution required."""
import hashlib,json,os,pathlib,struct,subprocess,sys
from datetime import datetime,timezone
assert os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
root=pathlib.Path(os.environ['GITHUB_WORKSPACE']);ci=root/'ci';source=root/'postgres';e=root/'evidence';e.mkdir(exist_ok=True)
def call(args,name,timeout=300):
 p=subprocess.run(args,capture_output=True,timeout=timeout,cwd=root)
 (e/(name+'.log')).write_bytes(p.stdout+p.stderr)
 if p.returncode:raise RuntimeError(name+' failed with '+str(p.returncode))
 return p.stdout.decode(errors='replace')
m=json.loads((ci/'tracking/email-readiness-20261017/manifest.json').read_text())
x=next(x for x in m['inputs'] if '/fopen/' in x['path']);patch=ci/x['path']
assert hashlib.sha256(patch.read_bytes()).hexdigest()==x['sha256']
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()==m['upstream_base']
call(['git','-C',str(source),'-c','user.name=PostgreSQL CI','-c','user.email=ci@example.invalid','am',str(patch)],'mail-application')
build=root/'frontend-build'
call(['meson','setup',str(build),str(source),'-Dauto_features=disabled','-Dtap_tests=enabled','-Dssl=disabled','-Dicu=disabled'],'setup')
call(['meson','compile','-C',str(build),'test_fopen','-j2'],'compile',600)
exe=build/'src/test/modules/test_pgport/test_fopen.exe'
data=exe.read_bytes();offset=struct.unpack_from('<I',data,0x3c)[0];assert data[offset:offset+4]==b'PE\0\0'
machine=struct.unpack_from('<H',data,offset+4)[0]
expected={'x64':0x8664,'arm64':0xaa64}[os.environ['TEST_ARCH']];assert machine==expected
for name,extra in [('wrapper',[]),('native-crt',['--native'])]:
 out=call([str(exe),str(e/('owned '+name+' fixture.dat')),*extra],name)
 assert 'cases=48 failures=0' in out and out.count('errors=0')==48,out
call(['meson','test','-C',str(build),'--suite','test_pgport','--no-rebuild','--print-errorlogs'],'registered-tap',120)
log=(build/'meson-logs/testlog.json').read_text()
records=[json.loads(x) for x in log.splitlines() if x.strip()]
assert records and all(x.get('result')=='OK' for x in records),records
for item in records:
 assert '1..4' in item.get('stdout','') and 'ok 4' in item.get('stdout',''),item
(e/'registered-testlog.json').write_text(log)
receipt={'checked_utc':datetime.now(timezone.utc).isoformat(),'ci_head':os.environ['GITHUB_SHA'],'run_id':os.environ['GITHUB_RUN_ID'],'source_base':m['upstream_base'],'patch_sha256':x['sha256'],'native_architecture':os.environ['TEST_ARCH'],'pe_machine':hex(machine),'helper_sha256':hashlib.sha256(data).hexdigest(),'wrapper_cases':48,'crt_cases':48,'registered_tap_assertions':4,'database_started':False,'scope':'Current-master frontend libpgport API and exact registered TAP regression. No full server or backpatch claim.'}
(e/'completion.json').write_text(json.dumps(receipt,indent=2)+'\n')
