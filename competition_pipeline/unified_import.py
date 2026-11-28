"""本地识别评测五表，支持多格式导入、分批合并和异步复算。"""
from __future__ import annotations

import csv
import io
import json
import re
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

import flex_import as flex
from field_contract import TABLES

ACCEPT_EXTS = (".csv", ".xlsx", ".xlsm", ".xls", ".json", ".zip", ".txt", ".md")
TABLE_NAMES = list(TABLES.keys())
OFFICIAL_IMPORT_MARKER = ".official-competition-data"
_LOCK = threading.Lock()


def expand_any(name: str, raw: bytes) -> List[Tuple[str, bytes]]:
    return unpack_upload(name, raw)[0]


def unpack_upload(name: str, raw: bytes) -> Tuple[List[Tuple[str, bytes]], bool]:
    if name.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            entries = []
            for info in zf.infolist():
                member = info.filename
                if not info.flag_bits & 0x800:
                    try:
                        member = member.encode("cp437").decode("gb18030")
                    except UnicodeError:
                        pass
                base = member.replace("\\", "/").rsplit("/", 1)[-1]
                if info.is_dir() or base.startswith("~$") or "__MACOSX" in member:
                    continue
                if base.lower().endswith(ACCEPT_EXTS):
                    entries.append((member, base, info))
            # 赛方整包还含规则、教材和 Excel 索引。统计只读取五份原始 CSV。
            official = [(base, info) for member, base, info in entries
                        if "/模拟学情数据/" in member.replace("\\", "/")
                        and base in flex.CANONICAL_NAME.values()]
            selected = official if len(official) == 5 and len({base for base, _ in official}) == 5 else [
                (base, info) for _, base, info in entries
            ]
            limit = 30 * 1024 * 1024
            if len(selected) > 200 or any(info.file_size > limit for _, info in selected) or sum(
                info.file_size for _, info in selected
            ) > 100 * 1024 * 1024:
                raise ValueError("压缩包中可导入文件过多或过大，请分批导入")
            if not selected:
                raise ValueError("压缩包内没有可导入的文件")
            if any(info.flag_bits & 1 for _, info in selected):
                raise ValueError("暂不支持加密压缩包，请解密后重新导入")
            protected = any("/赛题资料包/" in "/" + member.replace("\\", "/") for member, _, _ in entries)
            return [(base, zf.read(info)) for base, info in selected], protected
    return [(name, raw)], False


def _md_table(text: str) -> Tuple[List[str], List[dict]]:
    lines = [l.strip() for l in text.splitlines() if l.strip().startswith("|")]
    grid = [[c.strip() for c in l.strip("|").split("|")] for l in lines]
    grid = [g for g in grid if not all(re.fullmatch(r":?-{2,}:?", c or "---") for c in g)]
    if len(grid) < 2:
        return [], []
    header = grid[0]
    return header, [dict(zip(header, r)) for r in grid[1:]]


def sniff_any(name: str, raw: bytes) -> Tuple[List[str], List[dict]]:
    lower = name.lower()
    if lower.endswith(".xls"):
        import xlrd
        book = xlrd.open_workbook(file_contents=raw)
        sheet = book.sheet_by_index(0)
        grid = [[str(sheet.cell_value(r, c)) for c in range(sheet.ncols)] for r in range(sheet.nrows)]
        grid = [g for g in grid if any(x.strip() for x in g)]
        if not grid:
            return [], []
        header = [h.strip() for h in grid[0]]
        return header, [dict(zip(header, r)) for r in grid[1:]]
    if lower.endswith((".txt", ".md")):
        text = raw.decode("utf-8-sig", errors="replace")
        header, rows = _md_table(text)
        if header:
            return header, rows
        return flex.sniff_rows(name + ".csv", raw)
    return flex.sniff_rows(name, raw)


def merge_append(csv_dir: Path, table: str, rows: List[dict], colmap: Dict[str, str]) -> int:
    fields = TABLES[table][0]
    path = csv_dir / flex.CANONICAL_NAME[table]
    old: List[dict] = []
    if path.is_file():
        with path.open(encoding="utf-8-sig", newline="") as f:
            old = list(csv.DictReader(f))
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fields.keys()))
        writer.writeheader()
        for r in old:
            writer.writerow({k: r.get(k, "") for k in fields})
        for r in rows:
            writer.writerow({k: r.get(colmap.get(k, ""), "") for k in fields})
    return len(rows)


