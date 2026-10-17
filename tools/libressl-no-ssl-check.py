"""Hosted-only reproduction of the public CF7008 no-SSL compile failure."""
import hashlib,json,os,pathlib,platform,subprocess,sys,time
if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':
    raise SystemExit('Standard hosted runners only')
root=pathlib.Path.cwd();src=root/'postgres';out=root/'evidence';out.mkdir(exist_ok=True)
expected='600741b2692a01df2eb04bce9c0df11f1047a951'
assert subprocess.check_output(['git','-C',str(src),'rev-parse','HEAD'],text=True).strip()==expected
arch=os.environ['TEST_HOST_ARCH'];machine=platform.machine().lower()
assert (arch=='arm64' and machine in ('arm64','aarch64')) or (arch=='x64' and machine in ('amd64','x86_64'))
def run(args,name,cwd=None):
    start=time.monotonic()
    with (out/(name+'.log')).open('wb') as f:
        p=subprocess.run(args,cwd=cwd,stdout=f,stderr=subprocess.STDOUT,timeout=1200)
    results.append({'name':name,'args':args,'exit_code':p.returncode,'elapsed_seconds':time.monotonic()-start})
    (out/'commands.json').write_text(json.dumps(results,indent=2)+'\n')
    return p.returncode
results=[]
header=src/'src/include/libpq/libpq.h';before=header.read_text()
old='#ifdef USE_SSL\n#if defined(USE_OPENSSL)\n#define SSL_LIBRARY "OpenSSL"\n#elif defined(USE_LIBRESSL)\n#define SSL_LIBRARY "LibreSSL"\n#else\n#define SSL_LIBRARY ""\n#endif\n#endif'
new=old.removeprefix('#ifdef USE_SSL\n').removesuffix('\n#endif')
assert before.count(old)==1
(out/'libpq-before.h').write_text(before)
assert run([sys.executable,'-m','mesonbuild.mesonmain','setup','build-no-ssl','postgres','-Dauto_features=disabled','-Dssl=none','-Dicu=disabled','-Dtap_tests=disabled'],'configure')==0
code=run(['ninja','-C','build-no-ssl','-j','4','src/backend/postgres.exe'],'baseline-build')
log=(out/'baseline-build.log').read_text(errors='replace')
assert code!=0 and 'SSL_LIBRARY' in log and ('undeclared' in log or 'C2065' in log),'Expected exact undefined SSL_LIBRARY compile failure'
header.write_text(before.replace(old,new))
(out/'libpq-after.h').write_text(header.read_text())
diff=subprocess.check_output(['git','-C',str(src),'diff','--','src/include/libpq/libpq.h'])
(out/'no-ssl-correction.patch').write_bytes(diff)
assert run(['ninja','-C','build-no-ssl','-j','4','src/backend/postgres.exe'],'candidate-build')==0
binary=root/'build-no-ssl/src/backend/postgres.exe'
raw=binary.read_bytes();pe=int.from_bytes(raw[0x3c:0x40],'little');pe_machine=int.from_bytes(raw[pe+4:pe+6],'little')
assert pe_machine==({'x64':0x8664,'arm64':0xaa64}[arch])
controls=[]
for name,defs,want in [('none',[],''),('openssl',['USE_SSL','USE_OPENSSL'],'OpenSSL'),('libressl',['USE_SSL','USE_LIBRESSL'],'LibreSSL')]:
    source=out/(name+'.c');source.write_text('#include <stdio.h>\n'+new+'\nint main(void){puts(SSL_LIBRARY);return 0;}\n')
    exe=out/(name+'.exe')
    cmd=['cl','/nologo',str(source),'/Fe:'+str(exe)]+['/D'+x for x in defs]
    assert run(cmd,'macro-'+name)==0
    value=subprocess.check_output([str(exe)],text=True).strip();assert value==want
    controls.append({'provider':name,'value':value,'scope':'Extracted actual corrected macro block, no provider runtime'})
record={'source':expected,'host_arch':arch,'baseline_failure':'SSL_LIBRARY undefined with SSL disabled','candidate_build':'passed','target_pe_machine':hex(pe_machine),'binary_sha256':hashlib.sha256(raw).hexdigest(),'macro_controls':controls,'scope':'Exact public CFBot source; no SSL provider runtime or private integration test','correction_sha256':hashlib.sha256(diff).hexdigest()}
(out/'completion.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record))

