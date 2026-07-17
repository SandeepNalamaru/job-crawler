#!/usr/bin/env python3
"""
Financial Analyst job crawler, US roles only.

Queries Greenhouse, Lever, Ashby, and SmartRecruiters public JSON APIs directly
(pattern modeled on github.com/Babak-hasani/company-career-scraper), plus a
best-effort HTML fallback for companies with no public ATS.

Input:  companies.csv   -> add/edit companies here; extra columns are ignored, not errors
Output: jobs_found.csv  -> every currently-open matching role, rewritten fresh each run
State:  seen_jobs.json  -> tracks first-seen date per job so that date survives reruns

Run:  python crawler.py
"""

import csv
import json
import os
import re
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import requests

HERE = Path(__file__).parent
COMPANIES_FILE = HERE / "companies.csv"
JOBS_FILE = HERE / "jobs_found.csv"
STATE_FILE = HERE / "seen_jobs.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (personal job-alert crawler)"}

# ---------------------------------------------------------------------------
# FILTERS - edit these two to change what counts as a match.
# ---------------------------------------------------------------------------

# Title match: case-insensitive substring. Narrowed on purpose to just this
# phrase - titles like "Strategic Finance Associate" won't match unless they
# also contain the words "financial analyst" somewhere. Add more phrases to
# widen it back.
ROLE_KEYWORDS = ["financial analyst"]

# Location match: checks for "united states" / "usa" / "united states of
# america" as asked, PLUS every US state name and common "remote - US"
# phrasing, because most real postings list a city/state rather than
# spelling out the country - a strict match on only the 3 literal phrases
# would miss most US listings. Trim this list if you want it stricter.
US_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
]
US_STATE_ABBREVS = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN",
    "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV",
    "NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN",
    "TX","UT","VT","VA","WA","WV","WI","WY","DC",
}
LOCATION_KEYWORDS = [
    "united states", "usa", "united states of america",
    "remote - us", "remote (us)", "remote, us", "remote - usa",
    "remote (usa)", "us remote", "u.s.", "remote - united states",
] + US_STATE_NAMES

# Known limitation: a bare city name with no state or country marker (e.g.
# a location field that just says "Seattle") won't match anything above and
# will be excluded. Enumerating every US city risks false-positiving on
# unrelated words, so this is a deliberate tradeoff, not an oversight.


def matches_role(title):
    t = (title or "").lower()
    return any(k in t for k in ROLE_KEYWORDS)


def matches_us_location(location):
    if not location:
        return False
    low = location.lower()
    if any(k in low for k in LOCATION_KEYWORDS):
        return True
    m = re.search(r",\s*([A-Za-z]{2})\b", location)
    if m and m.group(1).upper() in US_STATE_ABBREVS:
        return True
    return False


def clean_html(raw, limit=300):
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def epoch_ms_to_date(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Per-platform fetchers. Each returns a list of dicts in a common shape so
# the rest of the script doesn't care which platform a job came from. All
# field access uses .get() with fallbacks - a missing field produces a blank
# column in the output rather than a crash, since exact field availability
# varies by company and platform.
# ---------------------------------------------------------------------------

def fetch_greenhouse(token):
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [greenhouse:{token}] fetch failed: {e}", file=sys.stderr)
        return []
    out = []
    for j in data.get("jobs", []):
        depts = j.get("departments") or []
        out.append({
            "job_id": f"gh-{j.get('id')}",
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "department": depts[0].get("name", "") if depts else "",
            "employment_type": "",
            "compensation": "",
            "posted_date": (j.get("updated_at") or "")[:10],
            "url": j.get("absolute_url", ""),
            "description_snippet": clean_html(j.get("content", "")),
            "source": "greenhouse",
        })
    return out


def fetch_lever(token):
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [lever:{token}] fetch failed: {e}", file=sys.stderr)
        return []
    out = []
    for j in data:
        cats = j.get("categories") or {}
        out.append({
            "job_id": f"lv-{j.get('id')}",
            "title": j.get("text", ""),
            "location": cats.get("location", ""),
            "department": cats.get("team", ""),
            "employment_type": cats.get("commitment", ""),
            "compensation": "",
            "posted_date": epoch_ms_to_date(j.get("createdAt")),
            "url": j.get("hostedUrl", ""),
            "description_snippet": clean_html(j.get("descriptionPlain") or j.get("description", "")),
            "source": "lever",
        })
    return out


def fetch_ashby(token):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [ashby:{token}] fetch failed: {e}", file=sys.stderr)
        return []
    out = []
    for j in data.get("jobs", []):
        comp = j.get("compensation") or {}
        out.append({
            "job_id": f"ab-{j.get('id')}",
            "title": j.get("title", ""),
            "location": j.get("location", ""),
            "department": j.get("department", ""),
            "employment_type": j.get("workplaceType", ""),
            "compensation": comp.get("compensationTierSummary", ""),
            "posted_date": (j.get("publishedAt") or j.get("updatedAt") or "")[:10],
            "url": j.get("jobUrl") or j.get("applyUrl", ""),
            "description_snippet": clean_html(j.get("descriptionHtml", "")),
            "source": "ashby",
        })
    return out


def fetch_smartrecruiters(token):
    out = []
    offset = 0
    for _ in range(5):  # cap at 500 postings
        url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100&offset={offset}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  [smartrecruiters:{token}] fetch failed: {e}", file=sys.stderr)
            break
        content = data.get("content", [])
        if not content:
            break
        for j in content:
            loc = j.get("location") or {}
            loc_str = ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")]))
            # postingUrl isn't always present on the list endpoint; this is a
            # best-guess construction if it's missing - verify before relying on it.
            guessed_url = f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}"
            out.append({
                "job_id": f"sr-{j.get('id')}",
                "title": j.get("name", ""),
                "location": loc_str,
                "department": (j.get("department") or {}).get("label", ""),
                "employment_type": (j.get("typeOfEmployment") or {}).get("label", ""),
                "compensation": "",
                "posted_date": (j.get("releasedDate") or "")[:10],
                "url": j.get("postingUrl") or guessed_url,
                "description_snippet": "",
                "source": "smartrecruiters",
            })
        offset += 100
        if offset >= data.get("totalFound", 0):
            break
    return out


