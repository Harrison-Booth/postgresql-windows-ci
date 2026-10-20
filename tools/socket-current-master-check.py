"""Hosted current-master build and TAP validation for CF6207."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
import time

BASE = "776df8a6d7c42282825d658ff631509f033e8f34"
EXPECTED_TREE = "d1ed458cd5faee39a92c58e9bedb5828e3d333a3"
PRODUCTION_FILES = [
    "src/backend/port/win32/socket.c",
    "src/backend/tcop/backend_startup.c",
]
ASSERTIONS = [
    "COPY helper publishes exact PID and creation time",
    "helper alive before shutdown",
    "owned postmaster stopped",
    "precondition: COPY helper remains alive after shutdown",
    "backend client closes while COPY helper is alive",
    "listener port can be rebound while COPY helper is alive",
    "exact helper exits after release",
    "port reusable after helper and COPY shell exit",
    "client transport closed after helper release",
]


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def command(name, args, log, cwd=None, timeout=300, allowed=(0,)):
    entry = {"name": name, "args": [str(x) for x in args], "timeout_seconds": timeout}
    started = time.monotonic()
    child = None
    try:
        with log.open("wb") as output:
            child = subprocess.Popen(entry["args"], cwd=cwd, stdin=subprocess.DEVNULL,
                                     stdout=output, stderr=subprocess.STDOUT)
            entry["owned_pid"] = child.pid
            try:
                entry["exit_code"] = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                entry["timed_out"] = True
                subprocess.run(["C:/Windows/System32/taskkill.exe", "/PID",
                                str(child.pid), "/T", "/F"],
                               capture_output=True, timeout=20)
                child.wait(timeout=20)
                raise RuntimeError(name + " exceeded its deadline")
        entry["elapsed_seconds"] = round(time.monotonic() - started, 2)
        entry["log_sha256"] = sha256(log.read_bytes())
        if entry["exit_code"] not in allowed:
            raise RuntimeError(name + " returned " + str(entry["exit_code"]))
        return entry
    finally:
        if child is not None:
            entry["observed_controller_exit"] = child.poll()
        with (log.parent / "commands.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, sort_keys=True) + "\n")


def tap_results(text):
    found = {}
    for line in text.splitlines():
        match = re.match(r"\s*(not )?ok\s+(\d+)\s+-\s+(.+?)\s*$", line)
        if match:
            found[int(match.group(2))] = {
                "ok": match.group(1) is None,
                "name": match.group(3),
            }
    return found


def run_tap(python, build, evidence, label, expect_baseline=False):
    log = evidence / (label + "-meson-test.log")
    result = command(
        label + "-socket-tap",
        [python, "-m", "mesonbuild.mesonmain", "test", "-C", build,
         "--no-rebuild", "--print-errorlogs", "--verbose",
         "pg_ctl/005_socket_handle_inheritance"],
        log, timeout=1200, allowed=(0, 1) if expect_baseline else (0,))
    testlog = build / "meson-logs" / "testlog.txt"
    if testlog.is_file():
        (evidence / (label + "-testlog.txt")).write_bytes(testlog.read_bytes())
        text = log.read_text(encoding="utf-8", errors="replace") + "\n" + testlog.read_text(
            encoding="utf-8", errors="replace")
    else:
        text = log.read_text(encoding="utf-8", errors="replace")
    results = tap_results(text)
    if sorted(results) != list(range(1, 10)):
        raise RuntimeError(label + " did not report all nine TAP assertions")
    names = [results[n]["name"] for n in range(1, 10)]
    if names != ASSERTIONS:
        raise RuntimeError(label + " TAP assertion names/order differ: " + repr(names))
    failed = [results[n]["name"] for n in range(1, 10) if not results[n]["ok"]]
    if expect_baseline:
        expected = [
            "backend client closes while COPY helper is alive",
            "listener port can be rebound while COPY helper is alive",
        ]
        if failed != expected:
            raise RuntimeError("Baseline control did not fail only the two socket-lifetime assertions: " + repr(failed))
    elif failed:
        raise RuntimeError("Candidate TAP assertions failed: " + repr(failed))
    result["tap_assertions"] = results
    result["expected_baseline_failure_control"] = expect_baseline
    return result


def run_setup(python, build, evidence, label):
    """Install the just-built tree and create the initdb template before TAP."""
    log = evidence / (label + "-setup-suite.log")
    result = command(
        label + "-setup-suite",
        [python, "-m", "mesonbuild.mesonmain", "test", "-C", build,
         "--no-rebuild", "--suite", "setup", "--print-errorlogs", "--verbose"],
        log, timeout=900)
    template = build / "tmp_install" / "initdb-template"
    if not template.is_dir() or not any(template.iterdir()):
        raise RuntimeError(label + " setup suite did not create a usable initdb template")
    readonly = []
    for path in template.rglob("*"):
        if path.is_file():
            mode = path.stat().st_mode
            if not mode & stat.S_IWRITE:
                os.chmod(path, mode | stat.S_IWRITE)
                readonly.append(str(path.relative_to(template)))
    hba = template / "pg_hba.conf"
    if not hba.is_file():
        raise RuntimeError(label + " initdb template has no pg_hba.conf")
    with hba.open("r+b") as stream:
        stream.seek(0)
        stream.flush()
    result["initdb_template"] = str(template)
    result["initdb_template_file_count"] = sum(1 for path in template.rglob("*") if path.is_file())
    result["readonly_files_made_writable"] = readonly
    result["pg_hba_write_preflight"] = True
    return result


def preflight_generated_cube_sources(build, evidence):
    """Run the two unrelated cube generators in isolation and retain commands."""
    log = evidence / "cube-generator-preflight.log"
    result = command(
        "cube-generator-preflight",
        ["ninja", "-C", build, "-v", "contrib/cube/cubeparse.c",
         "contrib/cube/cubeparse.h", "contrib/cube/cubescan.c"],
        log, timeout=180)
    expected = [
        build / "contrib" / "cube" / "cubeparse.c",
        build / "contrib" / "cube" / "cubeparse.h",
        build / "contrib" / "cube" / "cubescan.c",
    ]
    missing = [str(path) for path in expected if not path.is_file()]
    result["declared_outputs"] = [
        {"path": str(path), "exists": path.is_file(),
         "size_bytes": path.stat().st_size if path.is_file() else None}
        for path in expected
    ]
    (evidence / "cube-generator-outputs.json").write_text(
        json.dumps(result["declared_outputs"], indent=2) + "\n", encoding="utf-8")
    if missing:
        raise RuntimeError("cube generator returned success without its declared outputs: " + repr(missing))
    return result


def main():
    if (os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise RuntimeError("Standard GitHub-hosted Windows runners only")
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--arch", choices=["x64", "arm64"], required=True)
    args = parser.parse_args()
    machine = platform.machine().lower()
    if not ((args.arch == "x64" and machine in ("amd64", "x86_64"))
            or (args.arch == "arm64" and machine in ("arm64", "aarch64"))):
        raise RuntimeError("Native runner architecture mismatch")
    root = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
    source = root / "postgres"
    ci = root / "ci"
    evidence = root / "evidence"
    build = root / "build"
    manifest_path = ci / "tracking/email-readiness-20261017/socket/manifest.json"
    patch_dir = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["upstream_base"] != BASE or manifest["expected_final_tree"] != EXPECTED_TREE:
        raise RuntimeError("Reviewed source pin or result tree changed")
    if subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"], text=True).strip() != BASE:
        raise RuntimeError("PostgreSQL source is not the reviewed upstream base")
    if subprocess.check_output(["git", "-C", source, "status", "--porcelain"], text=True).strip():
        raise RuntimeError("PostgreSQL checkout is not pristine")
    evidence.mkdir(exist_ok=True)
    inputs = []
    patches = []
    for entry in manifest["series"]:
        patch = patch_dir / entry["file"]
        raw = patch.read_bytes()
        if sha256(raw) != entry["sha256"]:
            raise RuntimeError("Patch SHA256 mismatch: " + entry["file"])
        patch_id = subprocess.run(["git", "patch-id", "--stable"], input=raw,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  cwd=source, timeout=30, check=True).stdout.decode().split()[0]
        if patch_id != entry["stable_patch_id"]:
            raise RuntimeError("Stable patch ID mismatch: " + entry["file"])
        inputs.append({"file": entry["file"], "sha256": entry["sha256"],
                       "stable_patch_id": patch_id, "author": entry["author"]})
        patches.append(patch)
    install = root / "installation"
    if build.exists() or install.exists():
        raise RuntimeError("Fresh build and install paths required")
    receipt = {
        "status": "failed",
        "upstream_base": BASE,
        "candidate_tree": EXPECTED_TREE,
        "host_architecture": args.arch,
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_revision": os.environ.get("GITHUB_SHA"),
        "patch_inputs": inputs,
        "scope": manifest["scope"],
        "commands": [],
    }
    try:
        # git am preserves the patch author but also needs an identity for the
        # temporary committer. Set it for this command only; do not modify the
        # hosted runner's global Git configuration.
        applied = command("git-am-exact-series",
                          ["git", "-C", source,
                           "-c", "user.name=PostgreSQL CI validation",
                           "-c", "user.email=ci@example.invalid",
                           "am", "--keep-cr", *patches],
                          evidence / "git-am.log", timeout=120)
        receipt["git_am_committer_identity"] = {
            "name": "PostgreSQL CI validation",
            "email": "ci@example.invalid",
            "scope": "single git am command; patch authors preserved",
        }
        head = subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"], text=True).strip()
        tree = subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD^{tree}"], text=True).strip()
        if tree != EXPECTED_TREE:
            raise RuntimeError("Ordered series produced a different source tree: " + tree)
        command("series-whitespace-check",
                ["git", "-C", source, "diff", "--check", BASE + "..HEAD"],
                evidence / "series-whitespace-check.log", timeout=30)
        receipt["applied_head"] = head
        receipt["candidate_tree"] = tree
        receipt["git_am"] = applied
        python = sys.executable
        meson = [python, "-m", "mesonbuild.mesonmain"]
        configure = [
            *meson, "setup", build, source, "--prefix=" + str(install),
            "--buildtype=debug", "-Db_vscrt=mdd", "-Dauto_features=disabled",
            "-Dssl=none", "-Dicu=disabled", "-Dtap_tests=enabled",
            "-Dcassert=true", "-Dplperl=disabled", "-Dplpython=disabled",
            "-Dpltcl=disabled", "-DPERL=C:/Strawberry/perl/bin/perl.exe",
            "-DTAR=C:/Windows/System32/tar.exe",
        ]
        command("configure-tap-build", configure, evidence / "configure.log", timeout=600)
        receipt["cube_generator_preflight"] = preflight_generated_cube_sources(build, evidence)
        command("build-candidate", [*meson, "compile", "-C", build, "-j", "2"],
                evidence / "build-candidate.log", timeout=2400)
        candidate_setup = run_setup(python, build, evidence, "candidate")
        candidate = run_tap(python, build, evidence, "candidate")

        command("restore-unpatched-socket-source",
                ["git", "-C", source, "checkout", BASE, "--", *PRODUCTION_FILES],
                evidence / "restore-baseline-source.log", timeout=60)
        changed = subprocess.check_output(["git", "-C", source, "diff", "HEAD", "--name-only"],
                                           text=True).splitlines()
        if sorted(changed) != sorted(PRODUCTION_FILES):
            raise RuntimeError("Baseline control changed files outside the two production hunks: " + repr(changed))
        command("build-unpatched-control", [*meson, "compile", "-C", build, "-j", "2"],
                evidence / "build-baseline.log", timeout=1200)
        baseline_setup = run_setup(python, build, evidence, "baseline")
        baseline = run_tap(python, build, evidence, "baseline", expect_baseline=True)

        command("restore-candidate-socket-source",
                ["git", "-C", source, "checkout", "HEAD", "--", *PRODUCTION_FILES],
                evidence / "restore-candidate-source.log", timeout=60)
        if subprocess.check_output(["git", "-C", source, "status", "--porcelain"], text=True).strip():
            raise RuntimeError("Candidate source restoration did not return to a clean exact series")
        command("rebuild-candidate", [*meson, "compile", "-C", build, "-j", "2"],
                evidence / "rebuild-candidate.log", timeout=1200)
        final_candidate_setup = run_setup(python, build, evidence, "final-candidate")
        final_candidate = run_tap(python, build, evidence, "final-candidate")
        receipt["candidate_test"] = candidate
        receipt["candidate_setup"] = candidate_setup
        receipt["baseline_control"] = baseline
        receipt["baseline_setup"] = baseline_setup
        receipt["final_candidate_setup"] = final_candidate_setup
        receipt["final_candidate_test"] = final_candidate
        receipt["status"] = "passed-current-master-socket-tap-and-baseline-control"
    except Exception as error:
        receipt["error"] = str(error)
        raise
    finally:
        (evidence / "verification.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
