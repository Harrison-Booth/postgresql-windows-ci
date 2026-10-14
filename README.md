# PostgreSQL Windows CI

Independent CI for an unmodified, pinned PostgreSQL snapshot on standard GitHub-hosted Windows x64 and ARM64 runners.

Run **Actions > PostgreSQL Windows baseline > Run workflow**. Each architecture builds and installs PostgreSQL with MSVC, checks the produced executable architecture, creates a temporary UTF-8 cluster, verifies a SQL result and cluster identity, and stops its owned server. Only logs and a verification receipt are uploaded, with one-day retention.

The baseline disables optional SSL, ICU and LLVM features. It is a CI infrastructure check, not contribution validation or a full regression run. A successful run does not establish upstream acceptance.

Workflows are manual, use read-only repository permissions, run one architecture at a time, and permit only the two standard runner labels. No larger runners or personal access tokens are required.

This repository excludes email drafts, mailbox exports, private tracking records, private repository history and unapproved contribution revisions. Any future test inputs need explicit provenance and a publication review. PostgreSQL and downloaded tools retain their upstream licenses.
