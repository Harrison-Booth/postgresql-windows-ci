#!/usr/bin/env bash
set -euo pipefail
if [[ "${GITHUB_ACTIONS:-}" != true || "${RUNNER_ENVIRONMENT:-}" != github-hosted ]]; then
  echo 'Hosted runners only; local heavy execution is prohibited' >&2
  exit 1
fi
test -d evidence
exec > >(tee evidence/pipeline.log) 2>&1
export PATH="$(pwd)/llvm-build/bin:/ucrt64/bin:/c/Program Files/Git/cmd:/c/Strawberry/perl/bin:$(cygpath -u "$RUNNER_TEMP")/winflex:$PATH"
export CC=/ucrt64/bin/gcc.exe CXX=/ucrt64/bin/g++.exe
export PKG_CONFIG_LIBDIR=/ucrt64/lib/pkgconfig
export LC_ALL=C LANG=C
unset PG_TEST_NOCLEAN PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD PGOPTIONS || true
pacman -Q > evidence/dependency-versions.txt
gcc --version > evidence/gcc-version.txt
python --version
for tool in git gcc g++ clang clang++ ld win_bison win_flex meson cmake ninja python pkg-config; do
  command -v "$tool"
done
win_bison --version > evidence/bison-version.txt
win_flex --version > evidence/flex-version.txt
meson --version > evidence/meson-version.txt
cmake --version > evidence/cmake-version.txt
test "$(git -C llvm-source rev-parse HEAD)" = 36eaf6328d1ab9fb19d806269444a0a979e83397
test "$(git -C postgres rev-parse HEAD)" = 6b3f8857aa83b214f9e7d2465d4e21d6ef75f39e
python tracker/tools/verify-hosted-llvm-inputs.py
# Bootstrap LLVM with UCRT MinGW Clang, preserving GCC/G++ for PostgreSQL.
# Do not set a global visibility preset: upstream applies its component policy.
test ! -e llvm-build
python tracker/tools/verify-hosted-llvm-visibility.py probe \
  --toolchain-root "$(cygpath -m /ucrt64)" --evidence evidence
llvm_roots='ExecutionEngine;MCJIT;OrcJIT;Passes;DebugInfoDWARF;X86CodeGen;X86AsmParser;X86Desc;X86Info;X86Disassembler'
llvm_cmake_args=(
  -S llvm-source/llvm -B llvm-build -G Ninja
  -DCMAKE_C_COMPILER=/ucrt64/bin/clang.exe -DCMAKE_CXX_COMPILER=/ucrt64/bin/clang++.exe
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
  -DCMAKE_BUILD_TYPE=Release -DLLVM_ENABLE_PROJECTS=clang
  -DLLVM_TARGETS_TO_BUILD=X86 -DLLVM_BUILD_LLVM_DYLIB=ON
  -DLLVM_LINK_LLVM_DYLIB=OFF -DLLVM_ENABLE_ASSERTIONS=ON
  -DLLVM_BUILD_LLVM_DYLIB_VIS=ON
  -DLLVM_INCLUDE_TESTS=OFF -DLLVM_INCLUDE_BENCHMARKS=OFF
  -DLLVM_INCLUDE_EXAMPLES=OFF -DLLVM_ENABLE_BINDINGS=OFF
  -DLLVM_ENABLE_ZLIB=OFF -DLLVM_ENABLE_ZSTD=OFF
  -DLLVM_ENABLE_LIBXML2=OFF -DLLVM_ENABLE_LIBEDIT=OFF
  -DLLVM_ENABLE_TERMINFO=OFF -DLLVM_ENABLE_FFI=OFF
  -DLLVM_PARALLEL_LINK_JOBS=1
)
printf '%s\n' "${llvm_cmake_args[@]}" "-DLLVM_DYLIB_COMPONENTS=$llvm_roots" > evidence/llvm-configure-initial-arguments.txt
cmake "${llvm_cmake_args[@]}" "-DLLVM_DYLIB_COMPONENTS=$llvm_roots"
# Include the configured dependency closure in whole-archive, retaining C/C++ APIs.
llvm_components=$(python tracker/tools/verify-hosted-llvm-dll.py closure --build llvm-build --evidence evidence)
test -n "$llvm_components"
printf '%s\n' "${llvm_cmake_args[@]}" "-DLLVM_DYLIB_COMPONENTS=$llvm_components" > evidence/llvm-configure-final-arguments.txt
cmake "${llvm_cmake_args[@]}" "-DLLVM_DYLIB_COMPONENTS=$llvm_components"
python tracker/tools/verify-hosted-llvm-visibility.py configuration \
  --toolchain-root "$(cygpath -m /ucrt64)" --evidence evidence --build llvm-build
# Prove the actual DLL/export boundary before paying for the Clang tool build.
ninja -C llvm-build -j 2 LLVM
python tracker/tools/verify-hosted-llvm-dll.py dll --build llvm-build --evidence evidence
ninja -C llvm-build -j 2 clang llvm-config llvm-as llvm-dis opt llc llvm-lto
clang --version > evidence/clang-version.txt
llvm-config --version > evidence/llvm-version.txt
cat > evidence/native.ini <<EOF
[binaries]
c = '$(cygpath -m /ucrt64/bin/gcc.exe)'
cpp = '$(cygpath -m /ucrt64/bin/g++.exe)'
ar = '$(cygpath -m /ucrt64/bin/ar.exe)'
strip = '$(cygpath -m /ucrt64/bin/strip.exe)'
windres = '$(cygpath -m /ucrt64/bin/windres.exe)'
llvm-config = '$(cygpath -m "$PWD/llvm-build/bin/llvm-config.exe")'
pkg-config = '$(cygpath -m /ucrt64/bin/pkg-config.exe)'
EOF
meson setup postgres/build postgres --native-file evidence/native.ini \
  --prefix="$(cygpath -m "$PWD/pg-install")" --buildtype=debug \
  -Dauto_features=disabled -Dllvm=enabled -Dssl=openssl -Dicu=enabled -Dcassert=true \
  -Dplperl=disabled -Dplpython=disabled -Dpltcl=disabled -Dtap_tests=disabled \
  -DPERL=C:/Strawberry/perl/bin/perl.exe -DTAR=C:/Windows/System32/tar.exe
python tracker/tools/verify-hosted-pg-assertions.py configuration
python tracker/tools/run-hosted-llvm-jumps.py
ninja -C postgres/build -j 2
meson install -C postgres/build --no-rebuild
python tracker/tools/verify-hosted-pg-assertions.py installed
python tracker/tools/verify-hosted-llvm-dll.py provider --build llvm-build --evidence evidence --install pg-install
python tracker/tools/run-hosted-jit-sql.py
meson test -C postgres/build --num-processes 1 --print-errorlogs --logbase setup tmp_install install_test_files initdb_cache
set +e
meson test -C postgres/build --num-processes 1 --print-errorlogs --logbase regression-default --suite regress
default_status=$?
set -e
python tracker/tools/snapshot-hosted-regression-logs.py --phase default --exit-status "$default_status"
export PGOPTIONS='-c jit=on -c jit_above_cost=0 -c jit_inline_above_cost=0 -c jit_optimize_above_cost=0'
set +e
meson test -C postgres/build --num-processes 1 --print-errorlogs --logbase regression-jit-forced --suite regress
forced_status=$?
set -e
python tracker/tools/snapshot-hosted-regression-logs.py --phase jit-forced --exit-status "$forced_status"
if (( default_status != 0 || forced_status != 0 )); then
  echo "Full regression failures retained: default=$default_status jit-forced=$forced_status" >&2
  exit 1
fi
