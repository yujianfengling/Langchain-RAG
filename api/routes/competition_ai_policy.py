"""Outbound model policy for the competition workspace."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException

OFFICIAL_AI_NOTICE = (
    "官方赛题数据仅支持本地规则分析与出处核对。"
    "如需使用外部 AI，请通过「导入评测数据」切换至合成或自定义数据包。"
)


def _answer_keys(directory: Path) -> set[tuple[str, str, str, str]]:
    keys = set()
    for filename, record_key in (("answers.json", "sourceRecordId"), ("events.json", "recordId")):
        path = directory / filename
        if path.is_file():
            keys.update(
                (row[record_key], row["studentId"], row["quizId"], row["questionId"])
                for row in json.loads(path.read_text(encoding="utf-8")) if row.get(record_key)
            )
    return keys


def imported_data_allows_external_ai(directory: Path) -> bool:
    # Reimporting the competition CSVs must not turn official answers into custom data.
    from api.routes.competition import _DEFAULT_PRIVATE_DIR, _bundle_dir

    try:
        if (directory.parent / ".official-competition-data").exists() or not (directory / "answers.json").is_file():
            return False
        imported = _answer_keys(directory)
        reference_dirs = {_DEFAULT_PRIVATE_DIR.resolve(), _bundle_dir().resolve()}
        for reference in reference_dirs:
            manifest = reference / "manifest.json"
            if manifest.is_file() and json.loads(manifest.read_text(encoding="utf-8")).get("independentEvaluation"):
                continue
            if reference == directory.resolve() or imported & _answer_keys(reference):
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def require_external_ai(dataset: str | None) -> None:
    if dataset in (None, "", "official"):
        raise HTTPException(403, OFFICIAL_AI_NOTICE)

    from api.routes.competition import _dataset_bundle_dir

    directory = _dataset_bundle_dir(dataset)
    if not (directory / "manifest.json").is_file():
        raise HTTPException(404, "当前评测数据找不到，请重新导入或切换到可用数据。")
    if not imported_data_allows_external_ai(directory):
        raise HTTPException(403, OFFICIAL_AI_NOTICE)
