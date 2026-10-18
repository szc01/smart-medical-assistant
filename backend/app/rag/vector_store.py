"""按用户隔离的病历向量存储（Chroma）。"""
import hashlib
import logging
import re
from pathlib import Path

from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config.settings import settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500
CHUNK_OVERLAP = 80
MAX_K = 10
CHROMA_ROOT: Path = Path(__file__).resolve().parents[2] / "storage" / "chroma"

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 中文文本没有空格，分隔符必须包含中文标点，否则长段落无法在句末切开
_SPLITTER_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]


def get_embeddings() -> Embeddings:
    return OpenAIEmbeddings(
        model="text-embedding-v3",
        api_key=settings.DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
        # 百炼兼容端点不认 tiktoken 编码后的 token 数组，必须关闭
        check_embedding_ctx_length=False,
        # 百炼单次请求最多 10 条文本
        chunk_size=10,
    )


def _safe_collection_name(user_id: str) -> str:
    raw = str(user_id)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_-")
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    if len(slug) < 3:
        slug = f"user_{slug}" if slug else "user"
    slug = slug[:52].strip("_-") or "user"
    return f"{slug}_{digest}"


def get_vector_store(user_id: str, embedding_function=None) -> Chroma:
    name = _safe_collection_name(user_id)
    return Chroma(
        collection_name=name,
        embedding_function=embedding_function or get_embeddings(),
        persist_directory=str(CHROMA_ROOT / name),
        collection_metadata={"hnsw:space": "cosine"},
        client_settings=ChromaSettings(anonymized_telemetry=False),
    )


def _chunk_id(user_id: str, filename: str, page: int, chunk_index: int, content: str) -> str:
    raw = f"{user_id}|{filename}|{page}|{chunk_index}|{content[:120]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def index_pages(
    user_id: str,
    pages: list[tuple[int, str]],
    filename: str,
    embedding_function=None,
) -> int:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=_SPLITTER_SEPARATORS,
    )

    documents: list[Document] = []
    ids: list[str] = []
    chunk_index = 0

    for page_number, text in pages:
        if not text or not text.strip():
            continue
        for chunk in splitter.split_text(text):
            if not chunk.strip():
                continue
            documents.append(
                Document(
                    page_content=chunk,
                    metadata={
                        "user_id": user_id,
                        "filename": filename,
                        "page": page_number,
                        "chunk_index": chunk_index,
                    },
                )
            )
            ids.append(_chunk_id(user_id, filename, page_number, chunk_index, chunk))
            chunk_index += 1

    if not documents:
        return 0

    store = get_vector_store(user_id, embedding_function=embedding_function)
    store.add_documents(documents, ids=ids)
    return len(documents)


def index_document(
    user_id: str,
    file_path: str,
    filename: str,
    embedding_function=None,
) -> int:
    loader = PyPDFLoader(file_path)
    pages = []
    for document in loader.load():
        page_number = int(document.metadata.get("page", 0)) + 1
        pages.append((page_number, document.page_content))
    return index_pages(
        user_id,
        pages,
        filename,
        embedding_function=embedding_function,
    )


def search(
    user_id: str,
    query: str,
    k: int = 4,
    embedding_function=None,
) -> list[dict]:
    if not query or not query.strip():
        return []

    try:
        persist_dir = CHROMA_ROOT / _safe_collection_name(user_id)
        if not persist_dir.exists():
            return []

        k = max(1, min(int(k), MAX_K))
        store = get_vector_store(user_id, embedding_function=embedding_function)
        results = store.similarity_search_with_score(query, k=k)
        return [
            {
                "content": document.page_content,
                "filename": document.metadata.get("filename", ""),
                "page": document.metadata.get("page", 0),
                "score": round(1 - float(distance), 4),
            }
            for document, distance in results
        ]
    except Exception as exc:
        logger.warning("向量检索失败（user_id=%s）：%s", user_id, exc)
        return []
