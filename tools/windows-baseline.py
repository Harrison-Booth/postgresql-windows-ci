"""Build an unmodified PostgreSQL snapshot and exercise an owned SQL cluster."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import time

BASE = '1d45c5da3a0ad22b8e172da352ed25bd70a2253a'


def main():
    if os.name != 'nt' or os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('GitHub-hosted Windows only')
    parser = argparse.ArgumentParser()
    parser.add_argument('--arch', choices=['x64', 'arm64'], required=True)
    args = parser.parse_args()
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
    source, install, evidence = [workspace / name for name in ['postgres', 'installation', 'evidence']]
    build, data = source / 'build', workspace / 'baseline-data'
    if not evidence.is_dir() or build.exists() or install.exists() or data.exists():
        raise RuntimeError('Fresh owned build/install/data paths required')
    receipt = {'status': 'failed', 'source_base': BASE, 'architecture': args.arch,
               'run_id': os.environ['GITHUB_RUN_ID'], 'ci_revision': os.environ['GITHUB_SHA'],
               'scope': 'Unmodified upstream baseline and basic SQL only; no contribution patch or full regression claim.',
               'commands': [], 'binary_provenance': {}}
    server, server_log = None, None

    def run(label, command, timeout=180, allow_failure=False):
        log = evidence / (label + '.log')
        entry = {'label': label, 'command': [str(x) for x in command], 'timeout_seconds': timeout}
        receipt['commands'].append(entry)
        child = None
        try:
            with log.open('wb') as output:
                child = subprocess.Popen(entry['command'], stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                entry['owned_pid'] = child.pid
                try:
                    entry['exit'] = child.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    entry['timed_out'] = True
                    # The retained child is still live; terminate only its owned tree.
                    if child.poll() is None:
                        killed = subprocess.run(['C:/Windows/System32/taskkill.exe', '/PID', str(child.pid), '/T', '/F'], capture_output=True, timeout=15)
                        entry['owned_tree_termination_exit'] = killed.returncode
                    child.wait(timeout=15)
                    raise RuntimeError('Command deadline exceeded: ' + label)
            text = log.read_text(encoding='utf-8', errors='replace')
            if entry['exit'] and not allow_failure:
                raise RuntimeError('Command failed: ' + label)
            return entry['exit'], text
        finally:
            if log.exists():
                entry['log_sha256'] = hashlib.sha256(log.read_bytes()).hexdigest()
            if child is not None:
                entry['observed_controller_exit'] = child.poll()

    def binary(name):
        path = install / 'bin' / (name + '.exe')
        raw = path.read_bytes()
        nt = struct.unpack_from('<I', raw, 0x3c)[0]
        machine = struct.unpack_from('<H', raw, nt + 4)[0]
        if raw[:2] != b'MZ' or raw[nt:nt + 4] != b'PE\0\0' or machine != {'x64': 0x8664, 'arm64': 0xaa64}[args.arch]:
            raise RuntimeError('Native PE mismatch: ' + name)
        receipt['binary_provenance'][name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'machine': hex(machine)}
        return path

    try:
        _, head = run('source-head', ['git', '-C', source, 'rev-parse', 'HEAD'])
        if head.strip() != BASE:
            raise RuntimeError('Upstream source pin differs')
        _, dirty = run('source-status', ['git', '-C', source, 'status', '--porcelain'])
        if dirty.strip():
            raise RuntimeError('Upstream source is modified')
        for name in ['python', 'ninja', 'win_bison', 'win_flex']:
            executable = shutil.which(name)
            if not executable:
                raise RuntimeError('Missing tool: ' + name)
            run(name + '-version', [executable, '--version'])
        python = shutil.which('python')
        meson = [python, '-m', 'mesonbuild.mesonmain']
        run('meson-version', [*meson, '--version'])
        run('configure', [*meson, 'setup', build, source, '--prefix=' + str(install),
            '--buildtype=debug', '-Db_vscrt=mdd', '-Dauto_features=disabled', '-Dssl=none',
            '-Dtap_tests=disabled', '-Dcassert=true', '-Dplperl=disabled', '-Dplpython=disabled',
            '-Dpltcl=disabled', '-DPERL=C:/Strawberry/perl/bin/perl.exe', '-DTAR=C:/Windows/System32/tar.exe'])
        run('compile', ['ninja', '-C', build, '-j', '2'], timeout=1800)
        run('install', [*meson, 'install', '-C', build, '--no-rebuild'], timeout=300)
        for name in ['postgres', 'initdb', 'psql', 'pg_ctl', 'pg_config']:
            binary(name)
        run('initdb', [binary('initdb'), '-D', data, '-U', 'ci_test', '--auth=trust', '--encoding=UTF8', '--locale=C', '--no-instructions'])
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        server_log = (evidence / 'server.log').open('wb')
        server = subprocess.Popen([str(binary('postgres')), '-D', str(data), '-p', str(port),
            '-c', 'listen_addresses=127.0.0.1', '-c', 'shared_buffers=16MB', '-c', 'max_connections=20'],
            stdin=subprocess.DEVNULL, stdout=server_log, stderr=subprocess.STDOUT)
        receipt['owned_server_controller_pid'] = server.pid
        psql = [binary('psql'), '-X', '-v', 'ON_ERROR_STOP=1', '-h', '127.0.0.1', '-p', str(port), '-U', 'ci_test', '-d', 'postgres', '-At']
        deadline = time.monotonic() + 40
        attempt = 0
        while True:
            if server.poll() is not None:
                raise RuntimeError('Owned server exited before readiness')
            attempt += 1
            code, result = run('ready-' + str(attempt), [*psql, '-c', 'SELECT 1'], timeout=10, allow_failure=True)
            if code == 0 and result.strip() == '1':
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Owned SQL readiness deadline exceeded')
            time.sleep(0.5)
        _, result = run('sql-smoke', [*psql, '-c', 'SELECT sum(x) FROM generate_series(1,100) AS g(x)'])
        if result.strip() != '5050':
            raise RuntimeError('Unexpected SQL result')
        _, actual_data = run('data-identity', [*psql, '-c', 'SHOW data_directory'])
        if os.path.normcase(os.path.abspath(actual_data.strip())) != os.path.normcase(str(data)):
            raise RuntimeError('Connected cluster identity differs')
        receipt['sql_result'] = 5050
        receipt['status'] = 'baseline-smoke-passed-cleanup-pending'
    except Exception as error:
        receipt['error'] = str(error)
        raise
    finally:
        cleanup_ok = server is None
        if server is not None:
            try:
                if server.poll() is None:
                    run('stop-owned-server', [binary('pg_ctl'), '-D', data, '-m', 'fast', '-w', '-t', '20', 'stop'], timeout=35)
                receipt['server_exit'] = server.wait(timeout=10)
                cleanup_ok = receipt['server_exit'] == 0 and not (data / 'postmaster.pid').exists()
            except Exception as error:
                receipt['cleanup_error'] = str(error)
                if server.poll() is None:
                    try:
                        ended = subprocess.run(['C:/Windows/System32/taskkill.exe', '/PID', str(server.pid), '/T', '/F'], capture_output=True, timeout=15)
                        receipt['owned_server_tree_termination_exit'] = ended.returncode
                        server.wait(timeout=15)
                    except Exception as termination_error:
                        receipt['owned_server_termination_error'] = str(termination_error)
            finally:
                if server_log is not None:
                    server_log.close()
        if (evidence / 'server.log').is_file():
            receipt['server_log_sha256'] = hashlib.sha256((evidence / 'server.log').read_bytes()).hexdigest()
        receipt['cleanup_verified'] = cleanup_ok
        if receipt['status'] == 'baseline-smoke-passed-cleanup-pending' and cleanup_ok:
            receipt['status'] = 'passed-baseline-smoke'
        for name in ['meson-log.txt', 'install-log.txt']:
            log = build / 'meson-logs' / name
            if log.is_file():
                shutil.copy2(log, evidence / name)
        (evidence / 'verification.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        if not cleanup_ok:
            raise RuntimeError('Owned server cleanup was not verified')


if __name__ == '__main__':
    main()
