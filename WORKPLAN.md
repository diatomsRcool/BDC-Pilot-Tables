# Work Plan: Publication Tables for dbGAP Data Integration

## Overview

This project produces two publication tables describing the integration of data across long-term biomedical cohorts in dbGAP using the BDCHM data model and the LinkML ecosystem.

- **Before table**: Raw dbGAP variables per cohort, grouped by BDCHM class — **COMPLETE**
- **After table**: Harmonized output — design pending

Source transform specifications: https://github.com/RTIInternational/NHLBI-BDC-DMC-HV/tree/main/priority_variables_transform
Variable list: https://docs.google.com/spreadsheets/d/1PDaX266_H0haa0aabMYQ6UNtEKT5-ClMarP0FvNntN8/edit?gid=125829256#gid=125829256
BDCHM schema: https://github.com/RTIInternational/NHLBI-BDC-DMC-HM

---

## Cohort to phs Mapping (confirmed)

| Directory | Cohort | phs accession |
|---|---|---|
| ARIC-ingest | ARIC | phs000280 |
| CARDIA-ingest | CARDIA | phs000285 |
| CHS-ingest | CHS | phs000287 |
| COPDGene-ingest | COPDGene | phs000179 |
| FHS-ingest | FHS | phs000007 |
| HCHS-ingest | HCHS/SOL | phs000810 |
| JHS-ingest | JHS | phs000286 |
| MESA-ingest | MESA | phs000209 |
| WHI-ingest | WHI | phs000200 |

Note: LTRC-ingest and SPIROMICS-ingest exist in the repo but are not in scope for this publication.

---

## Before Table Design

### Row structure

| Section | Rows | How aggregated |
|---|---|---|
| Categorical | Conditions, Drug Exposures, Procedures, SdohObservations | Collapsed by BDCHM class |
| Continuous | One row per harmonized concept (98 concepts, alphabetical) | Per variable_file stem |
| Total | Grand total | Sum of all rows |

**Total: 103 rows** (4 categorical + 98 continuous + 1 total)

### Column structure

For each of the 9 cohorts, two columns:
1. **n vars**: number of unique phv numbers integrated from that cohort
2. **n data pts**: total non-missing records across those phv variables (sourced from dbGAP var_report.xml `n` attribute)

Final two columns: **Total n vars** and **Total n data pts** across all cohorts.

### Current output summary

| | Total n vars | Total n data pts |
|---|---|---|
| Conditions | 2,075 | 17,889,081 |
| Drug Exposures | 1,289 | 5,289,932 |
| Procedures | 73 | 1,487,492 |
| SdohObservations | 51 | 659,012 |
| Continuous (all concepts) | 3,796 | 27,134,337 |
| **Grand total** | **7,284** | **52,459,854** |

### Known limitations

- 21 ARIC phvs (across pht006419, pht006457, pht006480, pht006453, pht012502, pht012811, pht012855) have no public var_report entry and contribute n=0 to data point counts
- Continuous variable concept rows may share phvs across concepts (rare); the total row sums concept-level counts independently

---

## Step 1: Clone the Transform Repository ✓

```bash
git clone https://github.com/RTIInternational/NHLBI-BDC-DMC-HV.git
```

Cloned locally at `./NHLBI-BDC-DMC-HV`. Working directory: `priority_variables_transform/`

---

## Step 2: Parse YAML Files to Extract phv Numbers ✓

**Script**: `scripts/01_parse_yaml_phvs.py`
**Output**: `data/phv_by_cohort_class.csv` (columns: cohort, phs, bdchm_class, row_category, variable_file, pht, phv)

### Infrastructure files skipped

`participant.yaml`, `visit.yaml`, `person.yaml`, `researchstudy.yaml`, `research_study.yaml`, `demography.yaml`

### Key implementation details

