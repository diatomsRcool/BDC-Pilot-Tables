"""
01_parse_yaml_phvs.py

Parse all YAML transform specification files for the 9 in-scope cohorts and
extract the dbGAP phenotype variable (phv) numbers that appear in DATA_SLOTS
— the slots where actual data values are recorded.

Usage:
    python scripts/01_parse_yaml_phvs.py --repo /path/to/NHLBI-BDC-DMC-HV

Output:
    data/phv_by_cohort_class.csv
    Columns: cohort, phs, bdchm_class, row_category, variable_file, pht, phv

  All other slot names encountered (metadata slots) are printed at the end.
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
    "research_study.yaml",   # alternate filename used in some cohorts
    "demography.yaml",
}

# Slots where actual data values are recorded.
# Only phvs found in these slots are counted (for both n vars and n data pts).
# Everything else (age_at_observation, associated_visit case-filters, etc.)
# is treated as metadata and excluded.
DATA_SLOTS = {
    "condition_status",
    "exposure_status",
    "procedure_status",
    "value_string",
    "value_quantity",
    "value_boolean",
    "value_enum",
}

# Map BDCHM class names to table row labels
CLASS_TO_ROW = {
    "Condition":        "Conditions",
    "DrugExposure":     "Drug Exposures",
    "Procedure":        "Procedures",
    "SdohObservation":  "SdohObservations",
}
CONTINUOUS_ROW = "Continuous variables"

# Files that use MeasurementObservationSet and need sub-observation parsing
OBSERVATION_SET_FILES = {"spirometry"}

# Map OMOP observation_type codes to concept row labels for spirometry sub-observations.
# Codes not listed here are ignored (e.g., % predicted values).
SPIROMETRY_OMOP_MAP = {
    "OMOP:4241837": "fev1",
    "OMOP:4176265": "fvc",
    "OMOP:3011505": "fev1_fvc",
}

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


def find_data_slot_phvs(obj) -> set:
    """
    Recursively walk the YAML structure and return phvs found inside any slot
    whose name is in DATA_SLOTS.

    When a DATA_SLOTS key is found, all phvs within that slot's entire subtree
    are collected (handles value_quantity nested inside MeasurementObservationSet
    sub-observations). For all other keys the function recurses deeper without
    collecting phvs.
    """
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


def collect_slot_names(obj, result: set) -> None:
    """
    Recursively collect all slot names (keys of slot_derivations dicts)
    encountered anywhere in the YAML structure.
    """
    if isinstance(obj, dict):
        if "slot_derivations" in obj:
            sd = obj["slot_derivations"]
            if isinstance(sd, dict):
                result.update(sd.keys())
        for v in obj.values():
            collect_slot_names(v, result)
    elif isinstance(obj, list):
        for item in obj:
            collect_slot_names(item, result)


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

            data_phvs = find_data_slot_phvs(class_def)

            for phv in sorted(data_phvs):
                rows.append({
                    "bdchm_class": class_name,
                    "pht": pht,
                    "phv": phv,
                })

    return rows, bdchm_class


def parse_observation_set_yaml(yaml_path: Path, omop_map: dict) -> list[dict]:
    """
    Parse a MeasurementObservationSet YAML file and return one row dict per
    (concept × data_phv), where concept is determined by the observation_type
    OMOP code of each sub-observation.

    Sub-observations whose OMOP code is not in omop_map are silently skipped.
    """
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        return []

    blocks = data if isinstance(data, list) else [data]
    rows = []

    for block in blocks:
        if not isinstance(block, dict):
            continue
        class_defs = block.get("class_derivations") or {}

        for class_name, class_def in class_defs.items():
            if not isinstance(class_def, dict):
                continue

            # pht at the parent block level (used as fallback)
            parent_pht_matches = PHT_RE.findall(str(class_def.get("populated_from", "")))
            parent_pht = parent_pht_matches[0] if parent_pht_matches else ""

            # Navigate to the observations list inside slot_derivations
            parent_slots = class_def.get("slot_derivations") or {}
            observations_slot = parent_slots.get("observations")
            if not isinstance(observations_slot, dict):
                continue
            sub_blocks = observations_slot.get("class_derivations") or []
            if not isinstance(sub_blocks, list):
                sub_blocks = [sub_blocks]

            for sub_block in sub_blocks:
                if not isinstance(sub_block, dict):
                    continue
                for sub_class_name, sub_class_def in sub_block.items():
                    if not isinstance(sub_class_def, dict):
                        continue

                    # Determine concept from observation_type.value OMOP code
                    sub_slots = sub_class_def.get("slot_derivations") or {}
                    obs_type_slot = sub_slots.get("observation_type") or {}
                    omop_code = obs_type_slot.get("value", "")
                    concept = omop_map.get(omop_code)
                    if concept is None:
                        continue  # skip ignored measurements

                    # pht: prefer sub-observation's own populated_from
                    sub_pht_matches = PHT_RE.findall(
                        str(sub_class_def.get("populated_from", ""))
                    )
                    pht = sub_pht_matches[0] if sub_pht_matches else parent_pht

                    data_phvs = find_data_slot_phvs(sub_class_def)

                    for phv in sorted(data_phvs):
                        rows.append({
                            "bdchm_class": sub_class_name,
                            "pht": pht,
                            "phv": phv,
                            "concept": concept,
                        })

    return rows


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
    all_slot_names: set = set()

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

            # Special handling for MeasurementObservationSet files (e.g. spirometry):
            # parse sub-observations and emit one row per concept.
            if yaml_path.stem in OBSERVATION_SET_FILES:
                try:
                    with open(yaml_path, encoding="utf-8") as f:
                        raw_yaml = yaml.safe_load(f)
                    if raw_yaml:
                        collect_slot_names(raw_yaml, all_slot_names)
                    obs_rows = parse_observation_set_yaml(yaml_path, SPIROMETRY_OMOP_MAP)
                except Exception as e:
                    print(f"  ERROR parsing {yaml_path.name}: {e}", file=sys.stderr)
                    continue
                if not obs_rows:
                    print(f"  WARNING: no sub-observation phvs found in {yaml_path.name}")
                    continue
                for row in obs_rows:
                    all_rows.append({
                        "cohort":        cohort,
                        "phs":           phs,
                        "bdchm_class":   row["bdchm_class"],
                        "row_category":  CONTINUOUS_ROW,
                        "variable_file": row["concept"],
                        "pht":           row["pht"],
                        "phv":           row["phv"],
                    })
                continue

            try:
                with open(yaml_path, encoding="utf-8") as f:
                    raw_yaml = yaml.safe_load(f)
                if raw_yaml:
                    collect_slot_names(raw_yaml, all_slot_names)
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

    metadata_slots = sorted(all_slot_names - DATA_SLOTS)
    print(f"\nMetadata slots (not in DATA_SLOTS, phvs excluded from count):")
    for s in metadata_slots:
        print(f"  {s}")


if __name__ == "__main__":
    main()
