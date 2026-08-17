# Work Plan: Publication Tables for dbGAP Data Integration

## Overview

This project produces two publication tables describing the integration of data across long-term biomedical cohorts in dbGAP using the BDCHM data model and the LinkML ecosystem.

- **Before table**: Raw dbGAP variables per cohort, grouped by BDCHM class
- **After table**: Harmonized output (to be designed after before table is complete)

Source transform specifications: https://github.com/RTIInternational/NHLBI-BDC-DMC-HV/tree/main/priority_variables_transform
Variable list: https://docs.google.com/spreadsheets/d/1PDaX266_H0haa0aabMYQ6UNtEKT5-ClMarP0FvNntN8/edit?gid=125829256#gid=125829256
BDCHM schema: https://github.com/RTIInternational/NHLBI-BDC-DMC-HM

---

## Cohort to phs Mapping

| Directory | Cohort | phs accession |
|---|---|---|
| ARIC-ingest | ARIC | phs000280 |
| FHS-ingest | FHS | phs000007 |
| CARDIA-ingest | CARDIA | phs000285 |
| CHS-ingest | CHS | phs000287 |
| HCHS-ingest | HCHS/SOL | phs000810 |
| JHS-ingest | JHS | phs000286 |
| COPDGene-ingest | COPDGene | phs000179 |
| MESA-ingest | MESA | phs000209 |
| WHI-ingest | WHI | phs000200 |

Note: LTRC-ingest and SPIROMICS-ingest exist in the repo but are not in scope for this publication.

**Action**: Confirm this mapping before finalizing column headers.

---

## Before Table Design

### Row structure

Each row corresponds to a BDCHM class. All variables of a given class are collapsed into a single row:

| Row | BDCHM class |
|---|---|
| Conditions | `Condition` |
| Drug Exposures | `DrugExposure` |
| Procedures | `Procedure` |
| SdohObservations | `SdohObservation` |
| Continuous variables | measurement classes (e.g., `Measurement`, `BodyMeasurement`) |
| **Total** | (grand total row) |

### Column structure

For each of the 9 cohorts, two columns:
1. **n vars**: number of unique phv numbers integrated from that cohort for that class
2. **n data points**: total non-missing records across all those phv variables

The final two columns are **Total n vars** and **Total n data points** across all cohorts.

### Example layout

| Variable Type | ARIC n vars | ARIC n pts | FHS n vars | FHS n pts | ... | Total n vars | Total n pts |
|---|---|---|---|---|---|---|---|
| Conditions | | | | | | | |
| Drug Exposures | | | | | | | |
| Procedures | | | | | | | |
| SdohObservations | | | | | | | |
| Continuous variables | | | | | | | |
| **Total** | | | | | | | |

---

## Step 1: Clone the Transform Repository

```bash
git clone https://github.com/RTIInternational/NHLBI-BDC-DMC-HV.git
```

Working directory for YAML parsing: `priority_variables_transform/`

---

## Step 2: Parse YAML Files to Extract phv Numbers

Write `scripts/01_parse_yaml_phvs.py`.

### Files to skip (infrastructure, not harmonized variables)

Within each cohort ingest directory, skip:
- `participant.yaml`
- `visit.yaml`
- `person.yaml`
- `researchstudy.yaml`
- `demography.yaml`

### Logic

For each remaining YAML file in each of the 9 cohort directories:

1. Identify the BDCHM class from the `class_derivations` top-level key (e.g., `Condition`, `DrugExposure`, `Procedure`, `SdohObservation`, measurement classes)
2. Extract all phv numbers (regex: `phv\d{8}`) from the entire file
3. Extract phv numbers that appear **only** in `associated_participant` and `associated_visit` slot derivation expressions — these are linkage variables, not data variables
4. Subtract the linkage phv set from the full phv set to get data phv numbers
5. Deduplicate within each cohort × class combination (a phv may appear in multiple derivation blocks)

### Implementation sketch

