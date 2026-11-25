"""健壮导入：任意 CSV/Excel/JSON/ZIP → 按表头内容识别五张表并映射列（不依赖文件名）。"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

from field_contract import TABLES
from pipeline import _resolve_columns

CANONICAL_NAME = {
    "课程要求": "课程要求.csv", "题目与材料": "题目与材料.csv", "模拟学生": "模拟学生.csv",
    "测验节点": "测验节点.csv", "作答记录": "作答记录.csv",
}


def sniff_rows(name: str, raw: bytes) -> Tuple[List[str], List[dict]]:
    lower = name.lower()
    if lower.endswith(".json"):
        data = json.loads(raw.decode("utf-8-sig"))
        rows = data if isinstance(data, list) else data.get("rows", [])
        rows = [r for r in rows if isinstance(r, dict)]
        header = list(rows[0].keys()) if rows else []
        return header, rows
    if lower.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        grid = [[("" if c is None else str(c)) for c in row] for row in ws.iter_rows(values_only=True)]
        wb.close()
        grid = [g for g in grid if any(x.strip() for x in g)]
        if not grid:
            return [], []
        header = [h.strip() for h in grid[0]]
        rows = [dict(zip(header, r)) for r in grid[1:]]
        return header, rows
    text = None
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError(f"无法解码 {name}")
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    rows = [{(k or "").strip(): (v or "") for k, v in r.items()} for r in reader]
    return header, rows


def expand_zip(name: str, raw: bytes) -> List[Tuple[str, bytes]]:
    out: List[Tuple[str, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            base = info.filename.split("/")[-1]
            if base.lower().endswith((".csv", ".xlsx", ".xlsm", ".json")) and not base.startswith("~$"):
                out.append((base, zf.read(info)))
    return out


def detect_table(header: List[str]) -> Tuple[str, float, Dict[str, str], List[str]]:
    best = ("", 0.0, {}, list(header))
    for table, (fields, _req, optional) in TABLES.items():
        required = [k for k in fields if k not in optional]
        matched = [k for k in required if any(a in header for a in fields[k])]
        score = len(matched) / len(required) if required else 0.0
        colmap, missing = _resolve_columns(header, fields, {})
        if score > best[1]:
            best = (table, score, colmap, missing)
    return best


def write_canonical(csv_dir: Path, table: str, rows: List[dict], colmap: Dict[str, str]) -> int:
    fields = TABLES[table][0]
    path = csv_dir / CANONICAL_NAME[table]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fields.keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(colmap.get(k, ""), "") for k in fields})
    return len(rows)