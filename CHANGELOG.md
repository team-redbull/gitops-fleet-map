# Changelog

## 1.3.0
- Per-tenant branch picker: view any combination of branches, shareable as `#refs=a:dev,c:release`, `GET /api/view`.
- Chart conflicts are decided by repo + revision (not by version string alone).
- Only what exists: layers, charts and conflicts are shown from files that are present.
- `PREFETCH_REFS` and `MAX_CACHED_TREES` for branch views; trees are cached by commit SHA.

## 1.2.0
- "Not in day2": day1 MCEs and hosted clusters that no tenant repo has a folder for.
- Listed in `inventory.json` as `not_in_day2`, with the expected tenants and a reason; absent without day1.
- Shown as a tile, an Action items list, a matrix row tag and status filter, and a sidebar badge per site.
- An onboarded, stale or env-mismatch folder all count as "has a folder".

## 1.1.0
- No git: GitLab REST API only, configured with `SIGS_URL`, `DAY1_URL` and a token.
- Polls branch SHAs each interval; the archive is downloaded only when a SHA changes.
- Honors GitLab 429 `Retry-After`; keeps the last good output on errors.
- Archives are unpacked safely (no absolute paths, `..`, escaping links, devices).

## 1.0.0
- Initial page: cluster x tenant matrix (onboarded, stale, available, missing).
- Self-contained `index.html`, `INVENTORY.md`, `inventory.json`.
- Container image served by `serve`, `/healthz`.
