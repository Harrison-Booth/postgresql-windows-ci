# Independent CF7300 v4 Windows checks

This diagnostic workflow starts from PostgreSQL master
e75fcc9867fdfe295957ad5c9a98cbe2e1b4c5c3 and Aidar Imamov's exact public v4
attachment (SHA256 80d08b863671adea7b28246ddfeee52f6889f366e102b6d8f8cd18ce4e41d598).
It does not use the competing introspection exporter from the experimental LLVM stack.

On standard native Windows x64 and ARM64 runners it checks:

- Actual v4 export code extracted from the patched source, with Meson 0.57.2 and 1.11.2.
- Disabled dependencies, pkg-config selection overrides, and dependency prefixes with and without spaces.
- Controlled CMake package-config metadata, including paths with spaces. This does not test every CMake Find module.
- `extra_include_dirs` set to a Windows path containing spaces, checked both as a Meson compiler argument and in the serialized `pg_config`/PGXS flags.
- The exact `VAL_CPPFLAGS` macro construction with a real compiler and an executable string round trip.
- A native header compile using the exported include flags. Controlled metadata is checked against Meson's resolved dependency arguments before interpreting results.
- Focused Meson `find_library(has_headers:)` controls for LDAP, OpenSSL, UUID, and libintl header patterns. Each control lacks the external include argument; its paired probe receives `header_args`. These validate the Meson mechanism and do not configure PostgreSQL's optional dependencies end to end.
- Actual PostgreSQL `psql` builds with an external canary libpq header: author v4 alone, then a focused include-order follow-up. The latter follows Andres Freund's prototype and Thomas Munro's discussion.

The focused follow-up retains Aidar's export implementation. It excludes the competing exporter, ICU discovery changes, and LLVM extension-interface changes.

The workflow requires all seven include-export cases, the focused fallback-header controls, and both sides of the `psql` canary comparison to match their expected results. The focused fallback controls test Meson's header-check arguments, not the full PostgreSQL dependency-discovery branches.

Inspect individual result JSONs and compiler logs. Workflow success means all recorded controls passed for the exact ordered patch series. No full PostgreSQL server, SSL/ICU runtime, installed PGXS extension, actual optional-dependency configuration, or LLVM/JIT result is claimed by this workflow.

Builds are hosted only. No database is started. This public package contains source patches and test code only, with no email, mailbox evidence or private tracker state.
