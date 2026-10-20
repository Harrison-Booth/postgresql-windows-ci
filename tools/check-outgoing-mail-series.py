"""Hosted-only exact selected mail-series replay. No database or compilation."""
import hashlib,json,os,pathlib,re,subprocess
from datetime import datetime,timezone
assert os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
root=pathlib.Path(os.environ['GITHUB_WORKSPACE'])
ci=root/'ci'; source=root/'postgres'; evidence=root/'evidence';evidence.mkdir(exist_ok=False)
manifest=json.loads((ci/'tracking/email-readiness-20261017/manifest.json').read_text())
def run(args,cwd,log):
 p=subprocess.run(args,cwd=cwd,capture_output=True,timeout=120)
 (evidence/log).write_bytes(p.stdout+p.stderr)
 return p
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()==manifest['upstream_base']
p=run(['git','fetch','--depth=1','origin',manifest['previous_inspected_base']],source,'fetch-previous.log')
assert p.returncode==0
run(['git','config','user.name','PostgreSQL CI'],source,'config-name.log')
run(['git','config','user.email','ci@example.invalid'],source,'config-email.log')
results=[]
for name in ['fopen','resource']:
 items=[x for x in manifest['inputs'] if '/'+name+'/' in x['path']]
 assert items
 paths=set()
 for item in items:
  data=(ci/item['path']).read_bytes()
  assert hashlib.sha256(data).hexdigest()==item['sha256']
  paths.update(re.findall(r'^diff --git a/(.+?) b/',data.decode(),re.M))
 worktree=root/('replay-'+name)
 p=run(['git','worktree','add','--detach',str(worktree),manifest['upstream_base']],source,name+'-worktree.log')
 assert p.returncode==0
 delta=run(['git','diff','--exit-code',manifest['previous_inspected_base'],manifest['upstream_base'],'--',*sorted(paths)],source,name+'-source-delta.log')
 p=run(['git','-c','user.name=PostgreSQL CI','-c','user.email=ci@example.invalid','am',*[str(ci/x['path']) for x in items]],worktree,name+'-mail-application.log')
 row={'candidate':name,'base':manifest['upstream_base'],'ordered_attachment_sha256':[x['sha256'] for x in items],'mail_application_exit':p.returncode,'modified_paths_unchanged_since_previous_inspection':delta.returncode==0,'paths':sorted(paths)}
 if p.returncode==0:row['result_tree']=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=worktree,text=True).strip()
 results.append(row)
 (evidence/'results.json').write_text(json.dumps({'checked_utc':datetime.now(timezone.utc).isoformat(),'ci_head':os.environ['GITHUB_SHA'],'results':results,'runtime_tested':False,'emails_sent':False},indent=2)+'\n')
assert all(x['mail_application_exit']==0 for x in results),results
# Source deltas are retained for semantic review; a changed path alone is not a failed replay.
