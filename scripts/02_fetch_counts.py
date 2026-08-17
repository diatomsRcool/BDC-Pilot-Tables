"""
02_fetch_counts.py

For each phv in data/phv_by_cohort_class.csv, fetch the non-missing count
(n) from the publicly accessible dbGAP var_report.xml files on the NCBI FTP.

The var_report.xml files contain:
    <stat n="12518" nulls="253" .../>
where n = number of available (non-missing) observations.

Downloaded files are cached locally in data/var_report_cache/ so the script
can be re-run without re-downloading everything.

Usage:
    python scripts/02_fetch_counts.py

Input:
    data/phv_by_cohort_class.csv

Output:
    data/phv_counts.csv
    Columns: phs, pht, phv, n, count_available
"""

import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

FTP_BASE = "https://ftp.ncbi.nlm.nih.gov/dbgap/studies"
CACHE_DIR = Path("data/var_report_cache")
INPUT_CSV = Path("data/phv_by_cohort_class.csv")
OUTPUT_CSV = Path("data/phv_counts.csv")

# Seconds to wait between FTP requests to be polite to NCBI
REQUEST_DELAY = 0.5


# --------------------------------------------------------------------------
# FTP helpers
# --------------------------------------------------------------------------

def get_latest_study_version(phs: str) -> str:
    """
    Return the directory name for the latest version of a dbGAP study.
    E.g., 'phs000280.v7.p1'
    """
    url = f"{FTP_BASE}/{phs}/"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    # The FTP-over-HTTP directory listing has links like 'phs000280.v7.p1/'
    pattern = re.compile(rf'href="({re.escape(phs)}\.v(\d+)\.p\d+)/?"')
    matches = pattern.findall(resp.text)
    if not matches:
        raise ValueError(f"No version directories found for {phs} at {url}")

    # Sort by version number and return the highest
    latest = sorted(matches, key=lambda m: int(m[1]))[-1][0]
    return latest


def list_var_report_files(phs: str, study_version: str) -> list[str]:
    """
    Return all .var_report.xml filenames in the pheno_variable_summaries
    directory for the given study version.
    """
    url = f"{FTP_BASE}/{phs}/{study_version}/pheno_variable_summaries/"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY)

    filenames = re.findall(r'href="([^"]+\.var_report\.xml)"', resp.text)
    # Strip any path prefix — keep only the filename
    return [f.split("/")[-1] for f in filenames]


def download_var_report(phs: str, study_version: str, filename: str) -> Path:
    """
    Download a var_report.xml file to the local cache if not already present.
    Returns the local path.
    """
    local_path = CACHE_DIR / phs / filename
    if local_path.exists():
        return local_path

    local_path.parent.mkdir(parents=True, exist_ok=True)
    url = (
        f"{FTP_BASE}/{phs}/{study_version}/pheno_variable_summaries/{filename}"
    )
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    local_path.write_bytes(resp.content)
    time.sleep(REQUEST_DELAY)
    return local_path


# --------------------------------------------------------------------------
# var_report XML parsing
# --------------------------------------------------------------------------

