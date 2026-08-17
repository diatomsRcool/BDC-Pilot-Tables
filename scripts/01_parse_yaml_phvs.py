"""
01_parse_yaml_phvs.py

Parse all YAML transform specification files for the 9 in-scope cohorts and
extract the dbGAP phenotype variable (phv) numbers that were integrated,
excluding phv numbers used only for participant/visit linkage.

Usage:
    python scripts/01_parse_yaml_phvs.py --repo /path/to/NHLBI-BDC-DMC-HV

Output:
    data/phv_by_cohort_class.csv
    Columns: cohort, phs, bdchm_class, row_category, variable_file, pht, phv
"""

import argparse
import re
import sys
import yaml
import pandas as pd
from pathlib import Path

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

COHORT_MAP = {
    "ARIC-ingest":    ("ARIC",     "phs000280"),
    "CARDIA-ingest":  ("CARDIA",   "phs000285"),
    "CHS-ingest":     ("CHS",      "phs000287"),
    "COPDGene-ingest":("COPDGene", "phs000179"),
    "FHS-ingest":     ("FHS",      "phs000007"),
    "HCHS-ingest":    ("HCHS",     "phs000810"),
    "JHS-ingest":     ("JHS",      "phs000286"),
    "MESA-ingest":    ("MESA",     "phs000209"),
    "WHI-ingest":     ("WHI",      "phs000200"),
}

# YAML files that are infrastructure, not harmonized variables
SKIP_FILES = {
    "participant.yaml",
    "visit.yaml",
    "person.yaml",
    "researchstudy.yaml",
    "demography.yaml",
}

# Slot names whose phv references are linkage-only (not data variables)
LINKAGE_SLOTS = {"associated_participant", "associated_visit"}

# Map BDCHM class names to table row labels
CLASS_TO_ROW = {
    "Condition":        "Conditions",
    "DrugExposure":     "Drug Exposures",
    "Procedure":        "Procedures",
    "SdohObservation":  "SdohObservations",
}
CONTINUOUS_ROW = "Continuous variables"

# phv numbers are exactly 8 digits after "phv"
PHV_RE = re.compile(r"\bphv\d{8}\b")
# pht table identifiers (6-7 digits after "pht")
PHT_RE = re.compile(r"\bpht\d{6,7}\b")


# --------------------------------------------------------------------------
# YAML parsing helpers
# --------------------------------------------------------------------------

def find_all_phvs(obj) -> set:
    """Recursively collect all phv identifiers in a YAML structure."""
    phvs = set()
    if isinstance(obj, str):
        phvs.update(PHV_RE.findall(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            phvs.update(find_all_phvs(v))
    elif isinstance(obj, list):
        for item in obj:
            phvs.update(find_all_phvs(item))
    return phvs


def find_excluded_phvs(slot_derivations: dict) -> set:
    """Return phvs that appear only in linkage slots (participant/visit)."""
    excluded = set()
    for slot_name in LINKAGE_SLOTS:
        slot = slot_derivations.get(slot_name)
        if slot:
            excluded.update(find_all_phvs(slot))
    return excluded


def parse_yaml_file(yaml_path: Path) -> list[dict]:
    """
    Parse one YAML file and return a list of row dicts, one per
    (derivation_block × data_phv).

    A YAML file can be either:
      - A list of blocks:  [{ class_derivations: { CLASS: {...} } }, ...]
      - A single dict:     { class_derivations: { CLASS: {...} } }
    """
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        return []

    blocks = data if isinstance(data, list) else [data]

    rows = []
    bdchm_class = None  # determined from the first block

    for block in blocks:
        if not isinstance(block, dict):
            continue
        class_defs = block.get("class_derivations") or {}

        for class_name, class_def in class_defs.items():
            # Record the BDCHM class from the first block encountered
            if bdchm_class is None:
                bdchm_class = class_name

            if not isinstance(class_def, dict):
                continue

            # pht table this block is sourced from
            raw_populated = class_def.get("populated_from", "")
            pht_matches = PHT_RE.findall(str(raw_populated))
            pht = pht_matches[0] if pht_matches else ""

            slot_defs = class_def.get("slot_derivations") or {}

            all_phvs = find_all_phvs(class_def)
            excluded_phvs = find_excluded_phvs(slot_defs)
            data_phvs = all_phvs - excluded_phvs

            for phv in sorted(data_phvs):
                rows.append({
                    "bdchm_class": class_name,
                    "pht": pht,
                    "phv": phv,
                })

    return rows, bdchm_class


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        required=True,
        help="Path to local clone of NHLBI-BDC-DMC-HV repository",
    )
    parser.add_argument(
        "--output",
        default="data/phv_by_cohort_class.csv",
        help="Output CSV path (default: data/phv_by_cohort_class.csv)",
    )
    args = parser.parse_args()

    repo_path = Path(args.repo)
    transform_root = repo_path / "priority_variables_transform"

    if not transform_root.is_dir():
        sys.exit(
            f"ERROR: priority_variables_transform directory not found at {transform_root}"
        )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    all_rows = []
    skipped_files = []
    unknown_class_files = []

    for dir_name, (cohort, phs) in sorted(COHORT_MAP.items()):
        cohort_dir = transform_root / dir_name
        if not cohort_dir.is_dir():
            print(f"WARNING: directory not found: {cohort_dir}", file=sys.stderr)
            continue

        yaml_files = sorted(cohort_dir.glob("*.yaml"))
        print(f"{cohort}: found {len(yaml_files)} YAML files")

        for yaml_path in yaml_files:
            if yaml_path.name in SKIP_FILES:
                skipped_files.append(yaml_path.name)
                continue

            try:
                rows, bdchm_class = parse_yaml_file(yaml_path)
            except Exception as e:
                print(f"  ERROR parsing {yaml_path.name}: {e}", file=sys.stderr)
                continue

            if not rows:
                print(f"  WARNING: no data phvs found in {yaml_path.name}")
                continue

            if bdchm_class is None:
                unknown_class_files.append(str(yaml_path))
                row_category = "Unknown"
            else:
                row_category = CLASS_TO_ROW.get(bdchm_class, CONTINUOUS_ROW)

            for row in rows:
                all_rows.append({
                    "cohort":         cohort,
                    "phs":            phs,
                    "bdchm_class":    row["bdchm_class"],
                    "row_category":   row_category,
                    "variable_file":  yaml_path.stem,
                    "pht":            row["pht"],
                    "phv":            row["phv"],
                })

        print(f"  -> {sum(1 for r in all_rows if r['cohort'] == cohort)} phv rows accumulated so far")

    df = pd.DataFrame(all_rows)

    # Deduplicate: same phv can appear in multiple derivation blocks within
    # a file (different exam visits, etc.). Keep one row per unique
    # (cohort, phs, bdchm_class, row_category, variable_file, pht, phv).
    before = len(df)
    df = df.drop_duplicates()
    after = len(df)
    if before != after:
        print(f"Removed {before - after} duplicate rows")

    df.to_csv(output_path, index=False)
    print(f"\nWrote {len(df)} rows to {output_path}")

    # Summary by cohort x row_category
    print("\nSummary (unique phvs per cohort x row_category):")
    summary = (
        df.groupby(["cohort", "row_category"])["phv"]
        .nunique()
        .unstack(fill_value=0)
    )
    print(summary.to_string())

    if unknown_class_files:
        print(f"\nWARNING: could not determine BDCHM class for:\n  " +
              "\n  ".join(unknown_class_files))


if __name__ == "__main__":
    main()
