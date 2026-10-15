"""Hosted-only PostgreSQL assertion/configuration gates, separate from LLVM."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def owned(path, root):
    path = Path(os.path.abspath(path))
    if not path.is_relative_to(root):
        raise RuntimeError('Path outside owned root')
    for part in [path, *path.parents]:
        if part.exists() and (part.is_symlink() or getattr(part.lstat(), 'st_file_attributes', 0) & 0x400):
            raise RuntimeError('Reparse path is not accepted')
    return path


def main():
    if os.name != 'nt' or os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise SystemExit('GitHub-hosted Windows only')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['configuration', 'installed'])
    args = parser.parse_args()
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
    evidence = owned(workspace / 'evidence', workspace)
    build = owned(workspace / 'postgres/build', workspace)
    install = owned(workspace / 'pg-install', workspace)
    if not evidence.is_dir() or not build.is_dir():
        raise RuntimeError('Existing owned build/evidence paths required')
    destination = evidence / ('postgres-assertions-' + args.mode + '.json')
    receipt = dict(status='failed', mode=args.mode, run_id=os.environ.get('GITHUB_RUN_ID'),
                   tracker_revision=os.environ.get('GITHUB_SHA'), commands=[],
                   scope='Actual PostgreSQL configuration/header gates. LLVM assertions and full runtime remain separate requirements.')

    def native(path):
        raw = path.read_bytes()
        offset = struct.unpack_from('<I', raw, 0x3c)[0]
        if raw[:2] != b'MZ' or raw[offset:offset + 4] != b'PE\0\0' or struct.unpack_from('<H', raw, offset + 4)[0] != 0x8664:
            raise RuntimeError('Expected native x64 installed executable')
        return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), pe_machine='0x8664')

    def run(label, executable, option):
        log = evidence / ('postgres-assertions-' + label + '.log')
        entry = dict(label=label, argv=[str(executable), option], timeout_seconds=20)
        receipt['commands'].append(entry)
        child = None
        try:
            with log.open('wb') as output:
                child = subprocess.Popen(entry['argv'], stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                entry['owned_pid'] = child.pid
                try:
                    entry['exit_code'] = child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    entry['timed_out'] = True
                    # pg_config is a non-spawning read-only CLI. Only its retained
                    # exact process is terminated; never a name/global PID kill.
                    if child.poll() is None:
                        child.kill()
                    entry['exit_after_timeout'] = child.wait(timeout=5)
                    raise RuntimeError('Owned pg_config deadline exceeded')
            if entry['exit_code']:
                raise RuntimeError('Owned pg_config probe failed')
            return log.read_text(encoding='utf-8').strip()
        finally:
            if child is not None:
                entry['observed_controller_exit'] = child.poll()
            if log.exists():
                entry['log_sha256'] = sha(log)

    try:
        options_path = build / 'meson-info/intro-buildoptions.json'
        rows = json.loads(options_path.read_bytes())
        options = {row['name']: row['value'] for row in rows}
        if len(options) != len(rows):
            raise RuntimeError('Duplicate actual Meson option name')
        expected = dict(cassert=True, llvm='enabled', ssl='openssl', icu='enabled', buildtype='debug', auto_features='disabled')
        receipt['required_actual_meson_options'] = {name: options.get(name) for name in expected}
        receipt['intro_buildoptions_sha256'] = sha(options_path)
        for name, value in expected.items():
            if name not in options or type(options[name]) is not type(value) or options[name] != value:
                raise RuntimeError('Unexpected PostgreSQL Meson option: ' + name)
        generated = owned(build / 'src/include/pg_config.h', build)
        header = generated.read_text(encoding='utf-8')
        defines = dict(re.findall(r'^#define[ \t]+(\w+)[ \t]+([^\r\n]+)', header, re.M))
        macros = ['USE_ASSERT_CHECKING', 'USE_LLVM', 'USE_OPENSSL', 'USE_ICU']
        receipt['generated_macros'] = {name: defines.get(name) for name in macros}
        if any(defines.get(name, '').strip() != '1' for name in macros):
            raise RuntimeError('Required PostgreSQL generated feature/assertion macro absent')
        receipt['generated_header'] = dict(path=str(generated), sha256=sha(generated))
        if args.mode == 'installed':
            configuration_path = evidence / 'postgres-assertions-configuration.json'
            previous = json.loads(configuration_path.read_bytes())
            if previous['status'] != 'verified-postgresql-assertions-configuration' or previous['run_id'] != receipt['run_id'] or previous['tracker_revision'] != receipt['tracker_revision']:
                raise RuntimeError('Matching successful current-run configuration gate required')
            if previous['intro_buildoptions_sha256'] != sha(options_path) or previous['generated_header']['sha256'] != sha(generated):
                raise RuntimeError('Configuration/header changed after assertion gate')
            receipt['configuration_gate_sha256'] = sha(configuration_path)
            config = owned(install / 'bin/pg_config.exe', install)
            receipt['installed_pg_config'] = native(config)
            receipt['installed_postgres'] = native(owned(install / 'bin/postgres.exe', install))
            includedir = owned(run('includedir-server', config, '--includedir-server'), install)
            installed_header = owned(includedir / 'pg_config.h', install)
            receipt['installed_header'] = dict(path=str(installed_header), sha256=sha(installed_header))
            if installed_header.read_bytes() != generated.read_bytes():
                raise RuntimeError('Installed server pg_config.h differs from generated assertion-enabled header')
            receipt['pg_config_configure_output'] = run('configure', config, '--configure')
            receipt['pg_config_configure_scope'] = 'Recorded only. Pinned Meson sets CONFIGURE_ARGS empty; assertion proof is actual option plus byte-identical generated/installed USE_ASSERT_CHECKING header.'
            receipt['status'] = 'verified-postgresql-assertions-installed'
        else:
            receipt['status'] = 'verified-postgresql-assertions-configuration'
    except Exception as error:
        receipt['error'] = repr(error)
        raise
    finally:
        destination.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
