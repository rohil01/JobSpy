# Plan: JobSniffer — modernize JobSpy, then extract job data

## Context

`recipe.md` sets the goal: fork [speedyapply/JobSpy](https://github.com/speedyapply/JobSpy) to
`github.com/cspenn`, modernize it against this project's Python standards, and use it to extract job
descriptions, titles, pay, and related detail. Work stays entirely in cspenn's repository — no pull
requests back upstream, deliberately, to avoid contaminating the project with AI-edited code.

Upstream is real but decaying. It has 3,957 stars and an MIT license, but the last push was
2026-02-18 and PyPI `python-jobspy` 1.1.82 dates to 2025-07-28. It has **zero tests**, uses Poetry
with no `src/` layout, puts every scraper's logic in `__init__.py`, depends on the unmaintained
`tls-client`, and — as `recipe.md` suspected — does not use `curl_cffi`. Several defects that look
current are really **stale-release artifacts**: PyPI 1.1.82 advertises `NUMPY==1.26.3` and
`markdownify<0.14.0`, but git already relaxed both (`7160d0f` / PR #337, and `^1.1.0`). Nothing has
been released since, which is why issue #349 still bites anyone installing from PyPI.
Open issues confirm live breakage: #302 (ZipRecruiter and Google return 403 / 0 results), #374
(LinkedIn descriptions empty), #370, #342, #349 (a `markdownify` pin blocking the CVE-2025-46656
fix).

The three HAR captures in `input/` are the ground truth that makes this tractable, and analyzing
them changed the plan substantially. Findings are in the next section.

## Ground truth from the HAR captures

All three parse (Indeed 491 entries, LinkedIn 859, ZipRecruiter 205). **Response bodies are
captured** (358 / 703 / 167 entries with content), but **request headers are sanitized** down to
`DNT`, `Upgrade-Insecure-Requests`, and `User-Agent` — no cookies or tokens survive. This asymmetry
drives the whole strategy: the HARs are authoritative for **response schemas and parsers**, and
useless for **reconstructing request authentication**. Parsers get built and tested against recorded
data; request paths must be confirmed live.

| Site | What upstream JobSpy does | What the HAR proves | Consequence |
|---|---|---|---|
| Indeed | `apis.indeed.com/graphql` with hardcoded key `161092c…` impersonating the iOS app | Not exercised at all. Capture is `www.indeed.com/jobs` (2.0 MB HTML) plus a 322 KB `/viewjob` JSON — both base64-encoded in the HAR. Search HTML carries 40 jobs with **truncated `snippet` only**; the full description lives in the `/viewjob` response | **Two-step flow** (see below); keep GraphQL as fallback pending live test of the shared key |
| LinkedIn | Anonymous `jobs-guest/.../seeMoreJobPostings/search` | Authenticated Voyager: 173 `voyager/api/graphql` calls, `voyagerJobsDashJobCards`, plus a `checkpoint/challengesV2` — LinkedIn challenged the session | Stay on guest API (no account risk); use Voyager payloads only to understand field semantics and validate output |
| ZipRecruiter | `api.ziprecruiter.com/jobs-app/jobs` (JSON) | Endpoint gone. Site now uses Connect-RPC: `/job_services.job_card.api_public.public.api.v1.API/GetJobDetails` returning `application/proto`, and `/api/web.job_search.proto.v1.API/AutocompleteLocation` | Rewrite required — this is the root cause of issue #302 |

Asking the same question of both sites — *does the search response actually carry description and
pay, or only enough to identify a job?* — produced the same answer, and it is the central structural
finding of this analysis. **Neither site yields the target fields from search alone. Both require a
per-job detail fetch.**

- **Indeed.** `window.mosaic.providerData` survives as the mount point, but the search page carries
  40 × `"snippet"` (truncated) and 40 × `salarySnippet` / 38 × `extractedSalary` (estimates), with
  exactly **one** `sanitizedJobDescription` on the entire page — the job open in the detail pane, not
  all 40. The real payload is `/viewjob?jk={jobkey}&from=vjs`, which returns
  `body.jobInfoWrapperModel.jobInfoModel.sanitizedJobDescription` (7,259 chars of HTML in the sample)
  plus `body.salaryInfoModel` with concrete `salaryMin: 60000`, `salaryMax: 65000`,
  `salaryCurrency: "USD"`, alongside `jobTitle`, `jobKey`, and `hiringInsightsModel.age`. The same
  description is mirrored at `body.mosaicData.serverContextData.request.data.metaData.
  js-match-insights-provider.jobDescription`, giving a built-in fallback selector.
- **ZipRecruiter.** `/jobs-search` (548 KB) embeds Next.js RSC flight data and one
  `application/ld+json` block, but that block is a bare `ItemList` — 20 entries of title, URL, and
  `jid` only. Detail requires the protobuf `GetJobDetails` call. The payload is decodable: plaintext
  is visible in the wire bytes (`<p><b>Job title:</b> Global Rezurock Marketing Lead…`).

So Indeed and ZipRecruiter share one shape — *search for identity, then fetch per job for content* —
and the design treats them uniformly. This is exactly the shape the SQLite freshness check is built
to exploit: the expensive step is the per-job detail fetch, and that is the step re-runs skip.

## Decisions locked with the user

| Decision | Choice |
|---|---|
| Scope | Indeed + LinkedIn + ZipRecruiter verified against HAR. The other five scrapers (Glassdoor, Google, Bayt, BDJobs, Naukri) get the infrastructure upgrade and are explicitly marked unverified |
| Fork mode | True GitHub fork (preserves visible MIT attribution), upstream PRs disabled, README notice |
| Naming | Repo renamed to `cspenn/jobsniffer`; package and import become `jobsniffer`. Both names confirmed free on GitHub and PyPI. **No PyPI publish yet** — revisit once scrapers are proven live |
| Indeed | Both paths, HTML/mosaic primary, GraphQL fallback |
| LinkedIn | Guest API; HAR used for schema only |
| Testing | HAR-derived offline fixtures for deterministic CI, plus a separately-marked opt-in live smoke suite |
| Deliverable | Two artifacts: the modernized fork, and a thin extractor in this repo's `src/` |
| Extractor UX | YAML-config-driven CLI, exporting CSV + JSONL |
| Storage | **SQLite is the system of record** (stdlib `sqlite3`, `ON CONFLICT` upserts, FTS5 over descriptions). DuckDB attaches to the same file read-only via `ATTACH … (TYPE SQLITE)` for analytics and Parquet export — no dual-write, no sync problem |
| Descriptions | Store **both** raw HTML and converted Markdown |
| Idempotence | **First-class requirement.** Re-running any search must converge to identical state |

## Architecture

Two artifacts, cleanly separated per `docs/orientation.md`.

**1. `cspenn/jobsniffer`** — the modernized library (separate repo/worktree)

```
src/jobsniffer/
  http/          protocol + CurlCffiClient (impersonation) + ReplayClient (tests)
  model.py       Pydantic models, PEP 695 / builtin generics
  sites/
    indeed/      search.py (mosaic HTML) · detail.py (/viewjob JSON) · graphql.py (fallback) · parse.py
    linkedin/    guest.py (search + jobs/view detail) · parse.py
    ziprecruiter/  search.py (ld+json) · detail.py (Connect-RPC) · proto/ · parse.py
    …five unverified scrapers, ported to the new client
```

The pivotal design element is the **`HttpClient` protocol**. One narrow interface — request in,
response out — with a `CurlCffiClient` for production and a `ReplayClient` for tests. This is what
makes 100% coverage achievable without mocking libraries fighting `curl_cffi`'s non-`requests`
internals, and it keeps each scraper testable in isolation.

`ReplayClient` needs a **record mode**, because fixtures come from two sources and only one of them
is the HAR:

| Source | Sites | Why |
|---|---|---|
| Derived from HAR | Indeed, ZipRecruiter | The captures contain the exact responses these scrapers parse |
| Recorded live, once, into `tests/fixtures/` | LinkedIn + the five unverified scrapers | No HAR evidence exists for these request paths |

This is a genuine gap the HARs cannot close. LinkedIn's capture is authenticated Voyager, while the
chosen path is the anonymous guest API (`seeMoreJobPostings/search`, then `jobs/view/{id}` for
description) — **none of those responses are in the capture**. So LinkedIn fixtures must be recorded
live once via record mode, then committed and replayed. Without this, the #374 empty-description fix
would have nothing to verify against. The same applies to Glassdoor, Google, Bayt, BDJobs, and Naukri.

Where a live recording cannot be obtained at all (e.g. a site unreachable from this location, or one
already returning 403 — Google is a known case per issue #302), that scraper's parser gets a
**documented `# pragma: no cover` exclusion with the reason stated in-file and listed in the README**,
rather than a fabricated fixture or a silently-lowered coverage gate. The 100% target is then 100%
of covered-by-policy code, with exclusions visible and reviewable.

**2. This repo's `src/`** — the extractor application

```
src/job_extractor/
  config.py    pydantic-settings over config.yml
  store.py     SQLite schema, idempotent upserts, FTS5
  extract.py   orchestration
  export.py    CSV / JSONL / Parquet via DuckDB
  cli.py       typer
src/scripts/har_to_fixtures.py   reusable HAR→fixture utility (P2: not a one-off script)
```

### Storage schema and how idempotence actually works

Identity is `(site, site_job_id)` — Indeed `jobkey`, LinkedIn URN id, ZipRecruiter `jid`. A
`content_hash` over the normalized field set detects genuine changes.

- `jobs` — current state, `UNIQUE(site, site_job_id)`, upserted via
  `INSERT … ON CONFLICT(site, site_job_id) DO UPDATE`, carrying `first_seen_at` / `last_seen_at` /
  `content_hash`, plus `description_html` and `description_md`
- `job_versions` — a new row **only when `content_hash` changes**, so history accrues without churn
- `raw_payloads` — the original HTML / JSON / protobuf bytes per fetch, so parsers can be revised
  without re-scraping (this is what makes the HAR-fixture approach sustainable long-term)
- `runs` / `run_jobs` — which run observed which job
- `jobs_fts` — FTS5 virtual table over `description_md` and `title`

Idempotence follows from three properties: upsert-by-identity means re-running a search rewrites
rather than duplicates; `job_versions` only grows on real content change; and a job whose
`content_hash` is unchanged within a freshness window **skips its detail fetch entirely**, which
also makes re-runs cheap and much lighter on the target sites. Schema creation is
`CREATE TABLE IF NOT EXISTS` plus a `schema_version` row — no migration framework, per P10.

## Phases

Ordering is driven by dependency, and the three site tracks in Phase 3 are genuinely independent —
that is where parallel agents pay off (P5).

**Phase 0 — Fork and scaffold.** `gh repo fork` → rename to `jobsniffer` → disable upstream PRs →
README attribution notice. Poetry → uv, flat → `src/` layout, package rename, Python 3.12 floor.
Copy this plan into `docs/` as the spec of record.

**Phase 1 — Dependency and HTTP modernization.** Replace `tls-client` **and** `requests` with
`curl_cffi` (`impersonate="chrome"`, matching the TLS fingerprint the HARs were captured under).
Pin `markdownify>=1.1.0` and `numpy>=1.26.0` in the new `pyproject.toml` so the fixes that exist only
in upstream git actually ship. Add `typer`, `pydantic-settings`, `PyYAML`, `structlog`, `stamina`.
Introduce the `HttpClient` protocol with `CurlCffiClient` + `ReplayClient` (including record mode)
and port **all eight** scrapers to it — the client swap is not optional for the five unverified ones,
since their current transport is being deleted.

**Phase 2 — Fixture infrastructure.** Build `src/scripts/har_to_fixtures.py`, which **must
base64-decode `response.content.text`** — all three HARs use `encoding: "base64"`, and missing this
silently yields empty parses. Generate Indeed and ZipRecruiter fixtures from HAR; record LinkedIn and
the unverified sites live via record mode. This gates Phase 3.

**Phase 3 — Site work, three parallel tracks.** All three are the same two-step shape:
search → identity, detail fetch → description and pay.
- *Indeed*: mosaic HTML search parser for jobkeys + snippet + salary estimate, then
  `/viewjob?jk={jobkey}&from=vjs` for `sanitizedJobDescription` and `salaryInfoModel`, with the
  `js-match-insights-provider.jobDescription` mirror as fallback selector. Separately live-test
  whether the shared GraphQL key still works and wire it as a whole-path fallback if so
- *LinkedIn*: guest API search + `jobs/view/{id}` detail; fix the empty-description bug (#374),
  validating field semantics against the Voyager payloads
- *ZipRecruiter*: full rewrite. `/jobs-search` `ld+json` for identity, then `GetJobDetails` for
  content. Use `blackboxprotobuf` to discover the wire structure, then commit a minimal hand-written
  `.proto` and parse with the stable `protobuf` runtime (P12 — no bespoke wire parser)

**Phase 4 — Types and standards.** Purge banned `Optional[...]` / `List[...]` (`model.py` is full of
them), PEP 695 generics, `structlog`, explicit exceptions per P8, `radon` grade ≤ B.

**Phase 5 — Extractor application.** Config, SQLite store, orchestration, exports, CLI.

**Phase 6 — QA.** `/qa-python`, then coverage and the live smoke suite.

## Verification

Evidence required before anything is called done:

1. `uv run pytest --cov` — 100% coverage of covered-by-policy code, all passing, no network (P11),
   with every `pragma: no cover` exclusion justified in-file
2. Fixture round-trip against **known values from the captures**, not just "parses without error" —
   e.g. the Indeed sample must yield `jobKey == "20d6afbf6595234e"`, title
   `"Website Developer & Digital Marketing"`, a 7,259-char HTML description, and
   `salaryMin/Max == 60000/65000 USD`; the ZipRecruiter sample must yield the
   `"Global Rezurock Marketing Lead"` description body
3. **Idempotence proof**: run the same config twice; assert `jobs` row count and every
   `content_hash` are identical, `job_versions` gained zero rows, and the second run issued
   materially fewer HTTP requests — specifically **zero detail fetches**, which is where the cost is
4. Live smoke (`-m live`, opt-in): each of the three sites returns non-empty jobs with populated
   description and pay
5. End-to-end: `jobsniffer-extract --config config.yml` → SQLite populated → CSV + JSONL in
   `output/` → DuckDB `ATTACH` reads the same file and aggregates
6. `/qa-python` clean; `graphify update .`

## Risks

- **Shared Indeed API key may already be revoked.** Mitigated by HTML-primary; discovered in Phase 3
  live testing, not at the end.
- **ZipRecruiter protobuf is unversioned and can shift.** `raw_payloads` retains the bytes, so a
  schema change is a re-parse rather than a re-scrape.
- **Fixtures go stale while tests stay green** — precisely JobSpy's current failure mode. The live
  smoke suite exists specifically to catch this.
- **Per-job detail fetches multiply request volume** on both Indeed and ZipRecruiter: N jobs means
  N+1 requests, which is exactly the pattern that draws rate limiting. Mitigated by the SQLite
  freshness check (re-runs skip unchanged jobs entirely), plus `stamina` backoff and configurable
  delays. Worth stating plainly: first runs will be slower than upstream JobSpy's, because upstream
  is returning snippets where this returns full descriptions.
- **Five scrapers stay unverified**, and LinkedIn's guest path is verified only against
  live-recorded fixtures rather than your HAR. Both facts get stated in the README rather than
  quietly presented as working.
- LinkedIn's guest API returns less than Voyager. Accepted deliberately to avoid account risk.
