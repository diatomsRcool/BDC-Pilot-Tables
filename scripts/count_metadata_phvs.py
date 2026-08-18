"""
count_metadata_phvs.py

For each cohort, count phvs that appear ONLY in metadata slots (i.e. slots
not in DATA_SLOTS). Any phv that also appears in a data slot is excluded from
the metadata-only count.

DATA_SLOTS: condition_status, exposure_status, procedure_status,
            value_string, value_quantity, value_boolean, value_enum

Usage:
    python scripts/count_metadata_phvs.py --repo /path/to/NHLBI-BDC-DMC-HV

Output:
    data/metadata_phv_counts.csv
    Columns: cohort, metadata_only_phvs, data_slot_phvs, total_phvs
"""

import argparse
import re
import sys
import yaml
import pandas as pd
from pathlib import Path

COHORT_MAP = {
    "ARIC-ingest":    "ARIC",
    "CARDIA-ingest":  "CARDIA",
    "CHS-ingest":     "CHS",
    "COPDGene-ingest":"COPDGene",
    "FHS-ingest":     "FHS",
    "HCHS-ingest":    "HCHS",
    "JHS-ingest":     "JHS",
    "MESA-ingest":    "MESA",
    "WHI-ingest":     "WHI",
}

SKIP_FILES = {
    "participant.yaml",
    "visit.yaml",
    "person.yaml",
    "researchstudy.yaml",
    "research_study.yaml",
    "demography.yaml",
}

DATA_SLOTS = {
    "condition_status",
    "exposure_status",
    "procedure_status",
    "value_string",
    "value_quantity",
    "value_boolean",
    "value_enum",
}

PHV_RE = re.compile(r"\bphv\d{8}\b")


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


def find_data_slot_phvs(obj) -> set:
    """Recursively collect phvs found inside any slot whose name is in DATA_SLOTS."""
    phvs = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in DATA_SLOTS:
                phvs.update(find_all_phvs(value))
            else:
                phvs.update(find_data_slot_phvs(value))
    elif isinstance(obj, list):
        for item in obj:
            phvs.update(find_data_slot_phvs(item))
    return phvs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        required=True,
        help="Path to local clone of NHLBI-BDC-DMC-HV repository",
    )
    parser.add_argument(
        "--output",
        default="data/metadata_phv_counts.csv",
        help="Output CSV path (default: data/metadata_phv_counts.csv)",
    )
    args = parser.parse_args()

    transform_root = Path(args.repo) / "priority_variables_transform"
    if not transform_root.is_dir():
        sys.exit(f"ERROR: priority_variables_transform not found at {transform_root}")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for dir_name, cohort in sorted(COHORT_MAP.items()):
        cohort_dir = transform_root / dir_name
        if not cohort_dir.is_dir():
            print(f"WARNING: directory not found: {cohort_dir}", file=sys.stderr)
            continue

        all_phvs: set = set()
        data_phvs: set = set()

        for yaml_path in sorted(cohort_dir.glob("*.yaml")):
            if yaml_path.name in SKIP_FILES:
                continue
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data is None:
                continue
            all_phvs.update(find_all_phvs(data))
            data_phvs.update(find_data_slot_phvs(data))

        metadata_only = all_phvs - data_phvs
        rows.append({
            "cohort":             cohort,
            "metadata_only_phvs": len(metadata_only),
            "data_slot_phvs":     len(data_phvs),
            "total_phvs":         len(all_phvs),
        })

    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False)

    print(f"{'Cohort':<12} {'Metadata-only phvs':>20} {'Data-slot phvs':>16} {'Total phvs':>12}")
    print("-" * 64)
    for _, row in df.iterrows():
        print(f"{row['cohort']:<12} {row['metadata_only_phvs']:>20,} {row['data_slot_phvs']:>16,} {row['total_phvs']:>12,}")

    print(f"\nWrote {len(df)} rows to {output_path}")


if __name__ == "__main__":
    main()
