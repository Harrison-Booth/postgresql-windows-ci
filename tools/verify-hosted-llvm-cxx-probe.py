"""Proposed hosted-only O0 GCC/shared LLVM boundary probe. Not run locally."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess

SOURCE = r'''#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include "llvm/ADT/SmallVector.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <type_traits>
#include <windows.h>
static_assert(std::is_same_v<llvm::SmallVectorSizeType<int>, uint32_t>);
static_assert(std::is_same_v<llvm::SmallVectorSizeType<char>, uint64_t>);
template <class T> void check() {
  llvm::SmallVector<T, 1> V;
  assert(V.empty() && V.size() == 0 && V.capacity() >= 1);
  for (int I = 0; I < 64; ++I) V.push_back(static_cast<T>(I));
  assert(!V.empty() && V.size() == 64 && V.capacity() >= 64);
  for (int I = 0; I < 64; ++I) assert(V[I] == static_cast<T>(I));
  V.resize(3);
  assert(V.size() == 3);
  V.clear();
  assert(V.empty() && V.size() == 0);
}
int main(int argc, char **argv) {
  if (argc != 2) return 2;
  check<int>(); check<char>();
  HMODULE H = GetModuleHandleA(argv[1]);
  if (!H) return 3;
  wchar_t P[32768];
  DWORD N = GetModuleFileNameW(H, P, 32768);
  if (!N || N >= 32768) return 4;
  char U[131072];
  int C = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, P, N, U, sizeof(U), nullptr, nullptr);
  if (C <= 0) return 5;
  std::printf("loadedLLVM=%.*s\n", C, U);
  return 0;
}
'''

def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    if os.name != 'nt' or os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted' or os.environ.get('GITHUB_REPOSITORY') != 'Harrison-Booth/postgresql-windows-ci':
        raise SystemExit('GitHub-hosted Windows only; no local compilation/runtime')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toolchain-root', type=Path, required=True)
    parser.add_argument('--build', type=Path, default=Path('llvm-build'))
    parser.add_argument('--source', type=Path, default=Path('llvm-source'))
    parser.add_argument('--evidence', type=Path, default=Path('evidence'))
    args = parser.parse_args()
    workspace = Path(os.path.abspath(os.environ['GITHUB_WORKSPACE']))
    helper = Path(__file__).with_name('verify-hosted-llvm-visibility.py')
    spec = importlib.util.spec_from_file_location('llvm_visibility_probe_primitives', helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    owned, pe = module.owned, module.pe_metadata
    build = owned(args.build, workspace)
    source = owned(args.source, workspace)
    evidence = owned(args.evidence, workspace)
    toolchain = Path(os.path.abspath(args.toolchain_root))
    owned(toolchain, toolchain)
    if not all(p.is_dir() for p in [build, source, evidence, toolchain]):
        raise RuntimeError('Existing owned source/build/evidence/toolchain roots required')
    record_dir = owned(evidence / 'llvm-cxx-export-probe', workspace)
    record_dir.mkdir(exist_ok=False)
    destination = record_dir / 'verification.json'
    receipt = dict(status='failed', run_id=os.environ.get('GITHUB_RUN_ID'),
                   tracker_revision=os.environ.get('GITHUB_SHA'), commands=[],
                   scope='Early real shared LLVM C++ boundary only; full PostgreSQL/JIT/regressions remain required')

    def run(label, argv, timeout=30, env=None):
        entry = dict(label=label, argv=[str(a) for a in argv], timeout_seconds=timeout)
        receipt['commands'].append(entry)
        log = record_dir / (label + '.log')
        child = None
        try:
            with log.open('xb') as output:
                child = subprocess.Popen(entry['argv'], stdin=subprocess.DEVNULL, stdout=output,
                                         stderr=subprocess.STDOUT, cwd=workspace, env=env)
                entry['owned_pid'] = child.pid
                try:
                    entry['exit_code'] = child.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    entry['timed_out'] = True
                    if child.poll() is None:
                        command = [str(Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'),
                                   '/PID', str(child.pid), '/T', '/F']
                        entry['timeout_cleanup_command'] = command
                        with (record_dir / (label + '-timeout-cleanup.log')).open('xb') as cleanup:
                            try:
                                result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=cleanup,
                                                        stderr=subprocess.STDOUT, timeout=15)
                                entry['timeout_cleanup_exit'] = result.returncode
                            except Exception as error:
                                entry['timeout_cleanup_error'] = repr(error)
                        entry['timeout_cleanup_log_sha256'] = digest(record_dir / (label + '-timeout-cleanup.log'))
                    try:
                        entry['exit_after_timeout'] = child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        entry['controller_exit_still_pending'] = True
                    raise RuntimeError('Exact owned probe command exceeded deadline: ' + label)
            if entry['exit_code'] != 0:
                raise RuntimeError('Actual command failed: ' + label)
            return log.read_text(encoding='utf-8').strip()
        except Exception as error:
            entry['error'] = repr(error)
            raise
        finally:
            if child is not None:
                entry['observed_controller_exit'] = child.poll()
            if log.exists():
                entry['log_sha256'] = digest(log)
                entry['log_path'] = str(log)

    try:
        work = owned(build / 'hosted-cxx-export-probe', workspace)
        work.mkdir(exist_ok=False)
        owner = dict(run_id=receipt['run_id'], tracker_revision=receipt['tracker_revision'])
        (work / 'owner.json').write_text(json.dumps(owner), encoding='utf-8')
        gxx = owned(toolchain / 'bin/g++.exe', toolchain)
        config = owned(build / 'bin/llvm-config.exe', build)
        provenance = evidence / 'provenance.json'
        prior = json.loads(provenance.read_bytes())
        if str(prior.get('run_id')) != str(receipt['run_id']) or prior.get('tracker_revision') != receipt['tracker_revision']:
            raise RuntimeError('Matching current-run source reconstruction provenance required')
        receipt['input_provenance'] = dict(path=str(provenance), sha256=digest(provenance))
        receipt['source_headers'] = [dict(path=str(p), sha256=digest(p)) for p in [
            source / 'llvm/include/llvm/ADT/SmallVector.h',
            source / 'llvm/include/llvm/Support/Compiler.h',
            build / 'include/llvm/Config/llvm-config.h']]
        receipt['primitive_helper'] = dict(path=str(helper), sha256=digest(helper))
        receipt['gxx'] = dict(path=str(gxx), **pe(gxx))
        receipt['llvm_config'] = dict(path=str(config), **pe(config))
        receipt['gxx_version'] = run('gxx-version', [gxx, '--version'])
        receipt['gxx_target'] = run('gxx-target', [gxx, '-dumpmachine'])
        if 'x86_64' not in receipt['gxx_target'] or 'mingw' not in receipt['gxx_target']:
            raise RuntimeError('Expected native x64 GNU MinGW consumer')
        cxxflags = shlex.split(run('llvm-config-cxxflags', [config, '--quote-paths', '--cxxflags']), posix=True)
        ldflags = shlex.split(run('llvm-config-ldflags', [config, '--quote-paths', '--ldflags']), posix=True)
        libdirs = shlex.split(run('llvm-config-libdir', [config, '--quote-paths', '--libdir']), posix=True)
        if any('LLVM_BUILD_STATIC' in f for f in cxxflags):
            raise RuntimeError('Static consumer flag is forbidden')
        dll_check = json.loads((evidence / 'llvm-dll-verification.json').read_bytes())
        if dll_check.get('status') != 'real-shared-library-export-check-passed-runtime-pending':
            raise RuntimeError('Prior actual DLL/export gate required')
        dll = owned(dll_check['dll'], build)
        implib = owned(dll_check['import_library'], build)
        if digest(dll) != dll_check['dll_sha256'] or digest(implib) != dll_check['import_library_sha256']:
            raise RuntimeError('Actual DLL/import library changed after export gate')
        # LLVM_LINK_LLVM_DYLIB=OFF permits static component linkage for tools.
        # --link-shared changes component mode, not that monolithic selection.
        # Use real directory metadata and the separately verified shared import
        # library, without requesting nonexistent per-component shared DLLs.
        if len(libdirs) != 1 or owned(libdirs[0], build) != implib.parent or not str(implib).lower().endswith('.dll.a'):
            raise RuntimeError('Actual shared import library disagrees with llvm-config libdir')
        receipt['llvm_config_library_directory'] = libdirs[0]
        dll_meta = pe(dll)
        families = {}
        for code in ['j', 'y']:
            prefix = 'SmallVectorBaseI' + code + 'E'
            names = [n for n in dll_meta['exports'] if prefix in n]
            groups = {'size': ['4sizeEv'], 'capacity': ['8capacityEv'], 'empty': ['5emptyEv'],
                      'set_size': ['8set_sizeE'], 'set_allocation_range': ['20set_allocation_rangeE'],
                      'constructor': ['C1E', 'C2E']}
            matched = {name: [n for n in names if any(fragment in n for fragment in fragments)]
                       for name, fragments in groups.items()}
            families[code] = matched
            if any(not value for value in matched.values()):
                receipt['required_template_exports'] = families
                raise RuntimeError('Actual DLL lacks required explicit-template member exports')
        receipt['required_template_exports'] = families
        receipt['shared_DLL'] = dict(path=str(dll), sha256=digest(dll))
        receipt['shared_import_library'] = dict(path=str(implib), sha256=digest(implib))
        cpp = work / 'probe.cpp'
        cpp.write_text(SOURCE, encoding='utf-8')
        (record_dir / 'probe.cpp').write_bytes(cpp.read_bytes())
        receipt['source'] = dict(path=str(cpp), sha256=digest(cpp))
        obj, exe = work / 'probe.obj', work / 'probe.exe'
        compile_argv = [gxx, *cxxflags, '-I' + str(source / 'llvm/include'),
                        '-I' + str(build / 'include'), '-O0', '-fno-inline', '-UNDEBUG',
                        '-c', cpp, '-o', obj]
        run('compile', compile_argv, timeout=120)
        run('link', [gxx, obj, *ldflags, implib, '-o', exe], timeout=120)
        executable = pe(exe)
        if dll.name.lower() not in [name.lower() for name in executable['imports']]:
            raise RuntimeError('Probe PE does not import the exact LLVM DLL name')
        receipt['executable'] = dict(path=str(exe), **executable)
        env = os.environ.copy()
        env['PATH'] = str(dll.parent) + os.pathsep + str(toolchain / 'bin') + os.pathsep + env.get('PATH', '')
        receipt['runtime_DLL_search_prefix'] = [str(dll.parent), str(toolchain / 'bin')]
        receipt['runtime_effective_PATH_sha256'] = hashlib.sha256(env['PATH'].encode('utf-8')).hexdigest()
        output = run('runtime', [exe, dll.name], timeout=30, env=env)
        prefix = 'loadedLLVM='
        matches = [line[len(prefix):] for line in output.splitlines() if line.startswith(prefix)]
        if len(matches) != 1:
            raise RuntimeError('Runtime did not attest one actual loaded LLVM DLL path')
        loaded = owned(matches[0], build)
        if os.path.normcase(str(loaded)) != os.path.normcase(str(dll)) or digest(loaded) != receipt['shared_DLL']['sha256']:
            raise RuntimeError('Runtime loaded a different LLVM DLL')
        if digest(dll) != dll_check['dll_sha256'] or digest(implib) != dll_check['import_library_sha256']:
            raise RuntimeError('DLL/import library changed during consumer probe')
        receipt['loaded_DLL'] = dict(path=str(loaded), sha256=digest(loaded))
        receipt['status'] = 'passed-real-GCC-O0-shared-LLVM-template-probe'
    except Exception as error:
        receipt['error'] = repr(error)
        raise
    finally:
        destination.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
