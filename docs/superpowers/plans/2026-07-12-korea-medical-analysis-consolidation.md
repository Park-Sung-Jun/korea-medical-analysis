# Korea Medical Analysis Consolidation Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with deterministic PowerShell checks. Destructive and production mutations remain explicit approval gates.

**Goal:** Consolidate both Z-drive medical project copies into one C-drive repository named `korea-medical-analysis`, keep `kr_pop_atlas` independent, and synchronize the workspace registry and `links.tms-ai-lab.com` launcher.

**Architecture:** `C:\Users\user\Downloads\claude\isochrone_map` is the code/data base because it has the newer Git history and broader data. `Z:\ai_common_share\korea-medical-redesign` contributes only production-proven presentation changes that do not duplicate data or violate the WaveNet UI contract. `services.json` remains the service identity source of truth; the local admin and public launcher consume its generated output.

**Tech Stack:** Static HTML/CSS/JavaScript, Python data/build scripts, Git/GitHub, Vercel, PowerShell, Playwright.

## Global Constraints

- Canonical medical identity: `korea-medical-analysis` / `korea_medical_analysis` / `대한민국 의료 접근성 분석`.
- Keep `C:\Users\user\Downloads\claude\kr_pop_atlas` as a separate repository and service.
- Keep `https://links.tms-ai-lab.com` unchanged; do not create or redirect singular `link` or `list` hosts.
- Preserve the pre-existing uncommitted changes in `index.html`, `map.html`, and `synthetic/styles.css`.
- Do not import Z-drive `_qa`, `.vercel`, duplicate `redesign.html`, duplicate data, caches, or temporary files.
- Do not commit, push, deploy, rename external projects, or recursively delete without the applicable explicit approval.

---

### Task 1: Freeze the merge basis

**Files:**
- Inspect: `C:\Users\user\Downloads\claude\isochrone_map\README.md`
- Inspect: `Z:\ai_common_share\korea-medical-access`
- Inspect: `Z:\ai_common_share\korea-medical-redesign`

- [ ] Record Git heads, dirty files, deployment linkage, overlapping-file hashes, and excluded duplicate/temp paths.
- [ ] Confirm C data is at least as complete as both Z copies and identify any unique production UI changes.
- [ ] Confirm the existing user changes remain present after branch creation.

Verification: `git status --short` must still show the three pre-existing modified files before consolidation edits are added.

### Task 2: Consolidate the medical repository and identity

**Files:**
- Modify: `index.html`
- Modify: `map.html`
- Modify: `synthetic/index.html`
- Modify: `.env.example`
- Modify: `LICENSE`
- Modify: `ruff.toml`
- Modify: `README.md`
- Modify: `scripts/build_dist.py`
- Modify: `scripts/deploy_vercel.py`
- Modify only comments/labels where needed: `scripts/download_kosis_data.py`, `scripts/fetch_hira.py`, `scripts/gen_stats.py`, `synthetic/styles.css`

- [ ] Merge the production page's semantic content without importing its dark/gradient shell; retain WaveNet-compatible white surfaces, 56px topbar, shared typography, radius, and teal action tokens.
- [ ] Replace canonical repository, project, URL, and display-name references with the approved identity.
- [ ] Use relative `map.html` embedding so previews and renamed production URLs do not depend on an old absolute host.
- [ ] Keep the legacy URL only where explicitly documented as compatibility history, not as canonical metadata.
- [ ] Rename the C-drive folder to `C:\Users\user\Downloads\claude\korea-medical-analysis` after file-level verification.

Verification: a scoped old-name search may match only migration history/compatibility notes; HTML fetch targets must resolve from the built `dist` tree.

### Task 3: Synchronize registry and launcher

**Files:**
- Modify: `C:\Users\user\Downloads\claude\shared_data\registry\services.json`
- Generate/verify: `C:\Users\user\Downloads\claude\tms_links\index.html`

- [ ] Change `korea_medical` to `korea_medical_analysis`, update title, description, local folder, canonical production URL, repository URL, and stale source note.
- [ ] Update `hc_mkdata` parent path from `../isochrone_map` to `../korea-medical-analysis` while keeping it a separate service.
- [ ] Run `tms_links\build.py` and verify the generated launcher contains the same identity and plural `links.tms-ai-lab.com` remains canonical.
- [ ] Verify `http://127.0.0.1:8000/admin#services` reads the updated registry and renders both medical services correctly.

Verification: parse `services.json`, run the launcher build, and assert there are no duplicate service IDs or stale local paths.

### Task 4: Cleanup and deployment gates

**Files:**
- Remove only after approval: generated caches and confirmed duplicate/temp files.
- Build: `dist\` (generated and excluded from Git).

- [ ] Remove `__pycache__` and confirmed QA/temp artifacts; retain unique source, data, and ignored local credentials unless separately approved.
- [ ] Run syntax/lint/tests available in the repository, build `dist`, inspect payload exclusions, and scan for secret names/values without printing values.
- [ ] Verify administrative-boundary source/basis date and audit result-to-boundary joins for the production layer.
- [ ] Run Playwright desktop/mobile smoke checks for the landing report and embedded map.
- [ ] Recheck GitHub/Vercel target-name availability immediately before any external rename.

Verification: all applicable quality, test/build, dependency, secret, and deployment-payload gates must pass before production mutation.

### Task 5: External rename and Z-drive retirement

**Files/Resources:**
- Rename after approval: GitHub repository and Vercel project to `korea-medical-analysis`.
- Delete after approval: `Z:\ai_common_share\korea-medical-access`
- Delete after approval: `Z:\ai_common_share\korea-medical-redesign`

- [ ] Present the exact external mutations and the final Z deletion manifest for immediate approval.
- [ ] Rename GitHub/Vercel in place, update the local Git remote and Vercel link, deploy through the canonical gated procedure, and verify the approved production URL.
- [ ] Rebuild and deploy the `links.tms-ai-lab.com` launcher directly from the synchronized registry.
- [ ] Delete only the two approved Z folders and confirm both paths no longer exist while both C repositories remain healthy.

Verification: production HTTP/browser smoke checks pass; Git remote, Vercel linkage, registry, local admin, and public launcher all report the same identity; both exact Z paths return `False` from `Test-Path`.
