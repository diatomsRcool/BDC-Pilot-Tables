"""
03_build_table.py

Join phv metadata with counts, aggregate into the before table,
and export to Excel.

Table structure:
  Rows:    Conditions | Drug Exposures | Procedures | SdohObservations |
           Continuous variables | Total
  Columns: For each cohort: "n vars" and "n data pts"
           Final two columns: Total n vars | Total n data pts

Usage:
    python scripts/03_build_table.py

Inputs:
    data/phv_by_cohort_class.csv   (from 01_parse_yaml_phvs.py)
    data/phv_counts.csv            (from 02_fetch_counts.py)

Output:
    tables/before_table.xlsx
"""

import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

PHV_CSV = Path("data/phv_by_cohort_class.csv")
COUNTS_CSV = Path("data/phv_counts.csv")
OUTPUT_XLSX = Path("tables/before_table.xlsx")

# Row display order
ROW_ORDER = [
    "Conditions",
    "Drug Exposures",
    "Procedures",
    "SdohObservations",
    "Continuous variables",
]

# Cohort column order (alphabetical)
COHORT_ORDER = ["ARIC", "CARDIA", "CHS", "COPDGene", "FHS", "HCHS", "JHS", "MESA", "WHI"]


# --------------------------------------------------------------------------
# Build the pivot table
# --------------------------------------------------------------------------

def build_table(phv_df: pd.DataFrame, counts_df: pd.DataFrame) -> pd.DataFrame:
    """
    Join phv metadata with counts, then pivot to the publication table format.
    """
    # Merge: left join so we keep all phvs even if count is missing
    merged = phv_df.merge(
        counts_df[["phv", "phs", "n", "count_available"]],
        on=["phv", "phs"],
        how="left",
    )
    merged["n"] = merged["n"].fillna(0).astype(int)
    merged["count_available"] = merged["count_available"].fillna(False)

    missing_counts = merged[~merged["count_available"]]
    if len(missing_counts) > 0:
        print(f"WARNING: {len(missing_counts)} phvs have no count (treated as 0):")
        print(missing_counts[["cohort", "row_category", "phv"]].to_string(index=False))

    # Aggregate: per cohort × row_category
    # n_vars = number of unique phv numbers
    # n_data_pts = sum of n across those phvs
    # Note: a phv may appear multiple times in merged (different variable_file /
    # pht for the same phv within one cohort). Deduplicate at the phv level
    # before summing to avoid double-counting data points.
    deduped = merged.drop_duplicates(subset=["cohort", "row_category", "phv"])

    agg = (
        deduped.groupby(["cohort", "row_category"])
        .agg(
            n_vars=("phv", "nunique"),
            n_data_pts=("n", "sum"),
        )
        .reset_index()
    )

    # Pivot to wide format: one column-pair per cohort
    wide_vars = agg.pivot(index="row_category", columns="cohort", values="n_vars").fillna(0).astype(int)
    wide_pts  = agg.pivot(index="row_category", columns="cohort", values="n_data_pts").fillna(0).astype(int)

    # Ensure all cohorts and row categories are present, even if zero
    wide_vars = wide_vars.reindex(index=ROW_ORDER, columns=COHORT_ORDER, fill_value=0)
    wide_pts  = wide_pts.reindex(index=ROW_ORDER, columns=COHORT_ORDER, fill_value=0)

    # Interleave cohort columns: ARIC n vars, ARIC n data pts, CARDIA n vars, ...
    col_tuples = []
    data_cols = {}
    for cohort in COHORT_ORDER:
        vars_col = f"{cohort}\nn vars"
        pts_col  = f"{cohort}\nn data pts"
        col_tuples.extend([vars_col, pts_col])
        data_cols[vars_col] = wide_vars[cohort]
        data_cols[pts_col]  = wide_pts[cohort]

    table = pd.DataFrame(data_cols, index=ROW_ORDER)

    # Total columns (sum across all cohorts per row)
    table["Total\nn vars"]    = wide_vars.sum(axis=1)
    table["Total\nn data pts"] = wide_pts.sum(axis=1)

    # Total row (sum across all row categories per column)
    totals = table.sum(axis=0).rename("Total")
    table = pd.concat([table, totals.to_frame().T])
    table.index.name = "Variable Type"
    table = table.reset_index()

    return table


