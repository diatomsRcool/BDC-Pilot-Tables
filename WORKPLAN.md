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
| Categorical | One row per variable_file within Conditions, Drug Exposures, Procedures (43 concept rows, each section alphabetical) | Per variable_file stem |
| Continuous | One row per harmonized concept (99 concepts, alphabetical) | Per variable_file stem |
| Total | Grand total | Sum of all rows |

**Total: 143 rows** (43 categorical concepts + 99 continuous concepts + 1 total)

### Column structure

For each of the 9 cohorts, two columns:
1. **n vars**: number of unique phv numbers in DATA_SLOTS for that cohort × concept
2. **n data pts**: total non-missing records for those phv variables (sourced from dbGAP var_report.xml `n` attribute)

Final two columns: **Total n vars** and **Total n data pts** across all cohorts.

### phv counting rule — DATA_SLOTS (final)

Only phv numbers found in these YAML slots are counted, for **both** n vars and n data pts:

```
condition_status, exposure_status, procedure_status,
value_string, value_quantity, value_boolean, value_enum
```

All other slots — including `drug_concept`, `condition_concept`, `procedure_concept`, `age_at_observation`, `associated_visit`, `observation_type`, etc. — are **metadata slots**. phvs found only in metadata slots are excluded entirely from the table. Script `01_parse_yaml_phvs.py` prints the full metadata slot list at the end of its output.

**Consequence**: Drug Exposures are zero for cohorts (ARIC, CHS, HCHS, JHS, WHI) whose medication YAML files store data only in `drug_concept`. This is intentional — those phvs describe the type of drug, not a measured value in a data slot.

### Current output summary (approximate)

| | Total n vars | Total n data pts |
|---|---|---|
| Conditions (43 concept rows) | ~1,865 | ~17,200,000 |
| Drug Exposures | ~469 | ~982,000 |
| Procedures | ~38 | ~487,000 |
| Continuous (99 concepts) | ~3,616 | ~24,300,000 |

### Row classification overrides (applied in 03_build_table.py)

- `edu_lvl` and `fam_income` are forced to continuous rows — some cohorts classify them as `SdohObservation`
- `pacem_stat` is forced to Procedures — some cohorts (ARIC, HCHS, MESA) use `MeasurementObservation` but WHI uses `Procedure`
- `chr_bronchitis`, `emphysema`, `hypert_trt`, `hist_cor_bypg` are excluded from the Conditions row

### Known limitations

- 19 ARIC phvs have no public var_report entry and contribute n=0 to data point counts
- Continuous variable concept rows may share phvs across concepts; the total row sums concept-level counts independently
- `SdohObservations` is a row_category in the phv CSV but is not shown in the table (not in `CATEGORICAL_SECTION_ORDER` and not classified as continuous)
- `spirometry.yaml` files use a `MeasurementObservationSet` structure and are parsed specially: sub-observations are mapped to `fev1`, `fvc`, or `fev1_fvc` by OMOP code (OMOP:4241837, OMOP:4176265, OMOP:3011505 respectively); other spirometry measurements (e.g., % predicted) are ignored

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
- phv extraction uses `find_data_slot_phvs()`: recursively walks the YAML structure and collects phvs only from slots whose name is in DATA_SLOTS; all other slots are ignored entirely
- This correctly handles nested structures (e.g., `value_quantity` inside `MeasurementObservationSet > observations > MeasurementObservation`) because the recursion descends through non-data-slot keys
- Script prints all metadata slot names encountered (slot names not in DATA_SLOTS) at the end of its output for transparency

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
2. Categorical section: aggregate by `variable_file` (one row per concept per cohort, grouped by section: Conditions → Drug Exposures → Procedures, each alphabetical)
3. Continuous section: aggregate by `variable_file` concept (one row per concept per cohort)
4. Deduplicate phvs within each group (per cohort × variable_file × phv) before summing to avoid double-counting across derivation blocks
5. Pivot to wide format; add total columns and total row
6. Export to Excel with formatting: dark blue header, green categorical rows, light blue total columns, light blue total row

### Concept merges (synonym pairs collapsed to canonical name)

| Merged away | Canonical |
|---|---|
| alcohol | alcohol_servings |
| basophil_ct | basophil_ncnc_bld |
| blood_clots | ven_thromb |
| chd | cvd |
| eosinophil_ct | eosinophil_ncnc_bld |
| fam_stroke | stroke |
| fasting_blood_gluc | fast_gluc_bld |
| hist_cor_angio (WHI) | — (no data phvs in DATA_SLOTS) |
| hist_coronary_bypass | hist_cor_bypg |
| hist_cvd | cvd |
| hist_heart_disease | cvd |
| hist_hrt_failure | hist_heart_failure |
| hist_hrtdis | cvd |
| hist_hrtfail | hist_heart_failure |
| hist_mi_inf | hist_mi |
| hrt_rt | hrtrt |
| hyperten | hypertension |
| insulin_in_blood | insulin_blood |
| lymphocyte_ct | lympho_ct |
| mn_art_press | mean_art_press |
| monocyte_ct | monocyte_ncnc_bld |
| neutro_ct | neutrophil_ct |
| rbc | rdbld_ct |
| stroke_isch_atk | stroke |
| taking_non_statin_medication | tak_nstat_med |
| valv_hrtdis | cvd |

