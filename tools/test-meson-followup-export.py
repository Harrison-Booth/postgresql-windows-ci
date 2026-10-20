"""Run and strictly verify Meson v4 include-export controls on hosted Windows."""
import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys


def patch_id(path: pathlib.Path) -> str:
    args.output.mkdir(parents=True, exist_ok=False)
    case_output = args.output / "cases"
    proc = subprocess.run(
        ["git", "patch-id", "--stable"],
        input=path.read_bytes(),
        capture_output=True,
        check=True,
    )
    return proc.stdout.decode("ascii").split()[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=pathlib.Path, required=True)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--meson", type=pathlib.Path, required=True)
    parser.add_argument("--pkgconfig", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()

    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("Native export checks require standard GitHub-hosted runners")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    patch_dir = args.manifest.parent
    for record in [manifest["prerequisite"], *manifest["followups"]]:
        patch = patch_dir / record["file"]
        actual_hash = hashlib.sha256(patch.read_bytes()).hexdigest()
        if actual_hash != record["sha256"]:
            raise RuntimeError(f"Patch hash mismatch: {record['file']}")
        if patch_id(patch) != record["patch_id"]:
            raise RuntimeError(f"Patch ID mismatch: {record['file']}")

    proc = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(__file__).with_name("test-author-v4.py")),
            "--source",
            str(args.source),
            "--meson",
            str(args.meson),
            "--pkgconfig",
            str(args.pkgconfig),
            "--output",
            str(case_output),
            "--native",
        ],
        capture_output=True,
        timeout=1800,
    )
    (args.output / "harness.stdout.log").write_bytes(proc.stdout)
    (args.output / "harness.stderr.log").write_bytes(proc.stderr)
    result_path = case_output / "results.json"
    if not result_path.exists():
        receipt = {
            "harness_exit": proc.returncode,
            "failures": ["The controlled harness did not produce its results file; inspect saved stdout and stderr."],
            "scope": "Six controlled CMake/pkg-config include-export cases. No full server or PGXS runtime claim.",
        }
        (args.output / "strict-verification.json").write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
        )
        return 1
    results = json.loads(result_path.read_text(encoding="utf-8"))
    rows = results["records"]
    expected_cases = {
        "disabled",
        "pkgconfig",
        "pkgconfig-space",
        "pkgconfig-override",
        "cmake",
        "cmake-space",
    }
    if {row["case"] for row in rows} != expected_cases or len(rows) != len(expected_cases):
        raise RuntimeError("The expected six include-export cases did not run")

    failures = []
    for row in rows:
        case = row["case"]
        for key in ("setup_exit",):
            if row.get(key) != 0:
                failures.append(f"{case}: {key}={row.get(key)}")
        if row.get("dependency_metadata_valid") is not True:
            failures.append(f"{case}: dependency metadata did not preserve expected include directories")
        if row.get("exported_include_argv_matches") is not True:
            failures.append(f"{case}: exported include flags did not preserve paths as arguments")
        for key in ("macro_compile_exit", "macro_runtime_exit", "exported_header_compile_exit"):
            if row.get(key) != 0:
                failures.append(f"{case}: {key}={row.get(key)}")
        if row.get("pg_config_macro_roundtrip") is not True:
            failures.append(f"{case}: pg_config include flags did not survive the compiler macro round trip")

    receipt = {
        "runner_architecture": os.environ.get("RUNNER_ARCH"),
        "meson_version_under_test": str(args.meson),
        "patches": [
            {
                "file": record["file"],
                "sha256": record["sha256"],
                "patch_id": record["patch_id"],
            }
            for record in [manifest["prerequisite"], *manifest["followups"]]
        ],
        "cases": rows,
        "failures": failures,
        "scope": "Six controlled CMake/pkg-config include-export cases, including arrays, spaces, and pkg-config path override. No full server or PGXS runtime claim.",
    }
    (args.output / "strict-verification.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"cases": len(rows), "failures": failures}))
    return 0 if proc.returncode == 0 and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
