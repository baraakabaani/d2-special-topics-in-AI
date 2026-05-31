"""
FastAPI application — D2 endpoints.
done by Baraa

Routes:
  POST /ingest   — trigger PDF ingestion pipeline (calls ingest.py)
  POST /search   — hybrid BM25+dense search (calls vector_store.py)
  POST /feedback — record user feedback (stub, wired in D3)
  GET  /stats    — collection counts across all stores
"""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Docker Compose injects env vars directly into the container — no .env file needed here.
# For running api.py locally (outside Docker), export vars manually or use .env.local.

_searcher = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _searcher
    from src.vector_store import HybridSearch
    _searcher = HybridSearch()   # BM25 built once at startup, reused for every request
    yield

app = FastAPI(title="CSAI415 D2 — Retrieval Stack", version="0.2.0", lifespan=lifespan)


# ── request / response models ──────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    top_k: int = 5

class SearchResult(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    page: int
    text: str
    score: float

class FeedbackRequest(BaseModel):
    query: str
    chunk_id: str
    helpful: bool


# ── routes ─────────────────────────────────────────────────────────────────

@app.post("/ingest")
def ingest(data_dir: str = "data"):
    """Trigger ingestion of all PDFs in data_dir into MongoDB + Qdrant."""
    try:
        from src.ingest import run_ingest
        n_chunks = run_ingest(data_dir=data_dir)
        return {"status": "ok", "chunks_inserted": n_chunks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/search", response_model=list[SearchResult])
def search(req: SearchRequest):
    """Hybrid BM25 + dense search over ingested chunks."""
    try:
        results = _searcher.search(req.query, top_k=req.top_k)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/rebuild-index")
def rebuild_index():
    """Force-rebuild the in-memory BM25+dense index (call after /ingest adds new docs)."""
    global _searcher
    try:
        from src.vector_store import HybridSearch
        _searcher = HybridSearch()
        return {"status": "rebuilt"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/feedback")
def feedback(req: FeedbackRequest):
    """Record a relevance signal — stored in MongoDB, consumed by online learner in D3."""
    try:
        from pymongo import MongoClient
        uri = os.getenv("MONGO_URI", "mongodb://admin:changeme@localhost:27017")
        client = MongoClient(uri)
        client.d2.feedback.insert_one(req.model_dump())
        return {"status": "recorded"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
def stats():
    """Return document/chunk counts from MongoDB and Qdrant collection info."""
    out = {}
    try:
        from pymongo import MongoClient
        uri = os.getenv("MONGO_URI", "mongodb://admin:changeme@localhost:27017")
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        out["mongo_chunks"]    = client.d2.chunks.count_documents({})
        out["mongo_docs"]      = client.d2.docs.count_documents({})
        out["mongo_feedback"]  = client.d2.feedback.count_documents({})
    except Exception as e:
        out["mongo_error"] = str(e)

    try:
        from qdrant_client import QdrantClient
        host = os.getenv("QDRANT_HOST", "localhost")
        port = int(os.getenv("QDRANT_PORT", 6333))
        col  = os.getenv("QDRANT_COLLECTION", "d2_chunks")
        qc   = QdrantClient(host=host, port=port)
        info = qc.get_collection(col)
        out["qdrant_vectors"] = qc.count(col).count
    except Exception as e:
        out["qdrant_error"] = str(e)

    return out
