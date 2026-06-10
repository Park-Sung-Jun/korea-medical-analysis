import os
import sys
import re
import json
import time
import requests
import pandas as pd

# Set output stream to UTF-8
sys.stdout.reconfigure(encoding='utf-8')

# Relative paths for portability inside isochrone_map
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TREE_PATH = os.path.join(SCRIPT_DIR, "kosis_tree.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "data")

os.makedirs(OUTPUT_DIR, exist_ok=True)

def load_api_key():
    """Reads the KOSIS API Key from local and global environment files."""
    local_env = os.path.join(SCRIPT_DIR, "..", ".env")
    global_env = "C:/Users/user/.claude/.env"

    if os.environ.get("KOSIS_API_KEY"):
        return os.environ["KOSIS_API_KEY"]
    for p in (local_env, global_env):
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("KOSIS_API_KEY"):
                        _, _, val = line.strip().partition("=")
                        return val.strip().strip('"').strip("'")
    raise SystemExit("KOSIS_API_KEY를 찾을 수 없습니다. .env에 KOSIS_API_KEY=... 를 추가하세요.")

API_KEY = load_api_key()
URL_META = "https://kosis.kr/openapi/statisticsData.do"
URL_DATA = "https://kosis.kr/openapi/Param/statisticsParameterData.do"

def parse_kosis_json(text):
    """Parses KOSIS non-strict JSON format safely."""
    # Matches unquoted keys at beginning of objects or after commas
    quoted = re.sub(r'(?<=[\{,])\s*([a-zA-Z0-9_]+)\s*:', r'"\1":', text)
    return json.loads(quoted)

def get_all_tables_from_tree(nodes):
    """Recursively extracts all table nodes from the tree structure."""
    tbls = []
    for n in nodes:
        if n["type"] == "table":
            tbls.append(n)
        elif n["type"] == "folder" and "children" in n:
            tbls.extend(get_all_tables_from_tree(n["children"]))
    return tbls

def get_classifications(tbl_id):
    """Queries ITM metadata to find unique classification levels (objL1, objL2, etc.)."""
    params = {
        "method": "getMeta",
        "type": "ITM",
        "apiKey": API_KEY,
        "orgId": "350",
        "tblId": tbl_id,
        "format": "json"
    }
    for attempt in range(3):
        try:
            response = requests.get(URL_META, params=params, timeout=15)
            if response.status_code == 200:
                text = response.content.decode("utf-8", errors="replace")
                data = parse_kosis_json(text)

                # Check for error dict
                if isinstance(data, dict) and "err" in data:
                    print(f"  [WARN] Metadata query returned error: {data}")
                    return [], 0

                # Count elements in each group to estimate cells
                counts = {}
                for item in data:
                    sn = item.get("OBJ_ID_SN")
                    if sn is None or sn == "" or sn == "None":
                        sn = "METRICS"
                    counts[sn] = counts.get(sn, 0) + 1

                # Compute total combinations (cells per year)
                combinations = 1
                for sn, count in counts.items():
                    combinations *= count

                # Find unique classification levels (excluding main METRICS columns)
                sns = sorted(list(set(int(k) for k in counts.keys() if k != "METRICS")))
                return sns, combinations
        except Exception as e:
            print(f"  [RETRY] Metadata query failed (attempt {attempt+1}): {e}")
            time.sleep(1)
    return [], 0

def fetch_data_query(tbl_id, classifications, start_year=None, end_year=None, num_years=None):
    """Queries KOSIS data for a given range of years or count."""
    params = {
        "method": "getList",
        "apiKey": API_KEY,
        "orgId": "350",
        "tblId": tbl_id,
        "format": "json",
        "prdSe": "Y",
        "itmId": "ALL"
    }

    # Configure periods
    if start_year and end_year:
        params["startPrdDe"] = str(start_year)
        params["endPrdDe"] = str(end_year)
    elif num_years:
        params["newEstPrdCnt"] = str(num_years)

    # Add classification parameters
    for idx, sn in enumerate(classifications):
        params[f"objL{idx+1}"] = "ALL"

    for attempt in range(3):
        try:
            response = requests.get(URL_DATA, params=params, timeout=30)
            if response.status_code == 200:
                text = response.content.decode("utf-8", errors="replace")
                data = parse_kosis_json(text)

                # Handle error responses
                if isinstance(data, dict) and "err" in data:
                    err_code = data.get("err")
                    err_msg = data.get("errMsg", "")
                    if err_code == "30": # Data does not exist
                        return []
                    print(f"  [WARN] Data query returned error {err_code}: {err_msg}")
                    return None

                return data
            else:
                print(f"  [WARN] HTTP {response.status_code} on data query")
        except Exception as e:
            print(f"  [RETRY] Data query failed (attempt {attempt+1}): {e}")
            time.sleep(2)
    return None

def download_table(tbl_id, tbl_name):
    """Downloads the latest 10 years of data for a specific table."""
    print(f"\nProcessing Table: {tbl_id} ({tbl_name})")

    # 1. Get classifications and cell size estimate
    classifications, combinations = get_classifications(tbl_id)
    if not classifications:
        print(f"  [ERROR] No classifications found for {tbl_id}. Skipping.")
        return None

    print(f"  Classifications levels count: {len(classifications)}. Combinations per year: {combinations}")

    # 2. Query the latest year first to establish the baseline
    latest_data = fetch_data_query(tbl_id, classifications, num_years=1)
    if not latest_data or len(latest_data) == 0:
        print(f"  [WARN] No baseline data found for {tbl_id}. Skipping.")
        return None

    # Get latest year from the baseline
    latest_year_str = latest_data[0].get("PRD_DE")
    if not latest_year_str:
        print("  [WARN] Missing year field in data. Skipping.")
        return None

    try:
        latest_year = int(latest_year_str)
    except ValueError:
        print(f"  [WARN] Invalid year field '{latest_year_str}'. Skipping.")
        return None

    target_years = sorted(list(range(latest_year - 9, latest_year + 1)), reverse=True)
    print(f"  Latest year: {latest_year}. Target download years: {target_years}")

    all_rows = []

    # 3. Determine if we fetch all-at-once or year-by-year
    estimated_total_cells = combinations * 10
    if estimated_total_cells < 30000:
        # Fetch all 10 years in one single request
        print("  [OPTIMIZATION] Table is small. Fetching all 10 years in a single request...")
        data = fetch_data_query(tbl_id, classifications, start_year=target_years[-1], end_year=target_years[0])
        if data:
            all_rows.extend(data)
        time.sleep(0.3)
    else:
        # Fetch year-by-year to stay under cell limits
        print("  [INFO] Table is large. Fetching year-by-year...")
        for y in target_years:
            print(f"    Fetching year {y}...")
            data = fetch_data_query(tbl_id, classifications, start_year=y, end_year=y)
            if data:
                print(f"      Downloaded {len(data)} rows.")
                all_rows.extend(data)
            else:
                print(f"      No data or skipped for year {y}.")
            time.sleep(0.4) # Respectful delay

    if not all_rows:
        print(f"  [WARN] No historical data retrieved for {tbl_id}.")
        return None

    # 4. Save results
    csv_path = os.path.join(OUTPUT_DIR, f"{tbl_id}.csv")
    json_path = os.path.join(OUTPUT_DIR, f"{tbl_id}.json")

    # Save JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, indent=2, ensure_ascii=False)

    # Save CSV
    df = pd.DataFrame(all_rows)
    # Reorder columns to put keys first
    cols = list(df.columns)
    primary_cols = ["TBL_ID", "PRD_DE", "ITM_NM", "DT", "UNIT_NM"]
    for c in reversed(primary_cols):
        if c in cols:
            cols.remove(c)
            cols.insert(0, c)
    df = df[cols]
    # Sort by year descending
    if "PRD_DE" in df.columns:
        df = df.sort_values(by=["PRD_DE"], ascending=False)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    print(f"  [SUCCESS] Saved {len(all_rows)} rows to {tbl_id}.csv and .json")
    return {
        "id": tbl_id,
        "name": tbl_name,
        "rows": len(all_rows),
        "years": sorted(list(set(row.get("PRD_DE") for row in all_rows)), reverse=True)
    }

def main():
    print("=" * 60)
    print("KOSIS STATISTICAL DATA DOWNLOADER")
    print(f"Destination: {OUTPUT_DIR}")
    print("=" * 60)

    # Load tree
    if not os.path.exists(TREE_PATH):
        print(f"[FATAL] Mapped tree file not found at {TREE_PATH}. Please make sure kosis_tree.json is in the same directory.")
        sys.exit(1)

    with open(TREE_PATH, "r", encoding="utf-8") as f:
        tree = json.load(f)

    all_tables = get_all_tables_from_tree(tree)
    total_tables = len(all_tables)
    print(f"Found {total_tables} tables in the tree structure.")

    results = []
    success_count = 0
    fail_count = 0

    start_time = time.time()

    for i, t in enumerate(all_tables):
        tbl_id = t["id"]
        tbl_name = t["name"]

        print(f"\n[{i+1}/{total_tables}]", end="")
        res = download_table(tbl_id, tbl_name)
        if res:
            results.append(res)
            success_count += 1
        else:
            fail_count += 1

        # Checkpoint: Save summary markdown after each table in case of interruption
        write_summary_report(results, success_count, fail_count, total_tables, time.time() - start_time)

    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print("DOWNLOAD TASK COMPLETE")
    print(f"Success: {success_count} tables")
    print(f"Failed/Skipped: {fail_count} tables")
    print(f"Total Time: {elapsed:.2f} seconds ({elapsed/60:.2f} minutes)")
    print("=" * 60)

def write_summary_report(results, success_count, fail_count, total_tables, elapsed_time):
    """Writes a summary README report of the downloaded datasets."""
    report_path = os.path.join(OUTPUT_DIR, "README.md")

    content = []
    content.append("# KOSIS Health Examination Statistics Download Summary\n")
    content.append("Downloaded statistical tables from the National Health Insurance Service (국민건강보험공단 - 건강검진통계).\n")
    content.append("## Progress Status\n")
    content.append(f"- **Total Target Tables**: {total_tables}")
    content.append(f"- **Successfully Downloaded**: {success_count}")
    content.append(f"- **Failed or Empty**: {fail_count}")
    content.append(f"- **Elapsed Time**: {elapsed_time/60:.2f} minutes")
    content.append(f"- **Last Updated**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    content.append("## Downloaded Tables List\n")
    content.append("| # | Table ID | Table Name | Rows Count | Years Fetched | Files Created |")
    content.append("|---|----------|------------|------------|---------------|---------------|")

    for idx, r in enumerate(results):
        years_str = ", ".join(r["years"][:5])
        if len(r["years"]) > 5:
            years_str += f" (+{len(r['years'])-5} more)"

        # 백슬래시는 f-string 밖에서 치환(py3.11 호환 — f-string 내 백슬래시는 3.12+ 문법)
        out_dir_uri = OUTPUT_DIR.replace("\\", "/")
        csv_link = f"[{r['id']}.csv](file:///{out_dir_uri}/{r['id']}.csv)"
        json_link = f"[{r['id']}.json](file:///{out_dir_uri}/{r['id']}.json)"

        content.append(f"| {idx+1} | `{r['id']}` | {r['name']} | {r['rows']:,} | {years_str} | {csv_link}, {json_link} |")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(content))

if __name__ == "__main__":
    main()
