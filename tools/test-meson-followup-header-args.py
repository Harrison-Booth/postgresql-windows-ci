"""Exercise Meson's find_library header probes with and without header_args."""
import argparse
import json
import os
import pathlib
import subprocess
import sys


PROBES = [
    ("ldap", "ldap.h"),
    ("ldap-r", "ldap.h"),
    ("ssl", "openssl/ssl.h,openssl/err.h"),
    ("ossp-uuid", "uuid/uuid.h"),
    ("uuid", "uuid/uuid.h"),
    ("intl", "libintl.h"),
]


def run(command, timeout=60):
    return subprocess.run(command, capture_output=True, timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=pathlib.Path, required=True)
    parser.add_argument("--meson", type=pathlib.Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source", type=pathlib.Path, required=True)
    args = parser.parse_args()

    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("Meson compiler probes require standard GitHub-hosted runners")

    root = args.workspace / "evidence" / ("fallback-header-args-" + args.version)
    root.mkdir(parents=True, exist_ok=False)
    include = root / "external headers"
    postgres_include = root / "postgres headers"
    library_dir = root / "libraries"
    include.mkdir()
    postgres_include.mkdir()
    library_dir.mkdir()

    for name, headers in PROBES:
        for header in headers.split(","):
            path = include / header
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("/* controlled Meson find_library header probe */\n", encoding="ascii")

        stub = root / (name.replace("-", "_") + ".c")
        obj = root / (name.replace("-", "_") + ".obj")
        stub.write_text("int meson_header_args_control(void) { return 0; }\n", encoding="ascii")
        compiled = run(["cl", "/nologo", "/c", str(stub), "/Fo" + str(obj)])
        (root / (name + "-cl.log")).write_bytes(compiled.stdout + compiled.stderr)
        if compiled.returncode != 0:
            raise RuntimeError(f"Could not compile the {name} probe library")

        for suffix in ("control", "with-args"):
            library = library_dir / f"{name}-{suffix}.lib"
            archived = run(["lib", "/nologo", "/out:" + str(library), str(obj)])
            (root / f"{name}-{suffix}-lib.log").write_bytes(archived.stdout + archived.stderr)
            if archived.returncode != 0:
                raise RuntimeError(f"Could not create the {name} probe library")

    options = "option('probe_include_dir', type: 'string', value: '')\n"
    (root / "meson_options.txt").write_text(options, encoding="ascii")
    lines = [
        "project('meson-find-library-header-args', 'c')",
        "cc = meson.get_compiler('c')",
        "test_c_args = ['/X', '-I' + get_option('probe_include_dir')]",
        "postgres_inc = [include_directories('postgres headers')]",
        "library_dir = meson.current_source_dir() / 'libraries'",
    ]
    for index, (name, headers) in enumerate(PROBES):
        header_list = "['" + "', '".join(headers.split(",")) + "']"
        control = f"probe_{index}_control"
        candidate = f"probe_{index}_candidate"
        lines.extend(
            [
                f"{control} = cc.find_library('{name}-control', dirs: [library_dir],",
                f"  has_headers: {header_list}, header_args: ['/X'],",
                "  header_include_directories: postgres_inc, required: false)",
                f"{candidate} = cc.find_library('{name}-with-args', dirs: [library_dir],",
                f"  has_headers: {header_list}, header_args: test_c_args,",
                "  header_include_directories: postgres_inc, required: false)",
                f"if {control}.found()",
                f"  error('{name}: control unexpectedly found external headers')",
                "endif",
                f"if not {candidate}.found()",
                f"  error('{name}: header_args did not expose external headers')",
                "endif",
            ]
        )
    (root / "meson.build").write_text("\n".join(lines) + "\n", encoding="ascii")

    source_text = (args.source / "meson.build").read_text(encoding="utf-8")
    required_sites = (
        "has_headers: 'ldap.h', header_args: test_c_args",
        "has_headers: ['openssl/ssl.h', 'openssl/err.h']",
        "has_headers: uuidheader, header_args: test_c_args",
        "has_headers: ['libintl.h'], required: nlsopt,",
    )
    missing = [site for site in required_sites if site not in source_text]
    if source_text.count("header_args: test_c_args") != 6:
        missing.append("expected six fallback probes to use test_c_args")
    if source_text.count("has_headers: 'ldap.h', header_args: test_c_args") != 2:
        missing.append("expected both LDAP probes to use test_c_args")
    if source_text.count("has_headers: uuidheader, header_args: test_c_args") != 2:
        missing.append("expected both UUID probes to use test_c_args")
    receipt = {
        "meson_version": args.version,
        "runner_architecture": os.environ.get("RUNNER_ARCH"),
        "source_callsite_patterns_present": len(missing) == 0,
        "missing_source_patterns": missing,
        "probe_groups": [name for name, _ in PROBES],
        "expected": "each isolated control is not found without the external include directory and its paired probe is found with that directory",
        "scope": "Focused Meson find_library(has_headers:) compiler probes for LDAP, OpenSSL, UUID, and libintl patterns. Not a full PostgreSQL optional-dependency build.",
    }
    if missing:
        (root / "strict-verification.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return 1

    build = root / "build"
    setup = run(
        [
            sys.executable,
            str(args.meson),
            "setup",
            str(build),
            str(root),
            "-Dprobe_include_dir=" + include.resolve().as_posix(),
        ],
        timeout=240,
    )
    (root / "meson-setup.log").write_bytes(setup.stdout + setup.stderr)
    receipt["setup_exit"] = setup.returncode
    receipt["probes"] = []
    if setup.returncode == 0:
        for index, (name, _) in enumerate(PROBES):
            receipt["probes"].append(
                {"name": name, "control_found": False, "with_header_args_found": True}
            )
    (root / "strict-verification.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"meson": args.version, "setup_exit": setup.returncode, "groups": len(PROBES)}))
    return 0 if setup.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
