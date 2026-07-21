# Branch provenance: the 11 unrelated-history `claude/*` snapshots

This repository carries 11 long-lived side branches that are **not** ordinary
feature branches. Each is a **single root commit with no common ancestor with
`main`** — an independent, full-tree snapshot captured from an *older* lineage
of the project. They are kept (not deleted) as historical work records; this
file wires them into the project's documented history so their intent and
disposition are traceable without anyone having to re-derive it.

## Why they cannot be merged into `main`

Two independent reasons, either of which is decisive:

1. **Unrelated histories.** `git merge-base origin/main origin/<branch>` is
   empty for all 11. The repo's own CI gate **"Deny unrelated histories /
   check-common-ancestor"** blocks any PR from such a branch by design — this
   is a deliberate policy, not a tooling limitation.
2. **Superseded content.** Each branch is from an *older* lineage, so `main`
   is strictly ahead of it. Every branch's intended change is either already
   present in `main` (often in a more complete form) or, for the rebrand
   branches, is a *reversal* of work `main` has already completed (they would
   re-introduce `nousresearch.com` / `discord.gg/NousResearch` /
   `NousResearch/hercules-agent` references that `main` deliberately removed).

The work each branch represents therefore already lives in `main`. The table
below records where.

## Disposition (audited 2026-07)

Each branch was audited against `main` by reading its stated intent and
locating the equivalent (or superseding) code in `main`.

| Branch | Stated intent | Status in `main` |
|--------|---------------|------------------|
| `ci-fix` | Make fresh-final aging tests uptime-independent | **Already present.** `tests/gateway/test_stream_consumer_fresh_final.py` uses `time.monotonic() - 3600.0` on every relevant line — byte-identical to the branch. |
| `claude/desktop-ubuntu-build` | Add a Desktop Release workflow for Ubuntu installers | **Already present, improved.** `.github/workflows/desktop-release.yml` exists and pins `ubuntu-22.04` (oldest-supported LTS, glibc-forward-compatible) instead of the branch's `ubuntu-latest`. |
| `claude/docs-denous-cleanup` | Extend de-Nous doc cleanup | **Superseded / regressive.** `main` is further along; the branch would re-add `NousResearch/hercules-agent`, `discord.gg/NousResearch`, and `nousresearch.com` references and delete `main`'s "Maximizing context on llama.cpp" section. |
| `claude/fix-async-client-loop-flake` | Bind the async client to the loop object, not `id()` | **Already present, superset.** `agent/auxiliary_client.py` binds via `bound_loop` with closed-loop detection (a strict superset of the branch's fix). |
| `claude/fix-desktop-deb-homepage` | Set `homepage` so the `.deb`/`.rpm` build succeeds | **Already present.** `apps/desktop/package.json` sets `homepage` to the fork URL; the branch would additionally re-introduce `com.nousresearch.hercules` branding. |
| `claude/fix-slash-worker-flake` | Raise slash-worker `/tools` timeout 10s → 60s | **Already present, improved.** `tests/tui_gateway/test_slash_worker_mcp_discovery.py` uses a 60s deadline **and** a poll-until-deadline loop (more robust than the branch's single-shot read). |
| `claude/professional-polish` | Remove stale Nous Portal advertising | **Superseded.** `main` had already stripped ~40 more `nousresearch.com` advertising URLs than the branch; adopting it would re-add them. |
| `claude/rebrand-doc-links` | Repoint GitHub links to the fork | **Superseded / regressive.** `main` already repointed links; the branch's version still carries `discord.gg/NousResearch`, `nousresearch.com`, and `cd hercules-agent`. |
| `claude/remove-nous-draft` | Remove the Nous Portal provider | **Already done.** The provider (modules, registry entry, CLI, plugins) is fully absent from `main`; residual references were finished in the de-Nous cleanup (PR #46) and dead-orphan removal (PR #47). |
| `claude/repo-analysis-improvements-rgo6r1` | Update the github_comment webhook test to an async subprocess mock | **Already present.** The test is byte-identical in `main`; the branch's other files would revert `main`'s newer conversation-loop / prompt-caching optimizations. |
| `claude/self-improvement-roi` | Curator skill-effectiveness feedback loop | **Already present, superset.** `main`'s curator has the KEEP-proven-skills mechanism **plus** symmetric negative pruning **plus** selection-surface annotation the branch never implemented. |

## What to do with them

- **Keep them.** They are retained as historical snapshots; nothing here
  deletes them.
- **Do not attempt to merge them.** The unrelated-history gate will block it,
  and the content would regress `main`.
- If a specific idea from one is ever wanted, re-implement it as a fresh commit
  on top of current `main` (never merge/cherry-pick the snapshot, which has no
  shared history) — but per the audit above, every stated intent is already
  realized in `main`.
