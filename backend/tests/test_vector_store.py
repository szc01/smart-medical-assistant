"""RAG 检索层测试：假 embedding + 临时 Chroma 目录，不连网络。"""

import hashlib
import math
import re

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage, HumanMessage

from app.rag import vector_store
from app.rag.context import (
    REFERENCE_FOOTER,
    REFERENCE_HEADER,
    REFERENCE_SECURITY_NOTE,
    build_reference_block,
    last_human_text,
)


class FakeEmbeddings(Embeddings):
    """把字符串 sha256 前 8 字节归一化成 8 维单位向量。"""

    def _embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()[:8]
        raw = [float(byte) for byte in digest]
        norm = math.sqrt(sum(value * value for value in raw)) or 1.0
        return [value / norm for value in raw]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


@pytest.fixture
def fake_embedding() -> FakeEmbeddings:
    return FakeEmbeddings()


@pytest.fixture(autouse=True)
def temp_chroma_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(vector_store, "CHROMA_ROOT", tmp_path / "chroma")


def test_build_reference_block_empty() -> None:
    assert build_reference_block([]) == ""


def test_build_reference_block_includes_source_and_security_note() -> None:
    block = build_reference_block(
        [
            {
                "filename": "体检报告.pdf",
                "page": 2,
                "score": 0.83,
                "content": "空腹血糖 6.2 mmol/L。",
            }
        ]
    )
    assert REFERENCE_HEADER in block
    assert REFERENCE_FOOTER in block
    assert REFERENCE_SECURITY_NOTE in block
    assert "《体检报告.pdf》第 2 页" in block
    assert "空腹血糖 6.2 mmol/L。" in block


def test_last_human_text_returns_latest_human_message() -> None:
    messages = [
        HumanMessage(content="第一轮问题"),
        AIMessage(content="第一轮回答"),
        HumanMessage(content="我上次体检的空腹血糖是多少"),
    ]
    assert last_human_text(messages) == "我上次体检的空腹血糖是多少"
    assert last_human_text([]) == ""


def test_search_returns_empty_for_unindexed_user(fake_embedding) -> None:
    assert (
        vector_store.search("user_empty", "血糖", embedding_function=fake_embedding)
        == []
    )
    assert not (
        vector_store.CHROMA_ROOT
        / vector_store._safe_collection_name("user_empty")
    ).exists()


def test_search_clamps_out_of_range_k(monkeypatch, fake_embedding) -> None:
    pages = [
        (1, "第一页：空腹血糖 6.2 mmol/L。"),
        (2, "第二页：血红蛋白 13.7 g/dL。"),
        (3, "第三页：ALT 58 U/L。"),
    ]
    indexed = vector_store.index_pages(
        "user_k",
        pages,
        "体检报告.pdf",
        embedding_function=fake_embedding,
    )
    assert indexed == 3

    monkeypatch.setattr(vector_store, "MAX_K", 2)
    assert (
        len(
            vector_store.search(
                "user_k", "血糖", k=999, embedding_function=fake_embedding
            )
        )
        == 2
    )
    assert (
        len(
            vector_store.search(
                "user_k", "血糖", k=0, embedding_function=fake_embedding
            )
        )
        == 1
    )


def test_safe_collection_name_avoids_collision() -> None:
    assert vector_store._safe_collection_name(
        "a b"
    ) != vector_store._safe_collection_name("a/b")
    for user_id in ("a b", "a/b", "中文用户", "x" * 200):
        name = vector_store._safe_collection_name(user_id)
        assert 3 <= len(name) <= 63
        assert re.fullmatch(r"[A-Za-z0-9._-]+", name)