```python
import re, yaml
from pathlib import Path
import pandas as pd

PHV_PATTERN = re.compile(r'\bphv\d{8}\b')

SKIP_FILES = {
    'participant.yaml', 'visit.yaml', 'person.yaml',
    'researchstudy.yaml', 'demography.yaml'
}

COHORT_MAP = {
    'ARIC-ingest': 'phs000280',
    'FHS-ingest': 'phs000007',
    'CARDIA-ingest': 'phs000285',
    'CHS-ingest': 'phs000287',
    'HCHS-ingest': 'phs000810',
    'JHS-ingest': 'phs000286',
    'COPDGene-ingest': 'phs000179',
    'MESA-ingest': 'phs000209',
    'WHI-ingest': 'phs000200',
}

def extract_phvs(yaml_path):
    with open(yaml_path) as f:
        raw = f.read()
    data = yaml.safe_load(raw)

    all_phvs = set(PHV_PATTERN.findall(raw))

    # Find phvs used only for participant/visit linkage
    excluded_phvs = set()
    for class_name, class_def in (data.get('class_derivations') or {}).items():
        for deriv_name, deriv_def in (class_def.get('slot_derivations') or {}).items():
            if deriv_name in ('associated_participant', 'associated_visit'):
                expr = str(deriv_def.get('expr', ''))
                excluded_phvs.update(PHV_PATTERN.findall(expr))

    bdchm_class = list((data.get('class_derivations') or {}).keys())[0] if data.get('class_derivations') else 'Unknown'
    return bdchm_class, all_phvs - excluded_phvs
```

### Output

Save to `data/phv_by_cohort_class.csv` with columns:
`cohort`, `phs`, `bdchm_class`, `variable_file`, `phv`

---

## Step 3: Classify Variables by Row Category

Map BDCHM class names to table row labels:

| BDCHM class | Table row |
|---|---|
| `Condition` | Conditions |
| `DrugExposure` | Drug Exposures |
| `Procedure` | Procedures |
| `SdohObservation` | SdohObservations |
| All others | Continuous variables |

Cross-reference against the Google Sheets variable list to verify all harmonized concepts are accounted for and no YAML files are miscategorized or missing.

---

## Step 4: Get Non-Missing Record Counts per phv (dbGAP Data Dictionaries)

Write `scripts/02_fetch_counts.py`.

### Approach: dbGAP public data dictionaries via FTP

dbGAP publishes `.data_dict.xml` files for each study at:
```
https://ftp.ncbi.nlm.nih.gov/dbgap/studies/phsXXXXXXX/phsXXXXXXX.vN.pM/
```

For each phs accession, download the data dictionary archive and parse the XML files. Each variable entry contains a `reported_values` section with value frequencies for categorical variables. Summing the frequencies of all non-missing codes gives the non-missing count for that phv.

### Notes and limitations

- Data dictionary coverage varies across studies — some phv variables may not have reported value frequencies
- Missing value codes differ by study (e.g., `.`, `99`, `999`, `"Missing"`) — these must be identified per variable and excluded
- A mapping from phv number to pht (phenotype table) and phs (study) is needed to locate the correct XML file; this mapping is available in the dbGAP data dictionaries themselves

### Output

Save to `data/phv_counts.csv` with columns:
`phv`, `phs`, `non_missing_count`

---

## Step 5: Build the Before Table

Write `scripts/03_build_table.py`.

1. Join `phv_by_cohort_class.csv` with `phv_counts.csv` on `phv` + `phs`
2. Group by `cohort` × `row_category` to compute:
   - `n_vars` = count of distinct phv numbers
   - `n_datapoints` = sum of `non_missing_count`
3. Pivot to wide format (one column pair per cohort)
4. Append a totals row (sum across all cohorts per class) and totals columns (sum across all classes per cohort)
5. Add a grand total cell (bottom-right)

---

## Step 6: Export to Excel

Use `pandas` with `openpyxl` to write a formatted Excel file:

```python
with pd.ExcelWriter('tables/before_table.xlsx', engine='openpyxl') as writer:
    df.to_excel(writer, sheet_name='Before', index=False)
    # Apply formatting: bold headers, bold totals row/column, number formatting
```

Output: `tables/before_table.xlsx`

---

## Suggested File Structure

```
BDC-Pilot-Tables/
├── WORKPLAN.md
├── scripts/
│   ├── 01_parse_yaml_phvs.py     # Step 2: extract phv numbers from YAML
│   ├── 02_fetch_counts.py        # Step 4: fetch non-missing counts from dbGAP
│   └── 03_build_table.py         # Step 5-6: assemble and export table
├── data/
│   ├── phv_by_cohort_class.csv   # output of step 2
│   └── phv_counts.csv            # output of step 4
└── tables/
    └── before_table.xlsx
```

---

## After Table

Design to be determined after the before table is finalized.
