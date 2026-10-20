"""Hosted current-master validation for the LibreSSL follow-up patch."""
import hashlib
import json
import os
import pathlib
import platform
import re
import subprocess
import sys
import time

if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
    raise SystemExit("Standard hosted runners only")

root = pathlib.Path.cwd()
src = root / "postgres"
ci = root / "ci"
out = root / "evidence"
out.mkdir(exist_ok=True)
arch = os.environ["TEST_HOST_ARCH"]
machine = platform.machine().lower()
if not ((arch == "arm64" and machine in ("arm64", "aarch64")) or (arch == "x64" and machine in ("amd64", "x86_64"))):
    raise SystemExit("Native host architecture mismatch")

base = "776df8a6d7c42282825d658ff631509f033e8f34"
assert subprocess.check_output(["git", "-C", str(src), "rev-parse", "HEAD"], text=True).strip() == base
manifest = json.loads((ci / "tracking/email-readiness-20261017/libressl/manifest.json").read_text(encoding="utf-8"))
inputs = {entry["path"]: entry for entry in manifest["inputs"]}
replay_patch = ci / "tracking/email-readiness-20261017/libressl/current-master-public-vpoc-replay.patch"
followup_patch = ci / "tracking/email-readiness-20261017/libressl/0001-libpq-define-SSL_LIBRARY-without-SSL.diff"

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

for path in (replay_patch, followup_patch):
    if sha256(path) != inputs[path.name]["sha256"]:
        raise SystemExit(f"Patch SHA256 mismatch: {path.name}")

def patch_id(path):
    result = subprocess.run(["git", "patch-id", "--stable"], input=path.read_bytes(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=src, timeout=30)
    if result.returncode != 0:
        raise SystemExit(f"Could not compute patch-id for {path.name}")
    return result.stdout.decode("ascii").split()[0]

for path in (replay_patch, followup_patch):
    if patch_id(path) != inputs[path.name]["stable_patch_id"]:
        raise SystemExit(f"Stable patch-id mismatch: {path.name}")

results = []

def run(args, name, cwd=None):
    start = time.monotonic()
    log_path = out / f"{name}.log"
    with log_path.open("wb") as log:
        process = subprocess.run(args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, timeout=2400)
    entry = {"name": name, "args": args, "exit_code": process.returncode, "elapsed_seconds": round(time.monotonic() - start, 2), "log": str(log_path)}
    results.append(entry)
    (out / "commands.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return process.returncode, log_path

def require_success(args, name, cwd=None):
    code, log = run(args, name, cwd)
    if code != 0:
        raise SystemExit(f"{name} failed; see {log}")
    return log

# Replay the public proposal on current master, then prove the attached fix is needed.
require_success(["git", "-C", str(src), "apply", "--check", str(replay_patch)], "check-public-replay-apply")
require_success(["git", "-C", str(src), "apply", str(replay_patch)], "apply-public-replay")
require_success(["git", "-C", str(src), "diff", "--check"], "public-replay-whitespace-check")

build = root / "build-no-ssl"
require_success(
    [sys.executable, "-m", "mesonbuild.mesonmain", "setup", str(build), "postgres", "-Dauto_features=disabled", "-Dssl=none", "-Dicu=disabled", "-Dtap_tests=disabled"],
    "configure-no-ssl",
)
baseline_code, baseline_log = run(["ninja", "-C", str(build), "-j", "4", "src/backend/postgres.exe"], "baseline-build")
baseline_text = baseline_log.read_text(errors="replace")
if baseline_code == 0 or "SSL_LIBRARY" not in baseline_text or not ("undeclared" in baseline_text or "C2065" in baseline_text):
    raise SystemExit("Expected the rebased public proposal to fail only on undefined SSL_LIBRARY with SSL disabled")

require_success(["git", "-C", str(src), "apply", "--check", str(followup_patch)], "check-exact-followup-apply")
require_success(["git", "-C", str(src), "apply", str(followup_patch)], "apply-exact-followup")
require_success(["git", "-C", str(src), "diff", "--check"], "series-whitespace-check")
require_success(["ninja", "-C", str(build), "-j", "4", "src/backend/postgres.exe"], "candidate-build")

header = (src / "src/include/libpq/libpq.h").read_text(encoding="utf-8")
match = re.search(r'#if defined\(USE_OPENSSL\)\n#define SSL_LIBRARY "OpenSSL"\n#elif defined\(USE_LIBRESSL\)\n#define SSL_LIBRARY "LibreSSL"\n#else\n#define SSL_LIBRARY ""\n#endif', header)
if not match or header.count(match.group(0)) != 1:
    raise SystemExit("Expected provider-selection block not found exactly once in patched header")
macro_block = match.group(0)
controls = []
for name, defines, expected in (
    ("none", [], ""),
    ("openssl", ["USE_SSL", "USE_OPENSSL"], "OpenSSL"),
    ("libressl", ["USE_SSL", "USE_LIBRESSL"], "LibreSSL"),
):
    source = out / f"macro-{name}.c"
    source.write_text("#include <stdio.h>\n" + macro_block + "\nint main(void){puts(SSL_LIBRARY);return 0;}\n", encoding="utf-8")
    exe = out / f"macro-{name}.exe"
    command = ["cl", "/nologo", str(source), "/Fe:" + str(exe)] + ["/D" + item for item in defines]
    require_success(command, f"macro-{name}-compile")
    value = subprocess.check_output([str(exe)], text=True).strip()
    if value != expected:
        raise SystemExit(f"{name} macro control returned {value!r}, expected {expected!r}")
    controls.append({"provider": name, "value": value, "scope": "Actual patched header macro block; no provider runtime"})

binary = root / "build-no-ssl/src/backend/postgres.exe"
raw = binary.read_bytes()
pe = int.from_bytes(raw[0x3C:0x40], "little")
pe_machine = int.from_bytes(raw[pe + 4:pe + 6], "little")
expected_machine = {"x64": 0x8664, "arm64": 0xAA64}[arch]
if pe_machine != expected_machine:
    raise SystemExit(f"Unexpected server architecture: {pe_machine:#x}")

record = {
    "upstream_base": base,
    "public_replay_sha256": sha256(replay_patch),
    "public_replay_patch_id": patch_id(replay_patch),
    "followup_diff_sha256": sha256(followup_patch),
    "followup_patch_id": patch_id(followup_patch),
    "host_arch": arch,
    "baseline_error": "SSL_LIBRARY undefined with SSL disabled on the current-master replay of the public proposal",
    "candidate_build": "passed",
    "target_pe_machine": hex(pe_machine),
    "binary_sha256": hashlib.sha256(raw).hexdigest(),
    "macro_controls": controls,
    "scope": "Current PostgreSQL master plus a local replay of public CF7008 proposal and the exact follow-up code diff; no SSL provider runtime",
}
(out / "completion.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(json.dumps(record))
