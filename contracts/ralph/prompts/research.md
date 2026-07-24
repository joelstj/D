# Mode: RESEARCH / VERIFY FACTS (no contract code)

Turn unconfirmed external facts into confirmed, cited config. Precision over coverage.

1. Pick ONE unverified item — usually an address in `config/chains/*.json` with `"_verify": true` or a
   zero address, or a protocol availability claim in `docs/specs/`.
2. Confirm it against an **authoritative, citable source**: the protocol's official docs/deployment
   registry, the canonical GitHub deployments file, or the chain's block explorer for the *verified*
   contract. Cross-check at least two independent sources for anything that moves funds.
3. Update `config/chains/<chain>.json`: replace the placeholder, set `"_verify": false`, and add a
   `"_source"` URL next to the entry (or in the file's `_sources` map). If you cannot confirm it, leave
   it flagged and record *why* — do not guess, and never fill in a plausible-looking address.
4. Reflect confirmed availability in the relevant `docs/specs/` capability matrix.
5. Journal to `PROGRESS.md` with the sources you used. Commit `chore(config): verify <chain> <thing>`.
   Stop.

**Absolute rule:** an address you cannot verify stays flagged. A wrong address here can drain funds.
