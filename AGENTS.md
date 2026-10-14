# CI repository instructions

Keep all builds and PostgreSQL runtime testing on standard GitHub-hosted Windows runners. Do not run heavy jobs on a contributor's PC, add paid capacity, or switch to larger runners.

Keep workflows manual and repository token permissions read-only. Publish only reviewed public inputs. Never add mailbox data, drafts, private tracking state, credentials or private repository history. Publishing a test input is not permission to email or submit it upstream.

Preserve source and patch authorship, exact input hashes, observed failures and architecture limits. Never equate a green baseline with contribution validation. Stop only owned test processes, using retained identity, and retain evidence after failures.
