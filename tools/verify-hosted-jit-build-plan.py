"""Check configured Meson arguments before building PostgreSQL; hosted Windows only."""
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess


def selector_operations(arguments):
    """Read GNU/MSVC define and undefine spellings without shell parsing."""
    operations = []
    i = 0
    while i < len(arguments):
        token = arguments[i]
        for prefix, operation in [('-D', 'define'), ('/D', 'define'),
                                  ('-U', 'undefine'), ('/U', 'undefine')]:
            if token.startswith(prefix):
                value = token[len(prefix):]
                if not value:
                    i += 1
                    if i == len(arguments):
                        raise ValueError('Incomplete macro argument')
                    value = arguments[i]
                if value.split('=', 1)[0] == 'BUILDING_LLVMJIT':
                    operations.append(operation)
                break
        i += 1
    return operations


def inspect_plan(commands, targets, workspace):
    # Ninja may expose only a Meson --internal exe wrapper. The public JSON
    # introspection supplies configured argv without decoding private pickles
    # or reparsing Windows shell quoting. Keep Ninja text as separate evidence.
    if not commands.strip():
        raise ValueError('Empty Ninja command plan')
    build = (workspace / 'postgres/build').resolve()
    source = (workspace / 'postgres').resolve()
    matches = [t for t in targets if t.get('name') == 'llvmjit_types.bc'
               and t.get('type') == 'custom']
    if len(matches) != 1:
        raise ValueError('Expected one types-bitcode custom target')
    target = matches[0]
    expected_output = (build / 'src/backend/jit/llvm/llvmjit_types.bc').resolve()
    if [Path(x).resolve() for x in target.get('filename', [])] != [expected_output]:
        raise ValueError('Unexpected types-bitcode output')
    groups = target.get('target_sources', [])
    if len(groups) != 1:
        raise ValueError('Expected one types-bitcode command group')
    group = groups[0]
    expected_source = (source / 'src/backend/jit/llvm/llvmjit_types.c').resolve()
    if [Path(x).resolve() for x in group.get('sources', [])] != [expected_source]:
        raise ValueError('Unexpected types-bitcode source')
    tokens = group.get('compiler', []) + group.get('parameters', [])
    if not tokens or '-emit-llvm' not in tokens:
        raise ValueError('Types-bitcode configured command is unavailable')
    includes = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == '-I':
            i += 1
            if i == len(tokens):
                raise ValueError('Incomplete include argument')
            includes.append(tokens[i])
        elif token.startswith('-I'):
            includes.append(token[2:])
        i += 1
    resolved = []
    for value in includes:
        value = value.replace('@BUILD_ROOT@', str(build)).replace('@SOURCE_ROOT@', str(source))
        if '@' in value:
            raise ValueError('Unresolved include placeholder')
        path = Path(value)
        resolved.append((path if path.is_absolute() else build / path).resolve())
    expected = (source / 'src/include/port/win32').resolve()
    if expected not in resolved:
        raise ValueError('Types bitcode lacks configured Windows compatibility include directory')
    provider_languages = set()
    providers = [t for t in targets if t.get('name') == 'llvmjit']
    if len(providers) != 1 or providers[0].get('type') != 'shared module':
        raise ValueError('Expected one llvmjit provider shared module')
    for target in targets:
        for group in target.get('target_sources', []):
            arguments = group.get('compiler', []) + group.get('parameters', [])
            operations = selector_operations(arguments)
            provider = target is providers[0] and group.get('language') in ('c', 'cpp')
            if provider:
                # Reject any undefine, including a later redefinition: this
                # guard deliberately requires an unambiguous provider mode.
                if not operations or 'undefine' in operations:
                    raise ValueError('Provider declaration selector absent or undefined')
                provider_languages.add(group['language'])
            elif operations:
                raise ValueError('Provider declaration selector leaked to another target/group')
    if provider_languages != {'c', 'cpp'}:
        raise ValueError('Both provider C and C++ compilation plans required')
    return {'types_configured_argv': tokens,
            'types_include_directories': [str(x) for x in resolved],
            'provider_languages': sorted(provider_languages),
            'command_evidence': 'Meson structured configured argv; Ninja recipes retained separately, wrappers not decoded'}


