import io
import json
import time
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import require_teacher
from api.routes import competition


TABLES = {
    "课程要求.csv": "indicator_id,theme_id,theme_name,requirement\nI01,T01,合成主题,练习计算\n",
    "题目与材料.csv": "item_id,theme_id,材料类型,题型,indicator_id,stem,full_score,source_location\n"
    "Q001,T01,题目,填空题,I01,计算2+2,1,合成样例\n",
    "模拟学生.csv": "student_id,class_id\nSYN1,SC01\n",
    "测验节点.csv": "node_id,theme_ids,date,available_items\nN01,T01,2026-01-01,Q001\n",
    "作答记录.csv": "record_id,event_id,version,student_id,item_id,node_id,score,full_score,status,学生最终作答,解答过程,rubric_scores\n"
    "SYN-R,SYN-E,1,SYN1,Q001,N01,1,1,已评分,4,,\n",
}


class LegacyChinesePath(zipfile.ZipInfo):
    def _encodeFilenameFlags(self):
        return self.filename.encode("gb18030"), self.flag_bits


def pack(items, legacy=False):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in items.items():
            archive.writestr(LegacyChinesePath(name) if legacy else name, body,
                             compress_type=zipfile.ZIP_DEFLATED)
    return raw.getvalue()


@pytest.fixture()
def isolated_import(monkeypatch):
    import llm

    with TemporaryDirectory(prefix="competition-zip-test-") as temp:
        monkeypatch.setenv("COMPETITION_IMPORT_ROOT", temp)
        mock = Mock(side_effect=AssertionError("Local import must never call an external model"))
        monkeypatch.setattr(llm, "get_llm", mock)
        app = FastAPI()
        app.include_router(competition.router, prefix="/api")
        app.dependency_overrides[require_teacher] = lambda: "zip-test"
        with TestClient(app) as client:
            yield client, Path(temp)
        mock.assert_not_called()


@pytest.mark.parametrize("legacy", [False, True])
def test_zip_with_chinese_names_runs_only_the_five_tables(isolated_import, legacy):
    client, root = isolated_import
    prefix = "赛题数据/赛题资料包" if legacy else "合成赛题资料"
    folder = f"{prefix}/模拟学情数据/"
    content = pack({**{folder + name: rows for name, rows in TABLES.items()},
                    folder + "字段说明.csv": "仅供参考",
                    f"{prefix}/数据索引.xlsx": b"unrelated",
                    f"{prefix}/教材切片.pdf": b"never processed"}, legacy=legacy)
    response = client.post("/api/competition/import-unified",
                           files={"files": ("【临时合成数据】学情核验.zip", content, "application/zip")},
                           data={"ai_assist": "1"})
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    for _ in range(200):
        job = client.get(f"/api/competition/import-job/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.025)
    assert job["status"] == "succeeded", job
    assert {item["name"] for item in job["items"]} == set(TABLES)
    assert all(item["origin"] == "【临时合成数据】学情核验.zip" for item in job["items"])
    manifest = json.loads((root / job["eval_id"] / "bundle" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["students"] == manifest["counts"]["raw_answer_rows"] == 1
    permissions = client.get("/api/competition/eval-bundle", params={"id": job["eval_id"]})
    assert permissions.status_code == 200
    assert permissions.json()["external_ai_allowed"] is not legacy


def test_zip_rejects_corrupt_archive_and_oversized_expanded_member(isolated_import):
    client, _ = isolated_import
    for content in (b"not a zip", pack({"作答记录.csv": b"0" * (30 * 1024 * 1024 + 1)})):
        response = client.post("/api/competition/import-unified",
                               files={"files": ("临时夹具.zip", content, "application/zip")})
        assert response.status_code == 400, response.text
