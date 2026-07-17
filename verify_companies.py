#!/usr/bin/env python3
"""
Verifies every row in candidate_companies.csv by actually calling its API
endpoint (or fetching its careers URL for "generic" rows) and checking for a
real, non-empty response. This exists because the candidate list was
compiled from a mix of directly-verified searches and a secondary
community-maintained mapping that I have not personally re-tested one by
one - this script closes that gap for real, since it runs somewhere with
actual network access (your machine, or a GitHub Actions runner), unlike
the environment that built the list.

Run:  python verify_companies.py

Output: verified_companies.csv - only the rows that returned real,
non-empty results, in the exact schema companies.csv expects, ready to
copy-paste or append.

Also prints a summary of what failed and why, so you're not left guessing.
"""

import csv
import sys
from pathlib import Path

import requests

HERE = Path(__file__).parent
CANDIDATES_FILE = HERE / "candidate_companies.csv"
VERIFIED_FILE = HERE / "verified_companies.csv"

HEADERS = {"User-Agent": "Mozilla/5.0 (personal job-alert crawler, verification pass)"}
TIMEOUT = 15


def check_greenhouse(token):
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        data = r.json()
        jobs = data.get("jobs", [])
        return (len(jobs) > 0), f"{len(jobs)} jobs found"
    except Exception as e:
        return False, str(e)


def check_lever(token):
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        data = r.json()
        return (isinstance(data, list) and len(data) > 0), f"{len(data) if isinstance(data, list) else 0} jobs found"
    except Exception as e:
        return False, str(e)


def check_ashby(token):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        data = r.json()
        jobs = data.get("jobs", [])
        return (len(jobs) > 0), f"{len(jobs)} jobs found"
    except Exception as e:
        return False, str(e)


def check_smartrecruiters(token):
    url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=1"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        data = r.json()
        total = data.get("totalFound", 0)
        return (total > 0), f"{total} jobs found"
    except Exception as e:
        return False, str(e)


def check_generic(url):
    if not url:
        return False, "no URL provided"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        return (len(r.text) > 500), f"page loaded, {len(r.text)} bytes (can't confirm jobs are parseable without running crawler.py itself)"
    except Exception as e:
        return False, str(e)


CHECKERS = {
    "greenhouse": lambda row: check_greenhouse(row["token"]),
    "lever": lambda row: check_lever(row["token"]),
    "ashby": lambda row: check_ashby(row["token"]),
    "smartrecruiters": lambda row: check_smartrecruiters(row["token"]),
    "generic": lambda row: check_generic(row["url"]),
}


def main():
    if not CANDIDATES_FILE.exists():
        print(f"{CANDIDATES_FILE} not found.")
        return

    with open(CANDIDATES_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    passed = []
    failed = []

    for row in rows:
        name = row.get("company_name", "").strip()
        ats = (row.get("ats_type") or "generic").strip().lower()
        checker = CHECKERS.get(ats, check_generic)
        ok, detail = checker(row)
        status = "PASS" if ok else "FAIL"
        print(f"{status}  {name:25s} ({ats:15s}) - {detail}")
        if ok:
            passed.append(row)
        else:
            failed.append((row, detail))

    # Write verified rows in the exact schema companies.csv expects
    out_fieldnames = ["company_name", "ats_type", "token", "url", "active", "notes"]
    with open(VERIFIED_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out_fieldnames)
        w.writeheader()
        for row in passed:
            w.writerow({
                "company_name": row["company_name"],
                "ats_type": row["ats_type"],
                "token": row.get("token", ""),
                "url": row.get("url", ""),
                "active": "YES",
                "notes": f"Verified live by verify_companies.py",
            })

    print(f"\n{len(passed)} of {len(rows)} passed verification -> {VERIFIED_FILE.name}")
    if failed:
        print(f"{len(failed)} failed - review before adding these manually:")
        for row, detail in failed:
            print(f"  - {row['company_name']}: {detail}")


if __name__ == "__main__":
    main()
