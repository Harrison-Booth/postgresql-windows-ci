"""Build an unmodified PostgreSQL snapshot and exercise an owned SQL cluster."""
import argparse
import ctypes
from ctypes import wintypes
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


class NativeProcessApi:
    """Retain the actual postmaster identity, separately from pg_ctl."""
    def __init__(self):
        self.dll = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
            'GetSystemTimeAsFileTime': ([ctypes.POINTER(wintypes.FILETIME)], None),
            'GetProcessTimes': ([wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)], wintypes.BOOL),
            'QueryFullProcessImageNameW': ([wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            'WaitForSingleObject': ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            'GetExitCodeProcess': ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            'TerminateProcess': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = arguments, result

    @staticmethod
    def ticks(value):
        return (value.dwHighDateTime << 32) | value.dwLowDateTime

    def now(self):
        value = wintypes.FILETIME()
        self.dll.GetSystemTimeAsFileTime(ctypes.byref(value))
        return self.ticks(value)


class OwnedPostmaster:
    def __init__(self, api, data, executable, expected_hash, earliest_ticks):
        self.api, self.handle = api, None
        lines = (data / 'postmaster.pid').read_text(encoding='utf-8').splitlines()
        if len(lines) < 3 or os.path.normcase(os.path.abspath(lines[1])) != os.path.normcase(str(data)):
            raise RuntimeError('Fresh owned PID file data directory differs')
        self.pid = int(lines[0])
        if self.pid <= 0 or self.pid > 0xffffffff or self.pid == os.getpid():
            raise RuntimeError('Invalid owned postmaster PID')
        # QUERY_LIMITED_INFORMATION | SYNCHRONIZE | TERMINATE, never ALL_ACCESS.
        self.handle = api.dll.OpenProcess(0x1000 | 0x100000 | 0x0001, False, self.pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            created, exited, kernel, user = [wintypes.FILETIME() for _ in range(4)]
            if not api.dll.GetProcessTimes(self.handle, *[ctypes.byref(value) for value in [created, exited, kernel, user]]):
                raise ctypes.WinError(ctypes.get_last_error())
            creation = api.ticks(created)
            if not earliest_ticks <= creation <= api.now():
                raise RuntimeError('Postmaster creation time is outside this start attempt')
            # PID-file start time is recorded in whole Unix seconds.
            if abs(int(lines[2]) - int(creation / 10000000 - 11644473600)) > 2:
                raise RuntimeError('PID file start time differs from retained process')
            size = wintypes.DWORD(32768)
            image = ctypes.create_unicode_buffer(size.value)
            if not api.dll.QueryFullProcessImageNameW(self.handle, 0, image, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            if os.path.normcase(os.path.abspath(image.value)) != os.path.normcase(str(executable)):
                raise RuntimeError('Retained postmaster executable path differs')
            actual_hash = hashlib.sha256(Path(image.value).read_bytes()).hexdigest()
            if actual_hash != expected_hash or self.poll() is not None:
                raise RuntimeError('Postmaster binary changed or process already exited')
            self.identity = dict(pid=self.pid, executable=image.value, executable_sha256=actual_hash,
                                 creation_filetime=creation, attempt_started_filetime=earliest_ticks,
                                 pid_file_data_directory=lines[1], pid_file_start_unix=int(lines[2]),
                                 ownership='Fresh owned PID file, data root, creation time and executable; retained native handle.')
        except Exception:
            self.close()
            raise

    def poll(self):
        state = self.api.dll.WaitForSingleObject(self.handle, 0)
        if state == 258:
            return None
        if state != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        if not self.api.dll.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value

    def wait(self, timeout):
        state = self.api.dll.WaitForSingleObject(self.handle, int(timeout * 1000))
        if state == 258:
            raise RuntimeError('Retained postmaster exit deadline exceeded')
        if state != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return self.poll()

    def terminate(self):
        if self.poll() is None and not self.api.dll.TerminateProcess(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle is not None:
            handle, self.handle = self.handle, None
            if not self.api.dll.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())


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
    server, api, attempt_started_ticks, expected_server_hash = None, None, None, None
    start_attempted = False

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
        expected_server = binary('postgres')
        expected_server_hash = receipt['binary_provenance']['postgres']['sha256']
        api = NativeProcessApi()
        attempt_started_ticks = api.now()
        start_attempted = True
        receipt['start_attempted'] = True
        receipt['startup_method'] = 'Unmodified pg_ctl start; upstream restricted-token launch.'
        server_options = '-p ' + str(port) + ' -c listen_addresses=127.0.0.1 -c shared_buffers=16MB -c max_connections=20'
        run('start-owned-server', [binary('pg_ctl'), '-D', data, '-l', evidence / 'server.log',
            '-o', server_options, '-w', '-t', '30', 'start'], timeout=45)
        server = OwnedPostmaster(api, data, expected_server, expected_server_hash, attempt_started_ticks)
        receipt['owned_server_identity'] = server.identity
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
        cleanup_ok = not start_attempted
        receipt['start_attempted'] = start_attempted
        try:
            if start_attempted and server is None:
                # A failed pg_ctl controller can still have started its backend.
                # Adopt only the same positive identity checks, never a guessed PID.
                server = OwnedPostmaster(api, data, install / 'bin/postgres.exe', expected_server_hash, attempt_started_ticks)
                receipt['owned_server_identity'] = server.identity
                receipt['server_identity_acquired_during_failure_cleanup'] = True
            if server is not None:
                if server.poll() is not None:
                    receipt['server_exit'] = server.poll()
                    raise RuntimeError('Owned server exited before requested shutdown')
                else:
                    run('stop-owned-server', [binary('pg_ctl'), '-D', data, '-m', 'fast', '-w', '-t', '20', 'stop'], timeout=35)
                receipt['server_exit'] = server.wait(timeout=10)
                cleanup_ok = receipt['server_exit'] == 0 and not (data / 'postmaster.pid').exists()
        except Exception as error:
            receipt['cleanup_error'] = str(error)
            cleanup_ok = False
            if server is not None:
                try:
                    server.terminate()
                    receipt['fallback_retained_handle_exit'] = server.wait(timeout=15)
                    receipt['fallback_scope'] = 'Exact positively identified postmaster handle only; descendant cleanup not independently verified. Never a successful lifecycle.'
                except Exception as termination_error:
                    receipt['owned_server_termination_error'] = str(termination_error)
        finally:
            if server is not None:
                try:
                    server.close()
                except Exception as close_error:
                    cleanup_ok = False
                    receipt['server_handle_close_error'] = str(close_error)
        try:
            if (evidence / 'server.log').is_file():
                receipt['server_log_sha256'] = hashlib.sha256((evidence / 'server.log').read_bytes()).hexdigest()
            for name in ['meson-log.txt', 'install-log.txt']:
                log = build / 'meson-logs' / name
                if log.is_file():
                    shutil.copy2(log, evidence / name)
        except Exception as evidence_error:
            receipt['supplemental_evidence_error'] = str(evidence_error)
            cleanup_ok = False
        receipt['cleanup_verified'] = cleanup_ok
        if receipt['status'] == 'baseline-smoke-passed-cleanup-pending' and cleanup_ok:
            receipt['status'] = 'passed-baseline-smoke'
        (evidence / 'verification.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        if not cleanup_ok:
            raise RuntimeError('Owned server cleanup was not verified')


if __name__ == '__main__':
    main()
