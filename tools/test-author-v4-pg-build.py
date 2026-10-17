"""Hosted-only actual PostgreSQL canary header baseline/candidate comparison."""
import argparse,os,pathlib,subprocess,json,sys,hashlib
def main():
 p=argparse.ArgumentParser();p.add_argument('--workspace',type=pathlib.Path,required=True);p.add_argument('--architecture',required=True);p.add_argument('--meson',type=pathlib.Path,required=True);a=p.parse_args()
 if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':raise RuntimeError('Hosted only')
 w=a.workspace;out=w/'evidence/pg-include-order';out.mkdir();inputs=w/'ci/tracking/meson-v4-review'
 source=w/'postgres';candidate=w/'postgres-focused'
 subprocess.run(['git','-C',str(source),'worktree','add','--detach',str(candidate),'7787aec8ab000932a8704a988b175b39ff816f2f'],check=True)
 for patch in ['author-v4.patch','focused-order-and-probes.patch']:
  subprocess.run(['git','-C',str(candidate),'apply',str(inputs/patch)],check=True)
 canary=w/'canary-include';canary.mkdir();(canary/'libpq-fe.h').write_text('#error CF6056 selected external libpq-fe.h instead of the PostgreSQL header\n')
 records=[]
 for name,path in [('author-v4',source),('author-v4-plus-focused',candidate)]:
  build=w/('pg-build-'+name)
  setup=[sys.executable,str(a.meson),'setup',str(build),str(path),'-Dauto_features=disabled','-Dssl=none','-Dicu=disabled','-Dtap_tests=disabled','-Dextra_include_dirs='+str(canary)]
  proc=subprocess.run(setup,capture_output=True,timeout=300)
  (out/(name+'-setup.log')).write_bytes(proc.stdout+proc.stderr)
  row={'revision':name,'setup_exit':proc.returncode,'architecture':a.architecture}
  if proc.returncode==0:
   cp=subprocess.run([sys.executable,str(a.meson),'compile','-C',str(build),'-j','2','psql'],capture_output=True,timeout=600)
   log=cp.stdout+cp.stderr;(out/(name+'-psql-build.log')).write_bytes(log)
   row.update(psql_build_exit=cp.returncode,canary_header_error_seen=b'CF6056 selected external libpq-fe.h' in log,pgctl_or_server_started=False)
   cc=build/'compile_commands.json'
   if cc.exists():
    data=json.loads(cc.read_text());selected=[x for x in data if pathlib.Path(x['file']).name in ['startup.c','common.c','config_info.c']]
    (out/(name+'-selected-compile-commands.json')).write_text(json.dumps(selected,indent=2))
  records.append(row);(out/'results.json').write_text(json.dumps(records,indent=2)+'\n')
 expected=records[0]['setup_exit']==0 and records[0].get('psql_build_exit',0)!=0 and records[0].get('canary_header_error_seen') and records[1]['setup_exit']==0 and records[1].get('psql_build_exit')==0
 return 0 if expected else 1
if __name__=='__main__':raise SystemExit(main())