- YAML files can be a list of blocks (`- class_derivations:`) or a single dict
- BDCHM class is the key under `class_derivations`
- phv numbers in `associated_participant` and `associated_visit` slots are excluded as linkage variables **only if they appear inside `uuid5()` calls** — phvs used in `case()` filter conditions within these slots may also be data variables and are kept
- Nested class_derivations (e.g., `Quantity` inside `MeasurementObservation`) are captured via recursive extraction

### BDCHM class → row category mapping

| BDCHM class | Row category |
|---|---|
| `Condition` | Conditions |
| `DrugExposure` | Drug Exposures |
| `Procedure` | Procedures |
| `SdohObservation` | SdohObservations |
| All others (e.g., `MeasurementObservation`, `MeasurementObservationSet`) | Continuous variables |

---

## Step 3: Fetch Non-Missing Record Counts from dbGAP ✓

**Script**: `scripts/02_fetch_counts.py`
**Output**: `data/phv_counts.csv` (columns: phs, pht, phv, n, count_available)

### Approach

- dbGAP `var_report.xml` files are downloaded from the NCBI FTP and cached in `data/var_report_cache/{phs}/`
- FTP base: `https://ftp.ncbi.nlm.nih.gov/dbgap/studies/{phs}/{phs}.vN.pM/pheno_variable_summaries/`
- The `n` attribute of `<stat>` inside `<total><stats>` gives the non-missing count directly
- Only var_report files for relevant pht tables are downloaded first; remaining files scanned as fallback for any phvs not yet found
- Consent-group sub-entries (e.g., `phv00100285.v1.p1.c1`) are skipped; only the base entry (`phv00100285.v1.p1`) is used to avoid overwriting the total count with a subset

### Results

- 7,446 / 7,467 phvs (99.7%) successfully matched
- 21 ARIC phvs not found in any public var_report (treated as n=0, flagged in output)

---

## Step 4: Build and Export the Before Table ✓

**Script**: `scripts/03_build_table.py`
**Output**: `tables/before_table.xlsx`

### Logic

1. Apply concept merges (synonym variable names collapsed to one canonical name — see below)
2. Categorical section: aggregate by `row_category` (cohort × class)
3. Continuous section: aggregate by `variable_file` concept (one row per concept per cohort)
4. Deduplicate phvs within each group before summing to avoid double-counting
5. Pivot to wide format; add total columns and total row
6. Export to Excel with formatting: dark blue header, green categorical rows, light blue total columns, light blue total row

### Concept merges (synonym pairs collapsed to canonical name)

| Merged away | Canonical |
|---|---|
| alcohol | alcohol_servings |
| basophil_ct | basophil_ncnc_bld |
| eosinophil_ct | eosinophil_ncnc_bld |
| fasting_blood_gluc | fast_gluc_bld |
| hrt_rt | hrtrt |
| insulin_in_blood | insulin_blood |
| lymphocyte_ct | lympho_ct |
| mn_art_press | mean_art_press |
| monocyte_ct | monocyte_ncnc_bld |
| neutro_ct | neutrophil_ct |
| rbc | rdbld_ct |

---

## File Structure

```
BDC-Pilot-Tables/
├── WORKPLAN.md
├── requirements.txt
├── NHLBI-BDC-DMC-HV/          # cloned transform repo (not committed)
├── scripts/
│   ├── 01_parse_yaml_phvs.py
│   ├── 02_fetch_counts.py
│   └── 03_build_table.py
├── data/
│   ├── phv_by_cohort_class.csv
│   ├── phv_counts.csv
│   └── var_report_cache/       # cached dbGAP XML files (not committed)
└── tables/
    └── before_table.xlsx
```

To regenerate the table from scratch:
```bash
python scripts/01_parse_yaml_phvs.py --repo ./NHLBI-BDC-DMC-HV
python scripts/02_fetch_counts.py
python scripts/03_build_table.py
```

---

## After Table

Design to be determined. Will be described by user after before table is finalized.
