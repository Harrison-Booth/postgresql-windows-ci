Experimental Windows x64 LLVM/JIT CI. Not submitted or approved upstream.

The first public run, 38675103913 at ed6bbce, failed linking llvmjit.dll and compiling llvmjit_types.bc. It did not establish PostgreSQL JIT runtime success.

This proposed revision preserves both source pins and the first ten PostgreSQL inputs. It adds two PostgreSQL reconstruction patches for provider-owned data declarations and platform include ordering, plus a separate LLVM patch for SmallVector template exports. These three new patches have static application evidence only. Their compilation and runtime behavior remain unverified.

The recipe checks the actual shared LLVM DLL and import library, then compiles and executes a debug SmallVector C++ probe before building the remaining LLVM tools. It checks the configured PostgreSQL bitcode and provider compilation plans before the full build. Metadata checks do not establish runtime success.

The manual workflow runs only in the public Harrison-Booth/postgresql-windows-ci repository on standard windows-2022, with read-only contents and a 180 minute limit. The private template skips the job. No paid capacity is enabled. The full recipe retains SSL, ICU, assertions, actual JIT and error recovery, three setup groups, and both default and forced-JIT core regressions. No ARM64 LLVM claim.

The public baseline workflow remains separate. No private tracker state, mailbox evidence or email drafts belong in this package. Publishing this revised package requires approval for its exact bytes.
