# Independent CF7300 v4 Windows checks

This diagnostic workflow starts from PostgreSQL master
7787aec8ab000932a8704a988b175b39ff816f2f and Aidar Imamov's exact public v4
attachment (SHA256 80d08b863671adea7b28246ddfeee52f6889f366e102b6d8f8cd18ce4e41d598).
It does not use the competing introspection exporter from the experimental LLVM stack.

On standard native Windows x64 and ARM64 runners it checks:

- Actual v4 export code extracted from the patched source, with Meson 0.57.2 and 1.11.2.
- Disabled dependencies, pkg-config selection overrides, and dependency prefixes with and without spaces.
- Controlled CMake package-config metadata, including paths with spaces. This does not test every CMake Find module.
- The exact `VAL_CPPFLAGS` macro construction with a real compiler and an executable string round trip.
- A native header compile using the exported include flags. Controlled metadata is checked against Meson's resolved dependency arguments before interpreting results.
- Actual PostgreSQL `psql` builds with an external canary libpq header: author v4 alone, then a focused include-order/fallback-probe follow-up. The latter follows Andres Freund's prototype and Thomas Munro's discussion.

The focused follow-up retains Aidar's export implementation. It excludes the competing exporter, ICU discovery changes, and LLVM extension-interface changes.

The disabled-dependency macro compilation, execution and string round trip must pass as a positive compiler control. Fallback header-probe hunks are source-reviewed only: the PostgreSQL canary builds do not exercise the LDAP, OpenSSL, UUID or libintl fallback-discovery branches.

Inspect individual result JSONs and compiler logs. Workflow success means the diagnostic controls ran and the expected include-order comparison held; it does not mean every author-v4 export case passed. The exact author macro and exported-header compile failures, if any, are preserved as findings. No full PostgreSQL server, SSL/ICU runtime, installed PGXS extension, or LLVM/JIT result is claimed by this workflow.

Builds are hosted only. No database is started. This public package contains source patches and test code only, with no email, mailbox evidence or private tracker state.
