# PostgreSQL Windows CI

Independent CI for an unmodified, pinned PostgreSQL snapshot on standard GitHub-hosted Windows x64 and ARM64 runners.

Run **Actions > PostgreSQL Windows baseline > Run workflow**. Each architecture builds and installs PostgreSQL with MSVC, checks the produced executable architecture, creates a temporary UTF-8 cluster, verifies a SQL result and cluster identity, and stops its owned server. Only logs and a verification receipt are uploaded, with one-day retention.

The baseline disables optional SSL, ICU and LLVM features. It is a CI infrastructure check, not contribution validation or a full regression run. A successful run does not establish upstream acceptance.

Workflows are manual, use read-only repository permissions, run one architecture at a time, and permit only the two standard runner labels. No larger runners or personal access tokens are required.

This repository excludes email drafts, mailbox exports, private tracking records, private repository history and unapproved contribution revisions. Any future test inputs need explicit provenance and a publication review. PostgreSQL and downloaded tools retain their upstream licenses.

## CF7300 Meson v4 follow-up checks

The `meson-v4-followups` workflow is a manual check for the reviewed branch. It applies Aidar Imamov's public v4 prerequisite and the two listed follow-up patches in order, then checks Meson 0.57.2 and 1.11.2 on standard Windows x64 and ARM64 runners. It covers include-flag exports, focused optional-header probes and a `psql` header-selection canary. It does not build a full server or installed PGXS extension, and a passing run does not establish upstream acceptance.

## Experimental LLVM verification

The manual LLVM workflow reconstructs pinned PostgreSQL and LLVM sources from
the recorded 14 PostgreSQL patches and one LLVM patch. These are experimental
CI inputs, with no upstream submission approval implied. Public source bytes
are reviewed separately from private drafts and tracker evidence.

Run 38951128866 reached the PostgreSQL provider link and failed on three LLVM
vtables that were present in the actual shared DLL exports. Its four native
GCC/Clang jump combinations passed 1,000 iterations each. Server JIT and full
regressions were not reached.

The next candidate enables data auto-import only for the GNU Windows provider
dependency, following the existing readline pattern. An actual GCC O0 consumer
exercises SmallVector and IRBuilder against the reconstructed DLL. It requires
the same object to fail on the three vtables with auto-import disabled, then
link and run with auto-import enabled. The actual provider link plan must put
the enable option after the global disable option.

Those controls precede the unchanged full scope: shared-library identity,
four compiler jump combinations, assertion-enabled SSL/ICU server build and
install, provider imports, forced-JIT SQL, same-backend error recovery, three
setup groups, and separate full default and forced-JIT core regressions. The
candidate has no runtime result until its own hosted run completes.


Run 38964400928 passed the provider link and real IRBuilder import controls,
then stopped at backend bitcode indexing: the 793-input irlink command was
62,646 characters. Installation, JIT SQL and regressions were not reached.
The next experimental correction retains every bitcode dependency but uses
a configured input manifest and an LLVM response file. The build-plan gate
checks all 793 ordered inputs and bounded command length. Lightweight Python
and no-language Meson controls pass; real LLVM execution remains unverified
until the next hosted run completes. The full original test scope is retained.