# --------------------------------------------------------------------------
# Excel formatting
# --------------------------------------------------------------------------

HEADER_FILL   = PatternFill("solid", fgColor="2E4057")   # dark blue
TOTAL_FILL    = PatternFill("solid", fgColor="D6E4F0")   # light blue
SUBTOTAL_FILL = PatternFill("solid", fgColor="EBF5FB")   # very light blue

HEADER_FONT = Font(bold=True, color="FFFFFF", size=10)
BOLD_FONT   = Font(bold=True, size=10)
NORMAL_FONT = Font(size=10)

THIN_SIDE   = Side(style="thin", color="AAAAAA")
THIN_BORDER = Border(left=THIN_SIDE, right=THIN_SIDE, top=THIN_SIDE, bottom=THIN_SIDE)
THICK_SIDE  = Side(style="medium", color="666666")


def apply_formatting(ws, n_data_rows: int, n_cohort_cols: int):
    """Apply formatting to the worksheet after data is written."""

    max_row = ws.max_row
    max_col = ws.max_column

    # Row 1: header
    for col in range(1, max_col + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")

    # Data rows (rows 2 .. max_row - 1)
    for row in range(2, max_row):
        for col in range(1, max_col + 1):
            cell = ws.cell(row=row, column=col)
            cell.font = NORMAL_FONT
            cell.alignment = Alignment(horizontal="center" if col > 1 else "left",
                                       vertical="center")
            if col > 1:
                cell.number_format = "#,##0"

    # Totals row (last row)
    for col in range(1, max_col + 1):
        cell = ws.cell(row=max_row, column=col)
        cell.font = BOLD_FONT
        cell.fill = TOTAL_FILL
        cell.alignment = Alignment(horizontal="center" if col > 1 else "left",
                                   vertical="center")
        if col > 1:
            cell.number_format = "#,##0"

    # Total columns (last two columns)
    total_col_start = max_col - 1
    for row in range(2, max_row):  # excludes header, totals row already formatted
        for col in range(total_col_start, max_col + 1):
            cell = ws.cell(row=row, column=col)
            cell.font = BOLD_FONT
            cell.fill = SUBTOTAL_FILL
            cell.number_format = "#,##0"

    # Column widths
    ws.column_dimensions[get_column_letter(1)].width = 22  # Variable Type
    for col in range(2, max_col + 1):
        ws.column_dimensions[get_column_letter(col)].width = 13

    # Row heights
    ws.row_dimensions[1].height = 36  # header
    for row in range(2, max_row + 1):
        ws.row_dimensions[row].height = 18

    # Freeze panes: freeze the header row and Variable Type column
    ws.freeze_panes = "B2"


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    for path in [PHV_CSV, COUNTS_CSV]:
        if not path.exists():
            sys.exit(f"ERROR: {path} not found. Run earlier scripts first.")

    OUTPUT_XLSX.parent.mkdir(parents=True, exist_ok=True)

    phv_df    = pd.read_csv(PHV_CSV, dtype=str)
    counts_df = pd.read_csv(COUNTS_CSV, dtype={"n": int, "count_available": bool})

    print(f"Loaded {len(phv_df)} phv rows and {len(counts_df)} count rows")

    table = build_table(phv_df, counts_df)

    print("\nBefore table (preview):")
    print(table.to_string(index=False))

    # Write to Excel
    table.to_excel(OUTPUT_XLSX, sheet_name="Before", index=False)

    # Apply formatting
    wb = load_workbook(OUTPUT_XLSX)
    ws = wb["Before"]
    n_data_rows = len(ROW_ORDER)
    n_cohort_cols = len(COHORT_ORDER)
    apply_formatting(ws, n_data_rows, n_cohort_cols)
    wb.save(OUTPUT_XLSX)

    print(f"\nSaved formatted table to {OUTPUT_XLSX}")


if __name__ == "__main__":
    main()