def inspect_provider_link_command(commands):
    pattern = r'(?:^|\s)-o\s+"?src[/\\]backend[/\\]jit[/\\]llvm[/\\]llvmjit\.dll"?(?:\s|$)'
    lines = [line for line in commands.splitlines() if re.search(pattern, line)]
    if len(lines) != 1:
        raise ValueError('Expected exactly one actual provider link command')
    line = lines[0]
    if '--disable-auto-import' not in line or line.rfind('--enable-auto-import') < line.rfind('--disable-auto-import'):
        raise ValueError('Provider data auto-import must override the global disable')
    return line


def inspect_bitcode_index(targets, build):
    matches = [t for t in targets if t.get('type') == 'custom'
               and any(str(f).replace('\\', '/').endswith('/postgres.index.bc')
                       for f in t.get('filename', []))]
    if len(matches) != 1 or len(matches[0].get('target_sources', [])) != 1:
        raise ValueError('Expected one complete backend bitcode index target')
    group = matches[0]['target_sources'][0]
    argv = group.get('compiler', []) + group.get('parameters', [])
    if '@INPUT@' in argv or argv.count('--input-list') != 1:
        raise ValueError('Backend index must use a manifest instead of expanded input argv')
    manifest = argv[argv.index('--input-list') + 1]
    manifest = manifest.replace('@BUILD_ROOT@', str(build))
    path = Path(manifest)
    if not path.is_absolute():
        path = build / path
    path = path.resolve()
    expected = (build / 'src/backend/jit/llvm/bitcode/postgres.inputs').resolve()
    if path != expected:
        raise ValueError('Unexpected backend bitcode input manifest')
    contents = path.read_bytes()
    inputs = [Path(line).resolve() for line in contents.decode('utf-8').splitlines()]
    sources = [Path(line).resolve() for line in group.get('sources', [])]
    if not inputs or inputs != sources:
        raise ValueError('Backend manifest does not preserve every ordered bitcode input')
    length = len(subprocess.list2cmdline(argv))
    if length >= 30000:
        raise ValueError('Backend bitcode command still approaches the Windows limit')
    return {'manifest_sha256': hashlib.sha256(contents).hexdigest(),
            'input_count': len(inputs), 'configured_command_characters': length,
            'all_ordered_dependencies_preserved': True}, contents


def main():
    if os.name != 'nt' or os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise SystemExit('GitHub-hosted Windows only')
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
    build = workspace / 'postgres/build'
    evidence = workspace / 'evidence'
    receipt = {'status': 'failed', 'run_id': os.environ['GITHUB_RUN_ID'],
               'scope': 'Meson configured argv preconditions and retained Ninja recipes only; no compilation/runtime pass'}
    try:
        proc = subprocess.run(['ninja', '-C', str(build), '-t', 'commands',
                               'src/backend/jit/llvm/llvmjit_types.bc'],
                              capture_output=True, timeout=60, check=True)
        (evidence / 'jit-types-ninja-commands.txt').write_bytes(proc.stdout)
        intro = build / 'meson-info/intro-targets.json'
        receipt.update(inspect_plan(proc.stdout.decode('utf-8'), json.loads(intro.read_text()), workspace))
        receipt['commands_sha256'] = hashlib.sha256(proc.stdout).hexdigest()
        receipt['targets_sha256'] = hashlib.sha256(intro.read_bytes()).hexdigest()
        provider = subprocess.run(['ninja', '-C', str(build), '-t', 'commands',
                                   'src/backend/jit/llvm/llvmjit.dll'],
                                  capture_output=True, timeout=60, check=True)
        (evidence / 'jit-provider-ninja-commands.txt').write_bytes(provider.stdout)
        receipt['provider_link_command'] = inspect_provider_link_command(provider.stdout.decode('utf-8'))
        receipt['provider_commands_sha256'] = hashlib.sha256(provider.stdout).hexdigest()
        receipt['bitcode_index'], contents = inspect_bitcode_index(json.loads(intro.read_text()), build)
        if receipt['bitcode_index']['input_count'] != 793:
            raise ValueError('Pinned backend bitcode input count changed')
        (evidence / 'postgres-bitcode-index-inputs.txt').write_bytes(contents)
        receipt['status'] = 'verified-preconditions-only'
    except Exception as exc:
        receipt['error'] = repr(exc)
        raise
    finally:
        (evidence / 'jit-generated-plan-verification.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
