"""Compare actual psql header selection before and after the focused fix."""
import argparse
import json
import os
import pathlib
import subprocess
import sys


def run(command, *, timeout, env=None):
    return subprocess.run(command, capture_output=True, timeout=timeout, env=env)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=pathlib.Path, required=True)
    parser.add_argument("--architecture", choices=("x64", "arm64"), required=True)
    parser.add_argument("--meson", type=pathlib.Path, required=True)
    parser.add_argument("--baseline", type=pathlib.Path, required=True)
    parser.add_argument("--candidate", type=pathlib.Path, required=True)
    args = parser.parse_args()

    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("PostgreSQL builds are restricted to standard GitHub-hosted runners")

    evidence = args.workspace / "evidence" / "pg-include-order"
    evidence.mkdir(parents=True, exist_ok=False)
    canary = args.workspace / "canary-include"
    canary.mkdir(exist_ok=False)
    marker = "CF7300 selected external libpq-fe.h instead of the PostgreSQL header"
    (canary / "libpq-fe.h").write_text(f"#error {marker}\n", encoding="ascii")

    records = []
    for name, source in (("v4-baseline", args.baseline), ("v4-plus-followups", args.candidate)):
        build = args.workspace / f"pg-build-{name}"
        setup = [
            sys.executable,
            str(args.meson),
            "setup",
            str(build),
            str(source),
            "-Dauto_features=disabled",
            "-Dssl=none",
            "-Dicu=disabled",
            "-Dtap_tests=disabled",
            f"-Dextra_include_dirs={canary}",
        ]
        setup_result = run(setup, timeout=300)
        (evidence / f"{name}-setup.log").write_bytes(setup_result.stdout + setup_result.stderr)
        row = {
            "revision": name,
            "architecture": args.architecture,
            "setup_exit": setup_result.returncode,
            "command": setup,
        }
        if setup_result.returncode == 0:
            build_result = run(
                [sys.executable, str(args.meson), "compile", "-C", str(build), "-j", "2", "psql"],
                timeout=900,
            )
            log = build_result.stdout + build_result.stderr
            (evidence / f"{name}-psql-build.log").write_bytes(log)
            row.update(
                psql_build_exit=build_result.returncode,
                canary_header_error_seen=marker.encode("ascii") in log,
                server_or_pg_ctl_started=False,
            )
            commands_file = build / "compile_commands.json"
            if commands_file.exists():
                commands = json.loads(commands_file.read_text(encoding="utf-8"))
                selected = [
                    command
                    for command in commands
                    if pathlib.Path(command["file"]).name in {"startup.c", "common.c", "config_info.c"}
                ]
                (evidence / f"{name}-selected-compile-commands.json").write_text(
                    json.dumps(selected, indent=2) + "\n", encoding="utf-8"
                )
                row["selected_compile_command_count"] = len(selected)
        records.append(row)
        (evidence / "results.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")

    expected = (
        len(records) == 2
        and all(row.get("setup_exit") == 0 for row in records)
        and records[0].get("psql_build_exit") != 0
        and records[0].get("canary_header_error_seen") is True
        and records[1].get("psql_build_exit") == 0
        and records[1].get("canary_header_error_seen") is False
    )
    (evidence / "strict-verification.json").write_text(
        json.dumps(
            {
                "architecture": args.architecture,
                "expected_control_result": expected,
                "records": records,
                "scope": "Actual psql compile with a deliberately invalid competing libpq-fe.h. Baseline must hit the canary; patched candidate must select PostgreSQL's header. No server or pg_ctl process is run.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0 if expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
