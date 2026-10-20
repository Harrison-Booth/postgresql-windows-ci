"""Exercise exact CF7300 v4 export code. Native compilation is hosted-only."""
import argparse,hashlib,json,os,pathlib,shlex,subprocess,sys
from datetime import datetime,timezone

def main():
 p=argparse.ArgumentParser();p.add_argument('--source',type=pathlib.Path,required=True);p.add_argument('--meson',type=pathlib.Path,required=True);p.add_argument('--pkgconfig',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True);p.add_argument('--native',action='store_true');p.add_argument('--cmake-list-fix',action='store_true');p.add_argument('--cmake-space-fix',action='store_true');a=p.parse_args()
 if a.cmake_space_fix and not a.cmake_list_fix:raise RuntimeError('Space candidate requires list normalization')
 if a.native and (os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted'):raise RuntimeError('Native compiler controls require standard hosted runner')
 a.output.mkdir(parents=True,exist_ok=False)
 root=(a.source/'meson.build').read_text(encoding='utf-8')
 block=root[root.index("pkg_config = find_program('pkg-config', required: false)"):root.index('###############################################################\n# Build',root.index('# Dependency compiler flags for PGXS'))]
 if a.cmake_list_fix:
  assert block.count("incdirs.split(';')")==1
  block=block.replace("foreach incdir_raw : incdirs.split(';')", "incdirs_list = []\n      incdirs_list += incdirs\n      foreach incdir_raw : ';'.join(incdirs_list).split(';')")
 if a.cmake_space_fix:
  assert block.count("dep_flags += '-I' + incdir")==1
  block=block.replace("if incdir != '' and '-I' + incdir not in dep_flags", "incflag = '-I' + '\\\\ '.join(incdir.split(' '))\n        if incdir != '' and incflag not in dep_flags")
  block=block.replace("dep_flags += '-I' + incdir", "dep_flags += incflag")
 include=(a.source/'src/include/meson.build').read_text(encoding='utf-8')
 exports=include[include.index("var_cppflags = ' '.join"):include.index('var_cflags_sl =')]
 expected_sha='80d08b863671adea7b28246ddfeee52f6889f366e102b6d8f8cd18ce4e41d598'
 records=[]
 cases=['disabled','pkgconfig','pkgconfig-space','pkgconfig-override','extra-include-space']+(['cmake','cmake-space'] if a.native else [])
 extra_start=root.index('extra_include_args = []')
 extra_end=root.index('endforeach',extra_start)+len('endforeach')
 extra_include_block=root[extra_start:extra_end]
 for case in cases:
  project=a.output/case;project.mkdir();build=project/'build'
  prefix=project/('dependency prefix' if case.endswith('space') else 'dependency-prefix')
  names=['ssl','uc','i18n']
  for name in names:
   inc=prefix/name/'include';inc.mkdir(parents=True)
   (inc/('author_'+name+'.h')).write_text('#define AUTHOR_'+name.upper()+' 1\n')
  pc=project/'pc';pc.mkdir()
  for package,name in [('openssl','ssl'),('icu-uc','uc'),('icu-i18n','i18n')]:
   (pc/(package+'.pc')).write_text('prefix='+prefix.as_posix()+'/'+name+'\nName: '+package+'\nDescription: controlled v4 export probe\nVersion: 99.0\nCflags: -I"${prefix}/include"\nLibs:\n')
  (project/'meson_options.txt').write_text("option('extra_include_dirs', type: 'array', value: [])\n")
  extra_dir=project/'extra include dir'; extra_dir.mkdir()
  extra_dir_value=str(extra_dir.resolve())
  (extra_dir/'author_extra.h').write_text('#define AUTHOR_EXTRA 1\n')
  native=project/'native.ini';native.write_text('[binaries]\npkg-config = '+repr(a.pkgconfig.as_posix())+'\npkgconfig = '+repr(a.pkgconfig.as_posix())+'\n')
  pre="project('author-v4-control'"+(", 'c'" if a.native else '')+", version: '1')\ncppflags = []\n"
  if case=='disabled':
   pre+="ssl = dependency('no_such_ssl_for_control', required: false, method: 'pkg-config')\nicu = dependency('no_such_icu_for_control', required: false, method: 'pkg-config')\nicu_i18n = icu\n"
  elif case=='extra-include-space':
   pre+="ssl = dependency('no_such_ssl_for_control', required: false, method: 'pkg-config')\nicu = dependency('no_such_icu_for_control', required: false, method: 'pkg-config')\nicu_i18n = icu\n"
  elif case.startswith('pkgconfig'):
   pre+="ssl = dependency('openssl', method: 'pkg-config', required: true)\nicu = dependency('icu-uc', method: 'pkg-config', required: true)\nicu_i18n = dependency('icu-i18n', method: 'pkg-config', required: true)\n"
  else:
   configs=project/'cmake-packages';configs.mkdir()
   for package,targets in [('OpenSSL',['SSL','Crypto']),('ICU',['uc','i18n'])]:
    parts=['set('+package+'_FOUND TRUE)']
    dirs=[]
    for target in targets:
     name={'SSL':'ssl','Crypto':'ssl','uc':'uc','i18n':'i18n'}[target];inc=(prefix/name/'include').as_posix();dirs.append(inc)
     parts+=['add_library('+package+'::'+target+' INTERFACE IMPORTED)','set_target_properties('+package+'::'+target+' PROPERTIES INTERFACE_INCLUDE_DIRECTORIES "'+inc+'")']
    parts+=['set('+package+'_INCLUDE_DIRS "'+';'.join(dirs)+'")']
    (configs/(package+'Config.cmake')).write_text('\n'.join(parts)+'\n')
   pre+="ssl = dependency('OpenSSL', method: 'cmake', modules: ['OpenSSL::SSL'], cmake_args: ['-DCMAKE_FIND_PACKAGE_PREFER_CONFIG=ON', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5'], required: true)\nicu = dependency('ICU', method: 'cmake', modules: ['ICU::uc', 'ICU::i18n'], cmake_args: ['-DCMAKE_FIND_PACKAGE_PREFER_CONFIG=ON', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5'], required: true)\nicu_i18n = icu\n"
  post="""
data = configuration_data()
data.set('OPENSSL', ' '.join(pgxs_dep_cflags['openssl']))
data.set('ICU', ' '.join(pgxs_dep_cflags['icu']))
data.set('PG_CONFIG', var_cppflags_pg_config)
data.set('CPPFLAGS', var_cppflags)
configure_file(input: 'flags.in', output: 'flags.txt', configuration: data)
add_project_arguments(cppflags, language: ['c'])
add_project_arguments(extra_include_args, language: ['c'])
"""
  if a.native:
   post+="config_info_cflags = ['-DVAL_CPPFLAGS=\"@0@\"'.format(var_cppflags_pg_config)]\nexecutable('pg-config-flags-probe', 'print-flags.c', c_args: config_info_cflags)\n"
   (project/'print-flags.c').write_text('#include <stdio.h>\nint main(void){puts(VAL_CPPFLAGS);return 0;}\n')
  (project/'meson.build').write_text(pre+block+extra_include_block+'\n'+exports+post,encoding='utf-8')
  (project/'flags.in').write_text('@OPENSSL@\n@ICU@\n@PG_CONFIG@\n@CPPFLAGS@\n')
  if case=='extra-include-space':
   (project/'extra-probe.c').write_text('#include <author_extra.h>\nint probe(void){return AUTHOR_EXTRA;}\n')
   with (project/'meson.build').open('a',encoding='utf-8') as f:f.write("executable('extra-compiler-probe', 'extra-probe.c')\n")
  env=os.environ.copy()
  for key in ['CC','CXX','PKG_CONFIG_PATH','PKG_CONFIG_LIBDIR','PKG_CONFIG_SYSROOT_DIR']:env.pop(key,None)
  env['PKG_CONFIG_PATH']=str(pc);env['PKG_CONFIG_LIBDIR']=str(pc)
  cmd=[sys.executable,str(a.meson),'setup',str(build),str(project),'--native-file',str(native)]
  if case=='pkgconfig-override':
   bad=project/'wrong-pc';bad.mkdir();env['PKG_CONFIG_PATH']=str(bad);env['PKG_CONFIG_LIBDIR']=str(bad);cmd+=['-Dpkg_config_path='+str(pc)]
  if case.startswith('cmake'):cmd+=['-Dcmake_prefix_path='+str(configs)]
  if case=='extra-include-space':cmd+=['-Dextra_include_dirs='+extra_dir_value]
  proc=subprocess.run(cmd,env=env,capture_output=True,timeout=90)
  (project/'setup.log').write_bytes(proc.stdout+proc.stderr)
  row={'case':case,'setup_exit':proc.returncode,'compiler_execution':a.native,'command':cmd}
  setup_text=(proc.stdout+proc.stderr).decode(errors='replace')
  row['known_cmake_array_failure']=not a.cmake_list_fix and case.startswith('cmake') and proc.returncode!=0 and "Arrays do not have a method called 'split'" in setup_text and 'dependency openssl' in setup_text and 'dependency icu' in setup_text
  if proc.returncode==0:
   flags=(build/'flags.txt').read_text(encoding='utf-8').splitlines();row['flags']=flags
   expected=[(prefix/name/'include').as_posix() for name in names] if case not in ['disabled','extra-include-space'] else []
   dependencies=json.loads((build/'meson-info/intro-dependencies.json').read_text(encoding='utf-8'))
   resolved=[flag for dep in dependencies for flag in dep.get('compile_args',[])]
   row['dependency_metadata_valid']=all('-I'+x in resolved or '/I'+x in resolved for x in expected)
   assert row['dependency_metadata_valid'],('Invalid controlled dependency metadata',case,resolved,expected)
   tokens=shlex.split(flags[2]);row['exported_include_argv_matches']=all('-I'+x in tokens for x in expected)
   if case=='extra-include-space':
    expected_extra='-I'+extra_dir.resolve().as_posix()
    row['extra_include_export_matches']=expected_extra in shlex.split(flags[2]) and expected_extra in shlex.split(flags[3])
   if a.native:
    cp=subprocess.run([sys.executable,str(a.meson),'compile','-C',str(build),'pg-config-flags-probe'],env=env,capture_output=True,timeout=90)
    (project/'macro-compile.log').write_bytes(cp.stdout+cp.stderr);row['macro_compile_exit']=cp.returncode
    if cp.returncode==0:
     run=subprocess.run([str(build/'pg-config-flags-probe.exe')],env=env,capture_output=True,timeout=10)
     row['macro_runtime_exit']=run.returncode;row['pg_config_macro_roundtrip']=run.stdout.decode().rstrip('\r\n')==flags[2]
    if case not in ['disabled','extra-include-space']:
     (project/'header-probe.c').write_text('#include <author_ssl.h>\n#include <author_uc.h>\n#include <author_i18n.h>\nint probe(void){return AUTHOR_SSL+AUTHOR_UC+AUTHOR_I18N;}\n')
     compilecmd=['cl','/nologo','/c',*tokens,str(project/'header-probe.c'),'/Fo'+str(project/'header-probe.obj')]
     cp=subprocess.run(compilecmd,env=env,capture_output=True,timeout=30)
     (project/'exported-header-compile.log').write_bytes(cp.stdout+cp.stderr);row['exported_header_compile_exit']=cp.returncode
    if case=='extra-include-space':
     cp=subprocess.run([sys.executable,str(a.meson),'compile','-C',str(build),'extra-compiler-probe'],env=env,capture_output=True,timeout=90)
     (project/'extra-compiler-probe-build.log').write_bytes(cp.stdout+cp.stderr);row['extra_include_compiler_build_exit']=cp.returncode
     source=project/'extra-probe.c';obj=project/'exported-extra-probe.obj'
     cp=subprocess.run(['cl','/nologo','/c',*shlex.split(flags[2]),str(source),'/Fo'+str(obj)],env=env,capture_output=True,timeout=30)
     (project/'exported-extra-probe-compile.log').write_bytes(cp.stdout+cp.stderr);row['exported_extra_include_compile_exit']=cp.returncode
     obj=project/'makefile-exported-extra-probe.obj'
     cp=subprocess.run(['cl','/nologo','/c',*shlex.split(flags[3]),str(source),'/Fo'+str(obj)],env=env,capture_output=True,timeout=30)
     (project/'makefile-exported-extra-probe-compile.log').write_bytes(cp.stdout+cp.stderr);row['makefile_cppflags_extra_compile_exit']=cp.returncode
  records.append(row)
  (a.output/'results.json').write_text(json.dumps({'checked_utc':datetime.now(timezone.utc).isoformat(),'native_compilation':a.native,'cmake_list_normalization_candidate':a.cmake_list_fix,'cmake_space_escape_candidate':a.cmake_space_fix,'author_patch_sha256':expected_sha,'actual_export_block_sha256':hashlib.sha256(block.encode()).hexdigest(),'actual_var_cppflags_block_sha256':hashlib.sha256(exports.encode()).hexdigest(),'source':str(a.source),'scope':'Author-v4 export code and the follow-up extra_include_dirs export code with controlled dependency metadata. Not full PostgreSQL, optional-dependency, or PGXS runtime.','records':records},indent=2)+'\n')
 print(json.dumps(records))
 positive_control=next(x for x in records if x['case']=='disabled')
 compiler_control_ok=not a.native or (positive_control.get('macro_compile_exit')==0 and positive_control.get('macro_runtime_exit')==0 and positive_control.get('pg_config_macro_roundtrip') is True)
 extra_case=next(x for x in records if x['case']=='extra-include-space')
 extra_case_ok=extra_case['setup_exit']==0 and (not a.native or (extra_case.get('extra_include_export_matches') is True and extra_case.get('extra_include_compiler_build_exit')==0 and extra_case.get('exported_extra_include_compile_exit')==0 and extra_case.get('makefile_cppflags_extra_compile_exit')==0 and extra_case.get('pg_config_macro_roundtrip') is True))
 candidate_ok=(not a.cmake_space_fix or all(x['setup_exit']==0 and x.get('exported_include_argv_matches') is True and (not a.native or (x.get('macro_compile_exit')==0 and x.get('macro_runtime_exit')==0 and x.get('pg_config_macro_roundtrip') is True and (x['case'] in ['disabled','extra-include-space'] or x.get('exported_header_compile_exit')==0))) for x in records)) and extra_case_ok
 return 0 if all(x['setup_exit']==0 or x['known_cmake_array_failure'] for x in records) and compiler_control_ok and candidate_ok else 1
if __name__=='__main__':raise SystemExit(main())
