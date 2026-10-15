"""Record actual CMake closure, real PE DLL exports and provider imports."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise SystemExit('Hosted runners only; no local execution')
parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=['closure', 'dll', 'provider'])
parser.add_argument('--build', type=Path, required=True)
parser.add_argument('--evidence', type=Path, required=True)
parser.add_argument('--install', type=Path)
args = parser.parse_args()
build, evidence = args.build.resolve(), args.evidence.resolve()
workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
if build != workspace / 'llvm-build' or evidence != workspace / 'evidence':
    raise SystemExit('Only owned hosted build/evidence paths are accepted')
roots = ['ExecutionEngine', 'MCJIT', 'OrcJIT', 'Passes', 'DebugInfoDWARF',
         'X86CodeGen', 'X86AsmParser', 'X86Desc', 'X86Info', 'X86Disassembler']
sha = lambda raw: hashlib.sha256(raw).hexdigest()

def save(name, data):
    (evidence / name).write_text(json.dumps(data, indent=2) + '\n')

if args.mode == 'closure':
    config_path = build / 'lib/cmake/llvm/LLVMConfig.cmake'
    exports_path = build / 'lib/cmake/llvm/LLVMExports.cmake'
    config, exports = config_path.read_text(), exports_path.read_text()
    # Keep actual parser inputs in artifacts even if dependency validation fails.
    shutil.copy2(config_path, evidence / 'LLVMConfig.cmake')
    shutil.copy2(exports_path, evidence / 'LLVMExports.cmake')
    match = re.search(r'set\(LLVM_AVAILABLE_LIBS\s+(.*?)\)', config, re.S)
    if not match:
        raise SystemExit('No actual configured LLVM component inventory')
    available = set(re.findall(r'\bLLVM[A-Za-z0-9_]+', match.group(1)))
    # CMake omits set_target_properties entirely for dependency-free imported
    # libraries such as LLVMDemangle. Their add_library declaration is the
    # authoritative node; an absent property block does not mean missing target.
    declared = set(re.findall(r'add_library\((LLVM\w+)\s+(?:STATIC|SHARED|INTERFACE|UNKNOWN)\s+IMPORTED\)', exports))
    graph = {name: set() for name in declared}
    targets_with_properties = set()
    for match in re.finditer(r'set_target_properties\((LLVM\w+) PROPERTIES\s*\n(.*?)\n\)', exports, re.S):
        name, body = match.groups()
        if name not in declared:
            raise SystemExit('Property block for undeclared library: ' + name)
        targets_with_properties.add(name)
        links = re.search(r'INTERFACE_LINK_LIBRARIES\s+"([^"]*)"', body)
        graph[name] = set(re.findall(r'\bLLVM[A-Za-z0-9_]+', links.group(1))) if links else set()
    closure, pending = set(), ['LLVM' + name for name in roots]
    while pending:
        name = pending.pop()
        if name in closure:
            continue
        if name not in available or name not in graph:
            raise SystemExit('Unresolved configured dependency: ' + name)
        closure.add(name)
        pending.extend(graph[name] - closure)
    components = sorted(name[4:] for name in closure)
    if 'LLVM' in closure or len(closure) >= len(available):
        raise SystemExit('Invalid explicit component closure')
    save('llvm-component-closure.json', {
        'status': 'configured-closure-only-runtime-pending', 'roots': roots,
        'components': components, 'library_targets': sorted(closure),
        'available_component_targets': len(available),
        'declared_imported_library_targets': len(declared),
        'closure_targets_without_property_block': sorted(closure - targets_with_properties),
        'config_sha256': sha(config_path.read_bytes()),
        'exports_sha256': sha(exports_path.read_bytes()),
        'reason': 'Whole-archive every required dependency too, preserving Core/BitReader/Linker C and C++ APIs needed by PostgreSQL.',
        'scope': 'Actual first-configure target graph. No compilation, export-limit or runtime claim.'})
    print(';'.join(components))
    raise SystemExit(0)

def pe(path):
    raw = path.read_bytes()
    header = struct.unpack_from('<I', raw, 0x3c)[0]
    if raw[:2] != b'MZ' or raw[header:header+4] != b'PE\0\0':
        raise RuntimeError('Not a PE binary: ' + str(path))
    machine, section_count = struct.unpack_from('<HH', raw, header+4)
    optional_size = struct.unpack_from('<H', raw, header+20)[0]
    optional = header + 24
    if machine != 0x8664 or struct.unpack_from('<H', raw, optional)[0] != 0x20b:
        raise RuntimeError('Expected native Windows x64 PE32+')
    sections = []
    for index in range(section_count):
        start = optional + optional_size + index*40
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from('<IIII', raw, start+8)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_offset, raw_size))
    def offset(rva):
        for address, span, file_offset, file_size in sections:
            if address <= rva < address + span:
                delta = rva-address
                if delta >= file_size:
                    raise RuntimeError('PE RVA outside file-backed section')
                return file_offset+delta
        raise RuntimeError('PE RVA not mapped')
    def text(rva):
        start = offset(rva)
        end = raw.index(b'\0', start)
        return raw[start:end].decode('ascii')
    def directory(index):
        return struct.unpack_from('<II', raw, optional+112+index*8)
    return raw, offset, text, directory

dlls = list((build / 'bin').glob('libLLVM-*.dll'))
if len(dlls) != 1:
    raise SystemExit('Expected exactly one freshly built libLLVM DLL')
dll = dlls[0]
raw, offset, text, directory = pe(dll)
export_rva, export_size = directory(0)
if not export_rva:
    raise SystemExit('LLVM DLL has no exports')
base, count, name_count, functions_rva, names_rva, ordinals_rva = struct.unpack_from('<IIIIII', raw, offset(export_rva)+16)
required_exports = ['LLVMContextCreate', 'LLVMBuildAdd', 'LLVMParseBitcode2',
                    'LLVMCreatePassBuilderOptions', 'LLVMRunPasses',
                    'LLVMOrcCreateLLJIT', 'LLVMInitializeX86Target', 'LLVMInitializeX86AsmParser']
# Preserve actual metadata before applying validation gates. A successful DLL
# link alone does not prove its public API or export ordinal boundary is valid.
export_metadata = {
    'status': 'unvalidated-export-metadata', 'validation_passed': False,
    'dll': str(dll), 'dll_sha256': sha(raw),
    'export_directory_rva': export_rva, 'export_directory_size': export_size,
    'header': {'ordinal_base': base, 'function_count': count, 'name_count': name_count,
               'functions_rva': functions_rva, 'names_rva': names_rva,
               'ordinals_rva': ordinals_rva},
    'scope': 'Raw PE export evidence only. Ordinal/API/import-library/provider/runtime checks have not passed.'}
save('llvm-dll-export-prevalidation.json', export_metadata)
try:
    functions = struct.unpack_from('<' + 'I'*count, raw, offset(functions_rva))
    named_ordinals = struct.unpack_from('<' + 'H'*name_count, raw, offset(ordinals_rva))
    names = [text(rva) for rva in struct.unpack_from('<' + 'I'*name_count, raw, offset(names_rva))]
    actual_ordinals = [base+i for i, rva in enumerate(functions) if rva]
    tables = {'status': 'unvalidated-export-tables', 'dll_sha256': sha(raw),
              'function_rvas': list(functions), 'named_ordinal_indexes': list(named_ordinals),
              'names': names}
    save('llvm-dll-export-tables.json', tables)
    export_metadata.update({
        'tables': 'llvm-dll-export-tables.json',
        'tables_sha256': sha((evidence / 'llvm-dll-export-tables.json').read_bytes()),
        'actual_live_function_count': len(actual_ordinals),
        'maximum_actual_export_ordinal': max(actual_ordinals) if actual_ordinals else None,
        'required_api_presence': {name: name in names for name in required_exports},
        'table_read_completed': True})
    save('llvm-dll-export-prevalidation.json', export_metadata)
except Exception as exc:
    export_metadata['table_read_error'] = repr(exc)
    save('llvm-dll-export-prevalidation.json', export_metadata)
    raise
if not actual_ordinals or max(actual_ordinals) > 65535 or count > 65535:
    raise SystemExit('LLVM PE export ordinal range exceeds the Windows linker boundary')
if any(index >= count for index in named_ordinals) or not set(required_exports).issubset(names):
    raise SystemExit('Malformed exports or required real JIT APIs absent')
import_library = build / 'lib' / ('lib' + dll.stem.removeprefix('lib') + '.dll.a')
if not import_library.is_file():
    raise SystemExit('Actual LLVM import library absent')
alias = build / 'lib' / dll.name
if args.mode == 'dll':
    if alias.exists() and alias.read_bytes() != raw:
        raise SystemExit('Preserve unexpected existing DLL alias; do not overwrite')
    if not alias.exists():
        shutil.copy2(dll, alias)
    if alias.read_bytes() != raw:
        raise SystemExit('DLL libdir alias bytes differ')
    save('llvm-dll-verification.json', {
        'status': 'real-shared-library-export-check-passed-runtime-pending',
        'dll': str(dll), 'dll_sha256': sha(raw), 'libdir_alias': str(alias),
        'alias_sha256': sha(alias.read_bytes()), 'import_library': str(import_library),
        'import_library_sha256': sha(import_library.read_bytes()),
        'export_address_table_entries': count, 'named_export_count': name_count,
        'maximum_actual_export_ordinal': max(actual_ordinals), 'required_exports_present': required_exports,
        'layout_reason': 'Meson1.11.2 config-tool fallback searches libdir for DLLs; MinGW puts the real DLL in bin. This is a byte-identical layout alias, not fabricated llvm-config output.',
        'scope': 'Shared LLVM retained. Standalone tools may use static component libraries; PostgreSQL provider must import this actual DLL.'})
    raise SystemExit(0)

if not args.install or args.install.resolve() != workspace / 'pg-install':
    raise SystemExit('Expected owned PostgreSQL installation')
providers = list(args.install.resolve().rglob('llvmjit.dll'))
if len(providers) != 1 or not alias.exists() or alias.read_bytes() != raw:
    raise SystemExit('Expected actual llvmjit provider and unchanged shared LLVM alias')
provider = providers[0]
provider_raw, provider_offset, provider_text, provider_directory = pe(provider)
imports_rva, _ = provider_directory(1)
if not imports_rva:
    raise SystemExit('llvmjit provider has no dynamic imports')
imports = []
position = provider_offset(imports_rva)
while True:
    descriptor = struct.unpack_from('<IIIII', provider_raw, position)
    if not any(descriptor):
        break
    imports.append(provider_text(descriptor[3]))
    position += 20
llvm_imports = [name for name in imports if 'llvm' in name.lower()]
if [name.lower() for name in llvm_imports] != [dll.name.lower()]:
    raise SystemExit('Provider does not import exactly the verified shared LLVM DLL: ' + repr(llvm_imports))
dependencies_path = workspace / 'postgres/build/meson-info/intro-dependencies.json'
dependencies = json.loads(dependencies_path.read_text())
llvm_dependencies = [item for item in dependencies if item.get('name', '').lower() == 'llvm']
if len(llvm_dependencies) != 1:
    raise SystemExit('Expected exactly one actual Meson LLVM dependency receipt')
dependency = llvm_dependencies[0]
link_args = dependency.get('link_args', [])
normalized_link_args = [argument.replace('\\', '/').lower() for argument in link_args]
if not any((build / 'lib').as_posix().lower() in argument for argument in normalized_link_args):
    raise SystemExit('Meson LLVM dependency does not use owned libdir')
if not any(dll.stem.removeprefix('lib').lower() in argument for argument in normalized_link_args):
    raise SystemExit('Meson LLVM dependency does not name actual shared LLVM import')
save('llvm-provider-import-verification.json', {
    'status': 'actual-provider-dynamic-import-verified-runtime-pending',
    'provider': str(provider), 'provider_sha256': sha(provider_raw), 'imports': imports,
    'llvm_dll': str(dll), 'llvm_dll_sha256': sha(raw),
    'import_library': str(import_library), 'import_library_sha256': sha(import_library.read_bytes()),
    'actual_meson_llvm_dependency': dependency,
    'meson_dependency_receipt': str(dependencies_path),
    'meson_dependency_receipt_sha256': sha(dependencies_path.read_bytes()),
    'scope': 'Actual provider PE imports match real DLL. Full SQL, jump controls and both regression modes remain mandatory.'})
