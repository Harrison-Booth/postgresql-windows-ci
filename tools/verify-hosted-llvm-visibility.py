"""Hosted-only early visibility/ABI gates, not LLVM or PostgreSQL test evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def owned(path, root):
    path = Path(os.path.abspath(path))
    root = Path(os.path.abspath(root))
    if not path.is_relative_to(root):
        raise RuntimeError(f"Path outside owned root: {path}")
    for part in [path, *path.parents]:
        if part.exists() and (part.is_symlink() or
                getattr(part.stat(), "st_file_attributes", 0) & 0x400):
            raise RuntimeError(f"Reparse path: {part}")
    return path


def pe_metadata(path):
    """Read actual PE32+ x64 exports/imports without executing its code."""
    raw = Path(path).read_bytes()
    nt = struct.unpack_from("<I", raw, 0x3C)[0]
    if raw[:2] != b"MZ" or raw[nt:nt + 4] != b"PE\0\0":
        raise RuntimeError("Invalid PE image")
    machine, sections = struct.unpack_from("<HH", raw, nt + 4)
    optional = nt + 24
    if machine != 0x8664 or struct.unpack_from("<H", raw, optional)[0] != 0x20B:
        raise RuntimeError("Expected native x64 PE32+")
    section_start = optional + struct.unpack_from("<H", raw, nt + 20)[0]
    table = []
    for index in range(sections):
        at = section_start + index * 40
        size, rva, disk_size, disk = struct.unpack_from("<IIII", raw, at + 8)
        table.append((rva, max(size, disk_size), disk))

    def offset(rva):
        for start, size, disk in table:
            if start <= rva < start + size:
                return disk + rva - start
        raise RuntimeError(f"Unmapped PE RVA: {rva}")

    def text(rva):
        start = offset(rva)
        return raw[start:raw.index(b"\0", start)].decode("ascii")

    exports = []
    export_rva = struct.unpack_from("<I", raw, optional + 112)[0]
    if export_rva:
        at = offset(export_rva)
        count = struct.unpack_from("<I", raw, at + 24)[0]
        names_rva = struct.unpack_from("<I", raw, at + 32)[0]
        exports = [text(struct.unpack_from("<I", raw, offset(names_rva) + i * 4)[0])
                   for i in range(count)]
    imports = []
    import_rva = struct.unpack_from("<I", raw, optional + 120)[0]
    if import_rva:
        at = offset(import_rva)
        while any(raw[at:at + 20]):
            imports.append(text(struct.unpack_from("<I", raw, at + 12)[0]))
            at += 20
    return {"pe_machine": "0x8664", "sha256": sha(path),
            "exports": exports, "imports": imports}


def coff_directives(path):
    raw = Path(path).read_bytes()
    machine, sections = struct.unpack_from("<HH", raw)
    if machine != 0x8664:
        raise RuntimeError("Expected native x64 COFF object")
    start = 20 + struct.unpack_from("<H", raw, 16)[0]
    directives = []
    for index in range(sections):
        at = start + index * 40
        if raw[at:at + 8].rstrip(b"\0") == b".drectve":
            size, disk = struct.unpack_from("<II", raw, at + 16)
            directives.append(raw[disk:disk + size].decode("ascii"))
    return "\n".join(directives)


def command_tokens(entry):
    # posix=False preserves unquoted Windows backslashes and quoted paths.
    return entry.get("arguments") or [x.strip('"') for x in
                                      shlex.split(entry["command"], posix=False)]


def macro_definitions(text):
    # Empty object-like defines are meaningful. Never consume a following line.
    return {name: value or "" for name, value in re.findall(
        r"^#define[ \t]+(\w+)(?:[ \t]+([^\r\n]*))?\r?$", text, re.M)}


def output_target(entry, tokens, build):
    """Use the produced object, never a PCH or include path, as target identity."""
    directory = owned(entry["directory"], build)
    output = owned(entry["output"], build)
    options = [index for index, token in enumerate(tokens) if token == "-o"]
    if len(options) != 1 or options[0] + 1 >= len(tokens):
        raise RuntimeError("Expected exactly one actual compile output argument")
    argument = Path(tokens[options[0] + 1])
    actual = owned(argument if argument.is_absolute() else directory / argument, build)
    if actual != output:
        raise RuntimeError("Compile output argument differs from authoritative output field")
    targets = re.findall(r"(?:^|[/\\])CMakeFiles[/\\]([^/\\]+)\.dir(?:[/\\]|$)",
                         str(output.relative_to(Path(os.path.abspath(build)))))
    if len(targets) != 1:
        raise RuntimeError(f"Ambiguous compile output target: {output}")
    return targets[0]


def component_flags(tokens, cpp):
    required = {"-fvisibility=hidden", "-DLLVM_EXPORTS"}
    if cpp:
        required.add("-fvisibility-inlines-hidden")
    if not required.issubset(tokens):
        raise RuntimeError(f"Missing component visibility/export flags: {required - set(tokens)}")
    # Release emits -DNDEBUG; upstream assertions must later undo that define.
    ndebug = None
    for token in tokens:
        if re.fullmatch(r"-DNDEBUG(?:=.*)?", token):
            ndebug = True
        elif token == "-UNDEBUG":
            ndebug = False
    if ndebug is not False:
        raise RuntimeError("Component does not explicitly retain assertions")
    visibility = [x for x in tokens if x.startswith("-fvisibility=")]
    if visibility[-1:] != ["-fvisibility=hidden"]:
        raise RuntimeError("Component visibility overridden after hidden setting")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["probe", "configuration"])
    parser.add_argument("--toolchain-root", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--build")
    args = parser.parse_args()
    if (os.environ.get("GITHUB_ACTIONS") != "true" or
            os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted" or os.name != "nt"):
        raise SystemExit("Only GitHub-hosted Windows execution is permitted")
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
    evidence = owned(args.evidence, workspace)
    if not evidence.is_dir():
        raise RuntimeError("Existing owned evidence directory required")
    root = Path(args.toolchain_root).resolve()
    if root.name.lower() != "ucrt64":
        raise RuntimeError("UCRT64 toolchain required")
    tools = {name: owned(root / "bin" / (name + ".exe"), root)
             for name in ["clang", "clang++", "gcc", "g++", "ld"]}
    receipt = {"status": "failed", "mode": args.mode,
               "scope": "Early static/compile preconditions only; full LLVM/JIT/runtime still required",
               "run_id": os.environ.get("GITHUB_RUN_ID"),
               "tracker_revision": os.environ.get("GITHUB_SHA"), "commands": []}
    receipt_path = evidence / ("llvm-visibility-" + args.mode + ".json")

    def run(label, argv, stdin=None):
        log = evidence / ("llvm-precondition-" + label + ".log")
        item = {"label": label, "argv": [str(x) for x in argv], "timeout_seconds": 60,
                "timed_out": False, "exit_code": None}
        receipt["commands"].append(item)
        start = time.monotonic()
        proc = None
        try:
            with log.open("wb") as out:
                proc = subprocess.Popen(item["argv"], stdin=subprocess.PIPE if stdin is not None
                                        else subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
                item["pid"] = proc.pid
                try:
                    proc.communicate(stdin, timeout=60)
                except subprocess.TimeoutExpired:
                    item["timed_out"] = True
                    # Retained exact owned child only, never global process selection.
                    cleanup = [str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"),
                               "/PID", str(proc.pid), "/T", "/F"]
                    item["cleanup_command"] = cleanup
                    try:
                        killed = subprocess.run(cleanup, stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT, timeout=15)
                        cleanup_log = evidence / ("llvm-precondition-" + label + "-cleanup.log")
                        cleanup_log.write_bytes(killed.stdout)
                        item["cleanup_exit_code"] = killed.returncode
                        item["cleanup_log_sha256"] = sha(cleanup_log)
                    except Exception as exc:
                        item["cleanup_error"] = repr(exc)
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        item["cleanup_wait_timed_out"] = True
                    raise RuntimeError(f"Precondition timeout: {label}")
                item["exit_code"] = proc.returncode
            if proc.returncode:
                raise RuntimeError(f"Precondition failed: {label}, exit {proc.returncode}")
            return log.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            item["error"] = repr(exc)
            raise
        finally:
            if proc is not None:
                item["exit_code"] = proc.poll()
            item["elapsed_seconds"] = time.monotonic() - start
            item["log_path"] = str(log)
            if log.exists():
                item["log_sha256"] = sha(log)

    try:
        if args.mode == "probe":
            receipt["tools"] = {}
            for name, path in tools.items():
                receipt["tools"][name] = {"path": str(path), "sha256": sha(path),
                                          "pe": pe_metadata(path),
                                          "version": run(name + "-version", [path, "--version"])}
            receipt["targets"] = {}
            receipt["stdlib"] = {}
            for name in ["clang", "clang++", "gcc", "g++"]:
                target = run(name + "-target", [tools[name], "-dumpmachine"]).strip()
                if not (target.startswith("x86_64-") and
                        ("mingw" in target or "windows-gnu" in target) and "msvc" not in target):
                    raise RuntimeError(f"Unexpected non-GNU x64 target: {target}")
                receipt["targets"][name] = target
            for name in ["clang++", "g++"]:
                macros = run(name + "-stdlib", [tools[name], "-dM", "-E", "-x", "c++",
                                                "-include", "string", "-"], b"")
                parsed = macro_definitions(macros)
                if "_LIBCPP_VERSION" in parsed or "__GLIBCXX__" not in parsed:
                    raise RuntimeError("Both compilers must use GNU libstdc++")
                receipt["stdlib"][name] = {key: parsed.get(key) for key in
                    ["__GLIBCXX__", "_GLIBCXX_USE_CXX11_ABI", "_UCRT", "__MSVCRT_VERSION__"]}
            for key in ["__GLIBCXX__", "_GLIBCXX_USE_CXX11_ABI"]:
                if receipt["stdlib"]["clang++"][key] != receipt["stdlib"]["g++"][key]:
                    raise RuntimeError(f"C++ standard-library configuration differs: {key}")
            selected_ld = run("clang-selected-linker", [tools["clang++"], "-print-prog-name=ld"]).strip()
            # The compiler can select the target-tools copy rather than bin/ld.exe.
            linker = owned(selected_ld if Path(selected_ld).is_absolute()
                           else root / "bin" / selected_ld, root)
            if not linker.is_file() and linker.suffix.lower() != ".exe":
                linker = owned(str(linker) + ".exe", root)
            receipt["selected_linker"] = {"path": str(linker), "sha256": sha(linker),
                                          "version": run("selected-linker-version", [linker, "--version"])}
            probe = evidence / "llvm-visibility-probe"
            probe.mkdir()  # Fresh exact owned directory; never overwrite prior evidence.
            source = probe / "visibility.cpp"
            source.write_text('#include <string>\n#include <stdexcept>\n'
                'extern "C" __attribute__((visibility("default")))\n'
                'int codex_visible_api(const char *s) { return std::runtime_error(std::string(s)).what()[0]; }\n'
                'extern "C" __attribute__((visibility("hidden")))\n'
                'int codex_hidden_impl(void) { return 23; }\n', encoding="utf-8")
            obj, dll = probe / "visibility.o", probe / "visibility.dll"
            run("visibility-compile", [tools["clang++"], "-v", "-O0", "-fvisibility=hidden",
                "-fvisibility-inlines-hidden", "-c", source, "-o", obj])
            directives = coff_directives(obj)
            receipt["coff"] = {"sha256": sha(obj), "directives": directives}
            if not re.search(r"-exclude-symbols:(?:\"codex_hidden_impl\"|codex_hidden_impl)(?:\s|$)", directives):
                raise RuntimeError("Hidden symbol lacks compiler-generated COFF exclusion")
            run("visibility-link", [tools["clang++"], "-v", "-shared", obj, "-Wl,--export-all-symbols",
                                     "-o", dll])
            pe = pe_metadata(dll)
            receipt["probe_dll"] = pe
            if "codex_visible_api" not in pe["exports"] or "codex_hidden_impl" in pe["exports"]:
                raise RuntimeError("Actual export-all DLL did not preserve public/private visibility")
            if not any(x.lower() == "ucrtbase.dll" or x.lower().startswith("api-ms-win-crt-")
                       for x in pe["imports"]):
                raise RuntimeError("Probe DLL has no recorded UCRT import")
            if "libstdc++-6.dll" not in [name.lower() for name in pe["imports"]]:
                raise RuntimeError("Probe does not import the expected GNU C++ shared runtime")
            # Retain actual runtime bytes' identities, including their own CRT imports.
            receipt["toolchain_runtime_libraries"] = {}
            pending = [name for name in pe["imports"] if (root / "bin" / name).is_file()]
            while pending:
                name = pending.pop()
                if name.lower() in receipt["toolchain_runtime_libraries"]:
                    continue
                runtime = owned(root / "bin" / name, root)
                metadata = pe_metadata(runtime)
                receipt["toolchain_runtime_libraries"][name.lower()] = {
                    "path": str(runtime), **metadata}
                pending.extend(child for child in metadata["imports"]
                               if (root / "bin" / child).is_file())
            receipt["source_sha256"] = sha(source)
        else:
            if not args.build:
                raise RuntimeError("Configuration mode requires --build")
            build = owned(args.build, workspace)
            probe_receipt = json.loads((evidence / "llvm-visibility-probe.json").read_text())
            if probe_receipt["status"] != "verified" or probe_receipt["run_id"] != receipt["run_id"]:
                raise RuntimeError("Current-run verified visibility probe required")
            for name, tool in probe_receipt["tools"].items():
                if Path(tool["path"]) != tools[name] or sha(tools[name]) != tool["sha256"]:
                    raise RuntimeError("Toolchain changed after probe")
            for runtime in probe_receipt["toolchain_runtime_libraries"].values():
                if sha(owned(runtime["path"], root)) != runtime["sha256"]:
                    raise RuntimeError("Toolchain runtime changed after probe")
            if sha(owned(probe_receipt["selected_linker"]["path"], root)) != probe_receipt["selected_linker"]["sha256"]:
                raise RuntimeError("Selected linker changed after probe")
            cache_path, commands_path = build / "CMakeCache.txt", build / "compile_commands.json"
            cache = dict(re.findall(r"^([^/#\r\n:]+):[^=\r\n]+=(.*)$",
                                    cache_path.read_text(), re.M))
            for key, expected in {"LLVM_BUILD_LLVM_DYLIB": "ON", "LLVM_BUILD_LLVM_DYLIB_VIS": "ON",
                "LLVM_ENABLE_ASSERTIONS": "ON", "LLVM_LINK_LLVM_DYLIB": "OFF",
                "CMAKE_EXPORT_COMPILE_COMMANDS": "ON", "CMAKE_BUILD_TYPE": "Release"}.items():
                if cache.get(key) != expected:
                    raise RuntimeError(f"Unexpected cache setting {key}: {cache.get(key)}")
            if cache.get("BUILD_SHARED_LIBS", "OFF") not in ["OFF", "FALSE", "0", ""]:
                raise RuntimeError("Expected canonical monolithic shared-LLVM component build")
            if "CMAKE_CXX_VISIBILITY_PRESET" in cache:
                raise RuntimeError("Global visibility preset overrides upstream component policy")
            for key, compiler in [("CMAKE_C_COMPILER", "clang"), ("CMAKE_CXX_COMPILER", "clang++")]:
                if Path(cache[key]).resolve() != tools[compiler].resolve():
                    raise RuntimeError(f"Unexpected LLVM compiler: {cache[key]}")
            components = cache["LLVM_DYLIB_COMPONENTS"].split(";")
            if len(components) != 68 or len(set(components)) != 68:
                raise RuntimeError("Required configured 68-component closure changed")
            rows = json.loads(commands_path.read_text())
            checked = {"LLVM" + component: [] for component in components}
            assembly = []
            for row in rows:
                tokens = command_tokens(row)
                target = output_target(row, tokens, build)
                if target not in checked:
                    continue
                if Path(tokens[0]).resolve() not in [tools["clang"].resolve(), tools["clang++"].resolve()]:
                    raise RuntimeError("Component compile does not use selected LLVM compiler")
                suffix = Path(row["file"]).suffix.lower()
                if suffix in [".s", ".asm"]:
                    source = owned(row["file"], workspace)
                    assembly.append({"target": target, "source_sha256": sha(source), **row})
                    continue
                if suffix not in [".c", ".cpp", ".cc", ".cxx"]:
                    raise RuntimeError(f"Unrecognized component source language: {row['file']}")
                try:
                    component_flags(tokens, suffix != ".c")
                except RuntimeError:
                    receipt["failed_component_command"] = {"target": target, **row}
                    raise
                checked[target].append(row)
            missing = [name for name, entries in checked.items() if not entries]
            if missing:
                raise RuntimeError(f"No actual compile commands for closure components: {missing}")
            receipt.update({"cache_sha256": sha(cache_path), "compile_commands_sha256": sha(commands_path),
                            "probe_receipt_sha256": sha(evidence / "llvm-visibility-probe.json"),
                            "configured_components": components,
                            "component_command_counts": {key: len(value) for key, value in checked.items()},
                            "assembly_commands": assembly,
                            "checked_component_commands": checked})
        receipt["status"] = "verified"
    except Exception as exc:
        receipt["error"] = repr(exc)
        raise
    finally:
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
