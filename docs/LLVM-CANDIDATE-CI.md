Experimental Windows x64 LLVM/JIT CI. Not submitted or approved upstream.

The first public run, 38675103913 at ed6bbce, failed linking llvmjit.dll and compiling llvmjit_types.bc. It did not establish PostgreSQL JIT runtime success.

Run 38930842351 at ca96f570 built the shared LLVM DLL and passed its 30,717-export check. The C++ probe then requested separate component DLLs through llvm-config and stopped before consumer compilation or PostgreSQL testing. This harness repair uses real llvm-config cxxflags, ldflags and libdir metadata, checks that libdir matches the verified shared import library, and links that import library directly. Component DLLs are not built by this configuration. Source pins and all thirteen source patches are unchanged.

The approved source stack preserves both source pins and the first ten PostgreSQL inputs. It includes two PostgreSQL reconstruction patches for provider-owned data declarations and platform include ordering, plus a separate LLVM patch for SmallVector template exports. The LLVM patch reached successful DLL compilation and export checks in run 38930842351. The two PostgreSQL corrections were not compiled in that run, and consumer/server runtime remains unverified.

The recipe checks the actual shared LLVM DLL and import library, then compiles and executes a debug SmallVector C++ probe before building the remaining LLVM tools. It checks the configured PostgreSQL bitcode and provider compilation plans before the full build. Metadata checks do not establish runtime success.

The manual workflow runs only in the public Harrison-Booth/postgresql-windows-ci repository on standard windows-2022, with read-only contents and a 180 minute limit. The private template skips the job. No paid capacity is enabled. The full recipe retains SSL, ICU, assertions, actual JIT and error recovery, three setup groups, and both default and forced-JIT core regressions. No ARM64 LLVM claim.

The public baseline workflow remains separate. No private tracker state, mailbox evidence or email drafts belong in this package. This probe repair continues verification of the approved source stack. A changed source patch or upstream submission needs its applicable approval.