def _jobs_dir(imports_root: Path) -> Path:
    d = imports_root / "_jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_job(imports_root: Path, job: dict) -> None:
    with _LOCK:
        (_jobs_dir(imports_root) / f"{job['id']}.json").write_text(
            json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")


def load_job(imports_root: Path, job_id: str) -> dict | None:
    with _LOCK:
        path = _jobs_dir(imports_root) / f"{job_id}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None


def list_jobs(imports_root: Path, limit: int = 10) -> List[dict]:
    files = sorted(_jobs_dir(imports_root).glob("job-*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out = []
    for p in files:
        j = load_job(imports_root, p.stem)
        if j is None:
            continue
        out.append({k: j.get(k) for k in ("id", "session", "status", "eval_id", "counts", "created_at", "error")}
                   | {"items_total": len(j.get("items", [])),
                      "items_ready": sum(1 for i in j.get("items", []) if i["status"] == "ready"),
                      "items_failed": sum(1 for i in j.get("items", []) if i["status"] == "failed")})
    return out


def _present(csv_dir: Path) -> List[str]:
    return [t for t in TABLE_NAMES if (csv_dir / flex.CANONICAL_NAME[t]).is_file()]


def _process_member(item: dict, name: str, raw: bytes, csv_dir: Path) -> None:
    item["status"] = "running"
    item["stage"] = "parse"
    started = time.time()
    try:
        header, rows = sniff_any(name, raw)
        if not header or not rows:
            raise ValueError("空表或无表头")
        item["stage"] = "recognize"
        table, score, colmap, missing = flex.detect_table(header)
        item["missing"] = missing
        if not table or score < 0.4:
            raise ValueError("表头无法匹配，请按评测模板整理后重试")
        item["stage"] = "merge"
        n = merge_append(csv_dir, table, rows, colmap)
        item.update(status="ready", table=table, score=round(score, 2), rows=n, error=None)
    except Exception as exc:  # 逐文件隔离：一个坏文件不拖垮整批
        item.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:300])
    item["ms"] = int((time.time() - started) * 1000)


def _finalize(job: dict, imports_root: Path, csv_dir: Path) -> None:
    job["present"] = _present(csv_dir)
    if len(job["present"]) == 5 and not job.get("eval_id"):
        job["stage"] = "pipeline"
        _save_job(imports_root, job)
        try:
            import import_dataset as importer
            out = imports_root / f"eval-{uuid.uuid4().hex[:8]}"
            importer.import_dataset(csv_dir, out)
            if (csv_dir.parent / OFFICIAL_IMPORT_MARKER).exists():
                (out / OFFICIAL_IMPORT_MARKER).touch()
            manifest = json.loads((out / "bundle" / "manifest.json").read_text(encoding="utf-8"))
            job["eval_id"] = out.name
            job["counts"] = manifest.get("counts", {})
            job["status"] = "succeeded"
        except Exception as exc:
            job["status"] = "partial"
            job["error"] = f"五表已齐但管线复算失败：{exc}"[:300]
    else:
        failed = [i for i in job["items"] if i["status"] == "failed"]
        job["status"] = "partial" if (failed or len(job["present"]) < 5) else "succeeded"
        if len(job["present"]) < 5:
            job["error"] = "还缺表：" + "、".join(t for t in TABLE_NAMES if t not in job["present"])
    job["stage"] = "done"
    _save_job(imports_root, job)


def _run_job(job: dict, imports_root: Path, members: List[Tuple[str, bytes, str]]) -> None:
    session_dir = imports_root / job["session"]
    csv_dir = session_dir / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)
    for name, raw, item_id in members:
        item = next(i for i in job["items"] if i["item_id"] == item_id)
        _process_member(item, name, raw, csv_dir)
        job["present"] = _present(csv_dir)
        _save_job(imports_root, job)
    _finalize(job, imports_root, csv_dir)


def create_job(imports_root: Path, session: str, files: List[Tuple[str, bytes]]) -> dict:
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    raw_dir = imports_root / session / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    items, members = [], []
    for name, raw in files:
        expanded, protected = unpack_upload(name, raw)
        if protected:
            (raw_dir.parent / OFFICIAL_IMPORT_MARKER).touch()
        for mname, mraw in expanded:
            item_id = f"itm_{uuid.uuid4().hex[:8]}"
            safe = re.sub(r"[^\w.\-]", "_", mname)[:80]
            (raw_dir / f"{item_id}__{safe}").write_bytes(mraw)
            items.append({"item_id": item_id, "name": mname, "origin": name,
                          "status": "pending", "stage": "", "table": None,
                          "score": None, "rows": None, "error": None, "ms": 0})
            members.append((mname, mraw, item_id))
    job = {"id": job_id, "session": session, "status": "running", "stage": "parse",
           "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
           "items": items, "present": _present(imports_root / session / "csv"),
           "eval_id": None, "counts": {}, "error": None}
    _save_job(imports_root, job)
    threading.Thread(target=_run_job, args=(job, imports_root, members),
                     daemon=True, name=f"import-{job_id}").start()
    return job


def retry_job(imports_root: Path, job_id: str) -> dict | None:
    job = load_job(imports_root, job_id)
    if not job:
        return None
    if job["status"] == "running":
        return job
    raw_dir = imports_root / job["session"] / "raw"
    members = []
    for item in job["items"]:
        if item["status"] != "failed":
            continue
        matches = list(raw_dir.glob(f"{item['item_id']}__*"))
        if not matches:
            item["error"] = "原始文件已清理，无法重试"
            continue
        item.update(status="pending", error=None)
        members.append((item["name"], matches[0].read_bytes(), item["item_id"]))
    if not members:
        return job
    job["status"] = "running"
    job["error"] = None
    _save_job(imports_root, job)
    threading.Thread(target=_run_job, args=(job, imports_root, members),
                     daemon=True, name=f"retry-{job_id}").start()
    return job