def fetch_generic(name, careers_url):
    """
    Best-effort fallback for companies with no public ATS. Scans raw HTML for
    <a> tags whose text looks like a match. Some career sites render jobs via
    JavaScript, which this can't see - 0 results here can mean "check
    manually," not "no jobs."
    """
    try:
        r = requests.get(careers_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        print(f"  [generic:{name}] fetch failed: {e}", file=sys.stderr)
        return []
    anchor_re = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    out = []
    for href, inner in anchor_re.findall(html):
        text = re.sub(r"<[^>]+>", "", inner)
        text = unescape(re.sub(r"\s+", " ", text)).strip()
        if not text or not matches_role(text):
            continue
        full_url = href if href.startswith("http") else urljoin(careers_url, href)
        out.append({
            "job_id": f"gen-{full_url}",
            "title": text,
            "location": "",
            "department": "",
            "employment_type": "",
            "compensation": "",
            "posted_date": "",
            "url": full_url,
            "description_snippet": "",
            "source": "generic",
        })
    if not out:
        print(f"  [generic:{name}] 0 matches - page may be JS-rendered, check manually", file=sys.stderr)
    return out


FETCHERS = {
    "greenhouse": lambda row: fetch_greenhouse(row["token"]),
    "lever": lambda row: fetch_lever(row["token"]),
    "ashby": lambda row: fetch_ashby(row["token"]),
    "smartrecruiters": lambda row: fetch_smartrecruiters(row["token"]),
    "generic": lambda row: fetch_generic(row["company_name"], row["url"]),
}


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

def load_companies():
    if not COMPANIES_FILE.exists():
        print(f"{COMPANIES_FILE} not found.")
        return []
    with open(COMPANIES_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # DictReader tolerates any extra columns you add - they just ride along
    # unused. Rows with active=NO are skipped without being deleted.
    return [r for r in rows if (r.get("active", "YES") or "YES").strip().upper() != "NO"]


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


JOBS_FIELDNAMES = [
    "company", "title", "location", "department", "employment_type",
    "compensation", "source", "posted_date", "first_seen_date", "url",
    "description_snippet",
]


def write_jobs_csv(jobs):
    with open(JOBS_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=JOBS_FIELDNAMES)
        w.writeheader()
        for j in jobs:
            w.writerow({k: j.get(k, "") for k in JOBS_FIELDNAMES})


def send_email(new_jobs):
    smtp_user = os.environ.get("GMAIL_USER")
    smtp_pass = os.environ.get("GMAIL_APP_PASSWORD")
    to_addr = os.environ.get("ALERT_TO", smtp_user)
    if not smtp_user or not smtp_pass:
        print("GMAIL_USER / GMAIL_APP_PASSWORD not set - skipping email, listing instead:")
        for j in new_jobs:
            print(f"- [{j['company']}] {j['title']} ({j.get('location','')}) -> {j['url']}")
        return
    lines = [f"{j['company']}: {j['title']} ({j.get('location','')})\n{j['url']}\n" for j in new_jobs]
    msg = MIMEText("\n".join(lines))
    msg["Subject"] = f"[Job Alert] {len(new_jobs)} new Financial Analyst posting(s)"
    msg["From"] = smtp_user
    msg["To"] = to_addr
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [to_addr], msg.as_string())
    print(f"Emailed {len(new_jobs)} new posting(s) to {to_addr}")


def main():
    companies = load_companies()
    if not companies:
        print("No active companies in companies.csv.")
        return

    state = load_state()  # job_id -> first_seen_date (YYYY-MM-DD)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    all_matching = []
    new_jobs = []

    for row in companies:
        name = (row.get("company_name") or "").strip()
        ats = (row.get("ats_type") or "generic").strip().lower()
        fetcher = FETCHERS.get(ats, fetch_generic)
        print(f"Checking {name} ({ats})...")
        found = fetcher(row)

        for j in found:
            if not matches_role(j["title"]):
                continue
            if not matches_us_location(j["location"]):
                continue
            j["company"] = name
            jid = j["job_id"]
            if jid in state:
                j["first_seen_date"] = state[jid]
            else:
                j["first_seen_date"] = today
                state[jid] = today
                new_jobs.append(j)
            all_matching.append(j)

    save_state(state)
    write_jobs_csv(all_matching)
    print(f"{len(all_matching)} matching role(s) written to {JOBS_FILE.name} ({len(new_jobs)} new)")

    if new_jobs:
        send_email(new_jobs)


if __name__ == "__main__":
    main()
