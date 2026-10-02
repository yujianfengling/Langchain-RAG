"""增量入库（add_documents_to_store）回归测试。

背景：`services/knowledge_expander.add_knowledge_to_kb` 一直在调用
`rag.vector_store.add_documents_to_store`，但该函数**从未被实现**。
ImportError 被调用方的 `except Exception` 静默吞掉，导致"提取的知识点入库"
功能长期完全失效且无任何报错、无任何测试覆盖。

本测试锁定该函数的行为，防止再次静默失效。
"""

import os
import pickle
import shutil
import tempfile
import unittest

from langchain_core.documents import Document

import rag.vector_store as vs
from rag.embedding import get_embedding_model


class TestAddDocumentsToStore(unittest.TestCase):
    """增量向既有索引追加文档"""

    @classmethod
    def setUpClass(cls):
        cls.emb = get_embedding_model()

    def setUp(self):
        # 把索引根目录重定向到临时目录，绝不触碰正式 data/
        self.tmp = tempfile.mkdtemp(prefix="vs_incremental_test_")
        self._orig_dir = vs.VECTOR_STORE_DIR
        vs.VECTOR_STORE_DIR = self.tmp
        vs.GlobalFAISS.clear_all()
        self.subject = "单测增量学科"
        self.safe = vs._safe_dir_name(self.subject)
        self.persist = os.path.join(self.tmp, self.safe)

    def tearDown(self):
        vs.GlobalFAISS.clear_all()
        vs.VECTOR_STORE_DIR = self._orig_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _doc(text, source="t.docx"):
        return Document(page_content=text, metadata={"source": source})

    def test_creates_index_when_missing(self):
        """索引不存在时应自动新建"""
        r = vs.add_documents_to_store([self._doc("二叉树的先序遍历是根左右。" * 6)],
                                      self.subject, self.emb)
        self.assertTrue(r["created"])
        self.assertEqual(r["added"], 1)
        self.assertEqual(r["total"], 1)

    def test_appends_to_existing_index(self):
        """追加到已有索引，总数应累加"""
        vs.add_documents_to_store([self._doc("先序遍历。" * 10)], self.subject, self.emb)
        r = vs.add_documents_to_store(
            [self._doc("哈夫曼树是带权路径最短的二叉树。" * 6),
             self._doc("快速排序平均 O(n log n)。" * 8)],
            self.subject, self.emb)
        self.assertFalse(r["created"])
        self.assertEqual(r["added"], 2)
        self.assertEqual(r["total"], 3)

    def test_appended_content_is_retrievable(self):
        """追加的内容必须能被检索到（否则等于没入库）"""
        vs.add_documents_to_store([self._doc("先序遍历。" * 10)], self.subject, self.emb)
        vs.add_documents_to_store([self._doc("哈夫曼树是带权路径最短的二叉树。" * 6)],
                                  self.subject, self.emb)

        store = vs.GlobalFAISS.get(self.safe, self.emb, self.persist)
        self.assertIsNotNone(store, "追加后应能取回索引")
        hits = store.similarity_search("哈夫曼树是什么", 3)
        blob = "\n".join(h.page_content for h in hits)
        self.assertIn("哈夫曼", blob, "追加的内容检索不到")

    def test_survives_reload(self):
        """落盘后重新加载（模拟进程重启）内容必须还在，且两文件一致"""
        vs.add_documents_to_store([self._doc("先序遍历。" * 10)], self.subject, self.emb)
        vs.add_documents_to_store([self._doc("哈夫曼树。" * 10)], self.subject, self.emb)
        expected = 2

        vs.GlobalFAISS.clear_all()
        store = vs.GlobalFAISS.get(self.safe, self.emb, self.persist)
        self.assertIsNotNone(store, "重启后应能加载索引")
        self.assertEqual(store.count(), expected)

        # meta 里的文档数必须与 pkl 同步，否则下次加载会被一致性校验拒绝
        meta = vs.read_index_meta(self.persist) or {}
        self.assertEqual(meta.get("documents"), expected)
        data = pickle.load(open(os.path.join(self.persist, "index.pkl"), "rb"))
        self.assertEqual(store.index.ntotal, len(data["documents"]))

    def test_invalidates_bm25_index(self):
        """追加后必须让 BM25 失效，否则两个索引条目数会不一致"""
        r = vs.add_documents_to_store([self._doc("先序遍历。" * 10)],
                                      self.subject, self.emb)
        self.assertTrue(r["bm25_invalidated"])

    def test_rejects_empty_inputs(self):
        with self.assertRaises(ValueError):
            vs.add_documents_to_store([], self.subject, self.emb)
        with self.assertRaises(ValueError):
            vs.add_documents_to_store([self._doc("x" * 100)], "", self.emb)

    def test_create_if_missing_false_raises(self):
        with self.assertRaises(FileNotFoundError):
            vs.add_documents_to_store([self._doc("x" * 100)],
                                      "不存在的学科A", self.emb,
                                      create_if_missing=False)


if __name__ == "__main__":
    unittest.main()
