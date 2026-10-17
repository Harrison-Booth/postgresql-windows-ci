"""Hosted Windows control for inherited pipes and the exact revised JIT launcher helper."""
import ast,json,os,pathlib,subprocess,sys,time
assert os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
root=pathlib.Path(os.environ['GITHUB_WORKSPACE']);os.chdir(root)
e=root/'evidence';e.mkdir()
child=e/'owned-child.py';parent=e/'owned-launcher.py'
child.write_text("import pathlib,sys,time\np=pathlib.Path(sys.argv[1]);deadline=time.monotonic()+6\nwhile time.monotonic()<deadline and not p.with_suffix('.release').exists():time.sleep(0.05)\np.with_suffix('.done').write_text('finished')\n")
parent.write_text("import pathlib,subprocess,sys\nsubprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]],close_fds=False)\nprint('launcher-returned',flush=True)\n")
baseline=e/'baseline'
started=time.monotonic()
try:
 subprocess.run([sys.executable,str(parent),str(child),str(baseline)],capture_output=True,text=True,timeout=1)
 raise AssertionError('Inherited-pipe baseline unexpectedly returned')
except subprocess.TimeoutExpired as error:
 baseline_elapsed=time.monotonic()-started
 assert baseline.with_suffix('.done').exists() and baseline_elapsed>=4,(baseline_elapsed,error)
source=(root/'ci/tools/run-hosted-jit-sql.py').read_text()
node=next(x for x in ast.parse(source).body if isinstance(x,ast.FunctionDef) and x.name=='run')
proof={'cases':[]}
def save():(e/'exact-helper-cases.json').write_text(json.dumps(proof,indent=2))
namespace={'Path':pathlib.Path,'subprocess':subprocess,'env':os.environ.copy(),'proof':proof,'save':save}
exec(compile(ast.Module(body=[node],type_ignores=[]),'exact-revised-run-helper','exec'),namespace)
candidate=e/'candidate';started=time.monotonic()
try:
 output=namespace['run']('owned-launcher',[sys.executable,str(parent),str(child),str(candidate)])
 candidate_elapsed=time.monotonic()-started
 assert output.strip()=='launcher-returned' and candidate_elapsed<4,(output,candidate_elapsed)
finally:
 candidate.with_suffix('.release').write_text('release only this owned fixture')
deadline=time.monotonic()+4
while not candidate.with_suffix('.done').exists() and time.monotonic()<deadline:time.sleep(0.05)
assert candidate.with_suffix('.done').exists()
receipt={'run_id':os.environ['GITHUB_RUN_ID'],'ci_head':os.environ['GITHUB_SHA'],'baseline_pipe_timeout_seconds':1,'baseline_actual_elapsed_seconds':baseline_elapsed,'file_output_actual_elapsed_seconds':candidate_elapsed,'exact_revised_helper_passed':True,'owned_children_completed':True,'scope':'Synthetic inherited-output Windows launch behavior only. Actual pg_ctl/JIT runtime still requires full hosted run.','postgresql_started':False}
(e/'completion.json').write_text(json.dumps(receipt,indent=2)+'\n')
