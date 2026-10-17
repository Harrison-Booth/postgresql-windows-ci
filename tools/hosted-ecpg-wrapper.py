"""Hosted-only exact submitted ECPG v2 required-wrapper regression control."""
import hashlib,json,os,pathlib,subprocess,sys,urllib.request,struct,platform
from datetime import datetime,timezone
assert os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
root=pathlib.Path(os.environ['GITHUB_WORKSPACE']);source=root/'postgres';e=root/'evidence';e.mkdir(exist_ok=True)
base='8e82d6610e7280d7870c0c36ff1c797e3cfb3e09'
host_arch=os.environ['TEST_HOST_ARCH'];assert host_arch in ['x64','arm64']
assert platform.machine().lower() in ({'x64':['amd64','x86_64'],'arm64':['arm64','aarch64']}[host_arch])
def call(args,name,timeout=600,cwd=source):
 with (e/(name+'.log')).open('wb') as log:
  p=subprocess.run(args,cwd=cwd,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
 if p.returncode:raise RuntimeError(name+' failed, inspect preserved log')
 return (e/(name+'.log')).read_text(errors='replace')
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()==base
if '--prepare-native-zic' in sys.argv:
 assert not subprocess.check_output(['git','status','--porcelain'],cwd=source,text=True).strip()
 native=root/'native-tools'
 call(['meson','setup',str(native),str(source),'-Dauto_features=disabled','-Dssl=none','-Dicu=disabled','-Dtap_tests=disabled'],'native-zic-setup')
 call(['meson','compile','-C',str(native),'zic','-j2'],'native-zic-build')
 data=(native/'src/timezone/zic.exe').read_bytes();offset=struct.unpack_from('<I',data,0x3c)[0];assert data[offset:offset+4]==b'PE\0\0'
 machine=struct.unpack_from('<H',data,offset+4)[0];assert machine=={'x64':0x8664,'arm64':0xaa64}[host_arch]
 (e/'native-zic.json').write_text(json.dumps({'host_architecture':host_arch,'machine':hex(machine),'sha256':hashlib.sha256(data).hexdigest(),'source_base':base},indent=2)+'\n')
 raise SystemExit(0)
native_config=root/'native-zic.ini';zic=root/'native-tools/src/timezone/zic.exe';assert zic.is_file()
native_config.write_text("[binaries]\nzic = '"+zic.as_posix()+"'\n")
inputs=[
 ('https://www.postgresql.org/message-id/attachment/206105/v2-0001-Fix-races-in-Windows-pthread-mutex-initialization.patch','8198f605a25cfb99a324cec6d057f1064f8e79aa46ce7d2d128f9a95840706e0'),
 ('https://www.postgresql.org/message-id/attachment/206106/v3-0001-Fix-pg_basebackup-long-path-test-on-Windows.patch','8ba40c250ea208a8e8738b38321336177a16a1c087aff3380db6c98ecc869c38'),
 ('https://www.postgresql.org/message-id/attachment/206107/v2-0001-meson-Support-ECPG-tests-in-runnable-cross-builds.patch','8096e3920cc5b47259e1691265d7e1ee7022c3c6745a03c3e7322f4524c4a84a')]
for i,(url,digest) in enumerate(inputs):
 data=urllib.request.urlopen(url,timeout=60).read();assert hashlib.sha256(data).hexdigest()==digest
 patch=e/('submitted-'+str(i)+'.patch');patch.write_bytes(data)
 call(['git','apply','--check',str(patch)],'apply-check-'+str(i));call(['git','apply',str(patch)],'apply-'+str(i))
wrapper_dir=root/'wrapper with spaces';wrapper_dir.mkdir();wrapper=wrapper_dir/'wrapper.py'
wrapper.write_text("import json,pathlib,subprocess,sys\np=pathlib.Path(__file__).with_name('invocations.jsonl')\nwith p.open('a',encoding='utf-8') as f:f.write(json.dumps(sys.argv[1:])+'\\n')\nraise SystemExit(subprocess.run(sys.argv[1:]).returncode)\n")
cross=root/'cross-required.ini'
cross.write_text("[binaries]\nc = 'cl'\ncpp = 'cl'\nar = 'lib'\nexe_wrapper = "+repr([sys.executable.replace('\\','/'),wrapper.as_posix()])+"\n[host_machine]\nsystem = 'windows'\ncpu_family = 'x86_64'\ncpu = 'x86_64'\nendian = 'little'\n[properties]\nneeds_exe_wrapper = true\n")
missing=root/'cross-missing.ini';missing.write_text('\n'.join(x for x in cross.read_text().splitlines() if not x.startswith('exe_wrapper ='))+'\n')
common=['--native-file',str(native_config),'-Dauto_features=disabled','-Dtap_tests=enabled','-Dcassert=true','-DPERL=C:/Strawberry/perl/bin/perl.exe','-DTAR=C:/Windows/System32/tar.exe']
negative=root/'build-no-wrapper';call(['meson','setup',str(negative),str(source),'--cross-file',str(missing),*common],'missing-wrapper-setup')
targets=json.loads(call(['meson','introspect','--targets',str(negative)],'missing-wrapper-targets'))
assert not any('/ecpg/test/' in str(t.get('defined_in','')).replace('\\','/') for t in targets),targets
build=root/'build-required';call(['meson','setup',str(build),str(source),'--cross-file',str(cross),*common],'required-wrapper-setup')
call(['meson','compile','-C',str(build),'-j2'],'build',1800)
call(['meson','test','-C',str(build),'--num-processes','1','--print-errorlogs','tmp_install','install_test_files','initdb_cache','ecpg/ecpg'],'ecpg-test',900)
records=[json.loads(x) for x in (build/'meson-logs/testlog.json').read_text().splitlines() if x.strip()]
assert len(records)==4 and all(x['result']=='OK' for x in records),records
ecpg=next(x for x in records if 'ecpg/ecpg' in x['name']);assert '1..66' in ecpg['stdout'] and 'All 66 tests passed' in ecpg['stdout'],ecpg
pe_records=[]
for binary in ['src/backend/postgres.exe','src/interfaces/ecpg/preproc/ecpg.exe','src/interfaces/ecpg/test/pg_regress_ecpg.exe']:
 data=(build/binary).read_bytes();offset=struct.unpack_from('<I',data,0x3c)[0];assert data[offset:offset+4]==b'PE\0\0';machine=struct.unpack_from('<H',data,offset+4)[0];assert machine==0x8664
 pe_records.append({'binary':binary,'sha256':hashlib.sha256(data).hexdigest(),'machine':hex(machine)})
invocations=[json.loads(x) for x in (wrapper_dir/'invocations.jsonl').read_text().splitlines() if x.strip()]
assert any('pg_regress_ecpg' in pathlib.Path(x[0]).name for x in invocations if x)
assert any(pathlib.Path(x[0]).name.lower()=='ecpg.exe' for x in invocations if x)
(e/'wrapper-invocations.json').write_text(json.dumps(invocations,indent=2)+'\n')
(e/'completion.json').write_text(json.dumps({'checked_utc':datetime.now(timezone.utc).isoformat(),'source_base':base,'submitted_attachment_sha256':[x[1] for x in inputs],'ci_head':os.environ['GITHUB_SHA'],'run_id':os.environ['GITHUB_RUN_ID'],'host_architecture':host_arch,'tested_executables':pe_records,'target_architecture':'x64','cross_mode':True,'needs_exe_wrapper':True,'wrapper_path_contains_spaces':True,'ecpg_subtests':66,'setup_groups':3,'missing_wrapper_ecpg_tests_disabled':True,'scope':'Standard hosted runner with verified native host architecture and x64 PE targets in required-wrapper cross mode. On ARM64 this exercises Windows x64 emulation. Exact original three-patch integration; not current-master runtime.'},indent=2)+'\n')
