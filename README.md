# Financial Analyst Job Crawler (US only)

Checks a list of companies every 12 hours for open **Financial Analyst** roles
in the **United States**, and keeps a running file of everything it finds.

## What changed from the version you found on GitHub

Modeled on the multi-ATS pattern from
[Babak-hasani/company-career-scraper](https://github.com/Babak-hasani/company-career-scraper)
(same 4 platforms: Greenhouse, Lever, Ashby, SmartRecruiters), but:
- **Company list is a local CSV**, not Google Sheets, so there's no Google
  Cloud service account to set up. Add rows straight in a text editor or Excel.
- Narrowed to your two specific filters: title contains "financial analyst",
  location is US.
- Adds `first_seen_date` per job, so you always know when something was new.

## Files

| File | Purpose |
|---|---|
| `companies.csv` | **Edit this.** Add/remove companies, any extra columns are fine and ignored by the script. |
| `crawler.py` | The script. Reads companies.csv, hits each platform's API, writes jobs_found.csv. |
| `jobs_found.csv` | **Your results.** Every currently-open matching role, rewritten fresh each run. |
| `seen_jobs.json` | Internal state so `first_seen_date` survives between runs. Don't need to touch it. |
| `.github/workflows/crawl.yml` | Runs the script every 12 hours for free. |

## companies.csv columns

| Column | Required | Notes |
|---|---|---|
| `company_name` | yes | Shown in results |
| `ats_type` | yes | `greenhouse`, `lever`, `ashby`, `smartrecruiters`, or `generic` |
| `token` | for the 4 API types | the company's board token (see below) |
| `url` | for `generic` only | the careers page to scan |
| `active` | no | `NO` to pause a company without deleting the row |
| `notes` | no | free text, whatever you want |

Add any other columns you like (category, priority, referral contact,
whatever) - the script ignores columns it doesn't recognize, it won't error.

### Finding a token

Open the company's job listing and look at the URL:

| URL pattern | ats_type | token |
|---|---|---|
| `boards.greenhouse.io/{token}/jobs/...` or `job-boards.greenhouse.io/{token}/...` | `greenhouse` | the part after the domain |
| `jobs.lever.co/{token}/...` | `lever` | the part after `lever.co/` |
| `jobs.ashbyhq.com/{token}/...` | `ashby` | the part after `ashbyhq.com/` (case-sensitive) |
| `jobs.smartrecruiters.com/{token}/...` | `smartrecruiters` | the part after `smartrecruiters.com/` |

If none of those URL patterns show up when you click a job (it opens as a
popup on the company's own domain, or the URL looks nothing like the above),
the company probably doesn't have a public ATS. Use `ats_type=generic` with
their careers page URL - it's best-effort and may return 0 results if the
page loads jobs via JavaScript rather than plain HTML.

## Companies included by default

| Company | ats_type | token | Verified |
|---|---|---|---|
| Ramp | ashby | ramp | Yes, live board |
| Stripe | greenhouse | stripe | Yes, live board (their site is a custom skin on top of it) |
| Databricks | greenhouse | databricks | Yes, live board |
| Plaid | lever | plaid | Yes, live board |
| OpenAI | generic | - | No public ATS found; best-effort HTML scan of openai.com/careers |

## Changing the filters

Both are constants near the top of `crawler.py`:

- `ROLE_KEYWORDS` - currently just `["financial analyst"]`. Add more phrases
  (e.g. `"fp&a"`, `"strategic finance"`) to widen what counts as a match.
- `LOCATION_KEYWORDS` / `US_STATE_NAMES` / `US_STATE_ABBREVS` - matches
  "united states" / "usa" / "united states of america" plus every US state
  name and abbreviation and common "remote - US" phrasing. This is broader
  than the 3 literal phrases because most postings list a city/state rather
  than spelling out the country - a strict match on just those 3 phrases
  would return very few results in practice. Trim the lists if you want it
  stricter.

**Known gap:** a listing with just a bare city and no state or country (e.g.
a location field that only says "Seattle") won't match, since enumerating
every US city risks matching unrelated words. If you notice real roles
slipping through this gap, tell me the company and I'll adjust.

## jobs_found.csv columns

| Column | Notes |
|---|---|
| `company`, `title`, `location` | |
| `department` | when the platform provides it |
| `employment_type` | e.g. Full-time, Hybrid - varies by platform |
| `compensation` | mainly populated for Ashby postings that publish a range |
| `source` | which ATS it came from |
| `posted_date` | the platform's own date field, when available. Blank for Ashby - their public API doesn't expose one. |
| `first_seen_date` | always populated - the date this crawler first saw the posting, regardless of what the platform reports |
| `url` | direct link to apply |
| `description_snippet` | first ~300 characters, plain text, for a quick read without opening the link |

## Filtering to recent jobs only (and what it does to email)

Run with `--days N` and two things change:

1. You also get `jobs_found_recent.csv`, containing only jobs within the
   last N days (0 = today, 7 = this week, etc). `jobs_found.csv` always has
   everything regardless, so nothing gets lost.
2. **The email switches from "only what's new" to "everything in the
   window," every run.** Without `--days`, the crawler only emails jobs it
   hasn't seen before - run it twice in a row and the second run correctly
   sends nothing, since nothing changed. With `--days 7` set, it emails the
   full last-7-days list every single time it runs, whether or not any of
   those postings are new to it. That means you'll get repeat emails about
   the same still-open posting across multiple runs within the week - that's
   the tradeoff for guaranteed visibility instead of silent no-op runs.

The "date" used for the window is the platform's own posted date when
available (Greenhouse, Lever, SmartRecruiters all provide one), falling
back to `first_seen_date` when it isn't (Ashby, i.e. Ramp, never exposes a
real posted date) - so a Ramp posting's "recency" reflects when this
crawler first noticed it, not necessarily when Ramp actually posted it.

**To make the weekly digest run on the schedule**, edit the "Run crawler"
step in `.github/workflows/crawl.yml`:
```
run: python crawler.py --days 7
```
Every scheduled run (every 12 hours, per the cron above) will then email you
the current 7-day window. If you'd rather only hear about new postings and
accept the occasional silent run, leave the line as plain `python crawler.py`.

## Adding a batch of new companies (candidate_companies.csv + verify_companies.py)

`candidate_companies.csv` has ~105 more companies, not yet in your real
`companies.csv`. **Read this before trusting any of it:**

- 6 rows are **high confidence** - I directly found a live job posting URL
  for these, same rigor as your original 5 companies.
- ~100 rows are **medium confidence** - pulled from a community-maintained
  GitHub list mapping companies to ATS endpoints
  ([jshchnz/claude-jobs](https://github.com/jshchnz/claude-jobs)). I did not
  personally re-verify each one the way I did the high-confidence rows.
  Company ATS choices also change over time, so even a correct-when-written
  mapping can go stale.
- 2 rows are **low confidence** (Evenflo, Transform9) - I couldn't confirm
  a real careers URL for either, so they're set `active=NO` by default.

**Run the verifier before trusting any of this:**
```
pip install requests
python verify_companies.py
```
This actually calls every endpoint in `candidate_companies.csv` for real
(something I can't do from wherever I built this list - no live network
there) and writes `verified_companies.csv` containing only the rows that
returned a genuine, non-empty result, already in the exact column format
`companies.csv` expects. It also prints which ones failed and why.

After running it, open `verified_companies.csv`, review it, and copy the
rows you want into your real `companies.csv`. Don't skip this step and
paste `candidate_companies.csv` straight in - the medium-confidence rows
are a starting point, not a guarantee.

## Setup (about 15 minutes, one time)

### 1. Create a GitHub account
Skip if you have one.

### 2. Create a new repository
"+" top right -> New repository -> name it e.g. `job-crawler` -> keep it
**Private** -> Create.

### 3. Upload these files
"Add file" -> "Upload files": `crawler.py`, `companies.csv`, and the whole
`.github` folder (drag it in, GitHub keeps the folder structure).

### 4. Get a Gmail App Password
- https://myaccount.google.com/apppasswords (needs 2-Step Verification on)
- Create one named "job-crawler", copy the 16-character password

### 5. Add repo secrets
Settings -> Secrets and variables -> Actions -> New repository secret:

| Name | Value |
|---|---|
| `GMAIL_USER` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-character app password |
| `ALERT_TO` | where you want alerts sent (can be same as GMAIL_USER) |

### 6. Turn it on
Actions tab -> "Job Crawler" -> "Run workflow" to test it manually. If it
runs clean, it now runs itself every 12 hours for free, forever.

Pull the repo (or just look at `jobs_found.csv` on GitHub) anytime to see
current matches without waiting for an email.