---

## File Structure

```
BDC-Pilot-Tables/
├── WORKPLAN.md
├── requirements.txt
├── NHLBI-BDC-DMC-HV/               # cloned transform repo (not committed)
├── scripts/
│   ├── 01_parse_yaml_phvs.py        # parse YAMLs → phv_by_cohort_class.csv
│   ├── 02_fetch_counts.py           # fetch dbGAP counts → phv_counts.csv
│   ├── 03_build_table.py            # build before_table.xlsx
│   ├── 01b_parse_yaml_target_slots.py  # alternate parser (target slots only)
│   ├── 03b_build_target_table.py       # alternate table (target slots)
│   └── count_metadata_phvs.py       # count phvs in metadata-only slots per cohort
├── data/
│   ├── phv_by_cohort_class.csv
│   ├── phv_counts.csv
│   ├── phv_target_slots.csv         # output of 01b
│   ├── metadata_phv_counts.csv      # output of count_metadata_phvs.py
│   └── var_report_cache/            # cached dbGAP XML files (not committed)
└── tables/
    ├── before_table.xlsx
    └── before_table_target_slots.xlsx
```

To regenerate the before table from scratch:
```bash
python scripts/01_parse_yaml_phvs.py --repo ./NHLBI-BDC-DMC-HV
python scripts/02_fetch_counts.py
python scripts/03_build_table.py
```

---

## Quality Control

### Strategies (in suggested priority order)

| Check | Effort | What it catches |
|---|---|---|
| Spot-check phv lists against source YAMLs | Low | Parsing errors in 01_parse_yaml_phvs.py |
| Verify zero cells are truly zero | Low | Missing files / concepts |
| Check concept merge list for remaining synonyms | Low | Duplicate concept rows |
| Confirm row classification overrides are correct | Low | Misclassification |
| Cross-check n data pts against dbGAP directly | Medium | Count errors in 02_fetch_counts.py |

### 1. Spot-check phv lists against source YAMLs

Pick 2–3 cohorts × concept combinations and manually count phvs in the source YAML, then compare to the table. Focus on:
- A cohort with many phvs per concept (FHS has the most files)
- One categorical row and one continuous row per cohort

Completed spot-checks:
- ARIC FEV1: confirmed phvs match source YAML ✓
- CARDIA alcohol_servings: confirmed 23 phvs ✓
- CHS whtbld_ct: identified phv00100487 was in `age_at_observation` (metadata slot), correctly excluded under DATA_SLOTS rule ✓
- FHS ast_sgot: confirmed phv list matches source YAMLs ✓
- COPDGene edu_lvl: confirmed phv00568798 (visit-filter in `associated_visit`) correctly excluded under DATA_SLOTS rule ✓
- WHI slp_ap: n=6,806 is correct — phv00283514 is in `pht006223` (UNC Heart Failure sub-study, 42,283 total rows); consent-group sub-entries correctly skipped ✓

### 2. Verify zero cells are truly zero

The table has many cohort × concept cells with 0 vars. For a sample, confirm the source YAML for that cohort genuinely doesn't exist or doesn't contain that variable. Most suspicious zeros:
- ARIC `alcohol_servings` (0 vars) — does ARIC have no alcohol variable?
- FHS `alcohol_servings` (0 vars)
- Any cohort × concept where all neighbors in the same row have data

### 3. Check concept merge list for remaining synonyms

Scan all continuous variable_file names for pairs that look like the same concept with different names:

```bash
python3 -c "
import pandas as pd
df = pd.read_csv('data/phv_by_cohort_class.csv')
cont = df[df['row_category']=='Continuous variables']
print(sorted(cont['variable_file'].unique()))
"
```

### 4. Confirm row classification overrides

For each override in `FORCE_CONTINUOUS`, `FORCE_PROCEDURES`, and `EXCLUDE_FROM_CONDITIONS`, spot-check one cohort's YAML to confirm the classification decision is correct.

Current overrides to verify:
- `edu_lvl`, `fam_income` → continuous (some cohorts use SdohObservation)
- `pacem_stat` → Procedures (ARIC/HCHS/MESA use MeasurementObservation)
- `chr_bronchitis`, `emphysema`, `hypert_trt`, `hist_cor_bypg` → excluded from Conditions

### 5. Cross-check n data pts against dbGAP

Pick 5–10 phvs from `data/phv_counts.csv` and verify their `n` values against the dbGAP web interface or the raw var_report XML in `data/var_report_cache/`. The `n` attribute in `<stat>` inside `<total><stats>` of each var_report.xml is the non-missing count used.

---

## After Table

Design to be determined. Will be described by user after before table is finalized.