def parse_var_report(xml_path: Path) -> dict[str, int]:
    """
    Parse a var_report.xml file and return a dict mapping phv base IDs
    to non-missing counts (n).

    phv base ID strips the version suffix:
        phv00022796.v1.p1  ->  phv00022796
    """
    phv_to_n = {}
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        print(f"  WARNING: XML parse error in {xml_path.name}: {e}", file=sys.stderr)
        return phv_to_n

    root = tree.getroot()

    for variable in root.iter("variable"):
        var_id = variable.get("id", "")
        # Strip version: phv00022796.v1.p1 -> phv00022796
        phv_base = var_id.split(".")[0]
        if not phv_base.startswith("phv"):
            continue

        # Get n from <total><stats><stat n="..."/>
        stat = variable.find("./total/stats/stat")
        if stat is not None:
            n_str = stat.get("n", "")
            try:
                n = int(n_str)
                phv_to_n[phv_base] = n
            except (ValueError, TypeError):
                pass

    return phv_to_n


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    if not INPUT_CSV.exists():
        sys.exit(f"ERROR: {INPUT_CSV} not found. Run 01_parse_yaml_phvs.py first.")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(INPUT_CSV, dtype=str)
    print(f"Loaded {len(df)} rows from {INPUT_CSV}")

    # Work at the level of unique (phs, pht, phv) triples
    unique_targets = (
        df[["phs", "pht", "phv"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    print(f"Unique (phs, pht, phv) targets: {len(unique_targets)}")

    # Group targets by phs so we can process one study at a time
    phs_groups = unique_targets.groupby("phs")

    results = []

    for phs, group in phs_groups:
        print(f"\nProcessing {phs} ({len(group)} phv targets)...")
        target_phvs = set(group["phv"].tolist())
        target_phts = set(group["pht"].dropna().tolist())

        # Find latest study version on FTP
        try:
            study_version = get_latest_study_version(phs)
            print(f"  Latest version: {study_version}")
        except Exception as e:
            print(f"  ERROR finding study version: {e}", file=sys.stderr)
            for _, row in group.iterrows():
                results.append({
                    "phs": phs, "pht": row["pht"], "phv": row["phv"],
                    "n": 0, "count_available": False,
                })
            continue

        # List var_report files for this study
        try:
            all_var_report_files = list_var_report_files(phs, study_version)
            print(f"  Found {len(all_var_report_files)} var_report files total")
        except Exception as e:
            print(f"  ERROR listing var_report files: {e}", file=sys.stderr)
            for _, row in group.iterrows():
                results.append({
                    "phs": phs, "pht": row["pht"], "phv": row["phv"],
                    "n": 0, "count_available": False,
                })
            continue

        # Filter to only files for our target pht tables.
        # Filename pattern: phs000280.v7.pht004063.v2.p1.TABLE.var_report.xml
        relevant_files = []
        for filename in all_var_report_files:
            for pht in target_phts:
                if pht in filename:
                    relevant_files.append(filename)
                    break
        print(f"  Relevant var_report files for target phts: {len(relevant_files)}")

        # Also include files from the same phs (in case phv appears in a
        # table whose pht wasn't captured — e.g., expr-referenced phvs)
        # We'll search relevant files first, then fall back to all files
        # only for phvs not yet found.

        # Build phv -> n mapping from relevant files
        phv_to_n = {}
        for filename in relevant_files:
            try:
                local_path = download_var_report(phs, study_version, filename)
                phv_to_n.update(parse_var_report(local_path))
            except Exception as e:
                print(f"  WARNING: could not process {filename}: {e}", file=sys.stderr)

        # Check which phvs are still missing and search remaining files
        missing_phvs = target_phvs - set(phv_to_n.keys())
        if missing_phvs:
            print(f"  {len(missing_phvs)} phvs not yet found; scanning remaining files...")
            remaining_files = [f for f in all_var_report_files if f not in relevant_files]
            for filename in remaining_files:
                if not missing_phvs:
                    break
                try:
                    local_path = download_var_report(phs, study_version, filename)
                    batch = parse_var_report(local_path)
                    for phv in list(missing_phvs):
                        if phv in batch:
                            phv_to_n[phv] = batch[phv]
                            missing_phvs.discard(phv)
                except Exception as e:
                    print(f"  WARNING: could not process {filename}: {e}", file=sys.stderr)

        # Record results
        found = 0
        not_found = 0
        for _, row in group.iterrows():
            phv = row["phv"]
            if phv in phv_to_n:
                results.append({
                    "phs": phs, "pht": row["pht"], "phv": phv,
                    "n": phv_to_n[phv], "count_available": True,
                })
                found += 1
            else:
                results.append({
                    "phs": phs, "pht": row["pht"], "phv": phv,
                    "n": 0, "count_available": False,
                })
                not_found += 1

        print(f"  Found counts for {found}/{found + not_found} phvs")
        if not_found:
            unfound = [
                r["phv"] for r in results
                if r["phs"] == phs and not r["count_available"]
            ]
            print(f"  Not found: {unfound[:10]}" + (" ..." if len(unfound) > 10 else ""))

    out_df = pd.DataFrame(results)
    out_df.to_csv(OUTPUT_CSV, index=False)
    print(f"\nWrote {len(out_df)} rows to {OUTPUT_CSV}")

    total_available = out_df["count_available"].sum()
    total = len(out_df)
    print(f"Counts available: {total_available}/{total} phvs "
          f"({100 * total_available / total:.1f}%)")

    if (out_df["count_available"] == False).any():
        print("\nWARNING: The following phvs have no count (n=0 in output):")
        missing = out_df[out_df["count_available"] == False][["phs", "pht", "phv"]]
        print(missing.to_string(index=False))


if __name__ == "__main__":
    main()
