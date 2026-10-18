from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from app import config
from app.database import ping_database
from app.services import ServiceError, recommend_from_search, record_interaction, search_videos

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="YouTube Recommendation Backend",
    description="YouTube search, MySQL caching, persisted vectors, and content-based recommendations.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class InteractionRequest(BaseModel):
    video_id: str = Field(min_length=1, max_length=32)
    interaction_type: str = Field(pattern="^(click|like|dislike|skip|watch)$")
    user_id: str | None = Field(default=None, max_length=128)
    watch_duration_seconds: int | None = Field(default=None, ge=0)


@app.get("/")
def root():
    return {"message": "Backend is running.", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "database": "connected" if ping_database() else "disconnected",
        "youtube_api_key_configured": bool(config.YOUTUBE_API_KEY),
    }


@app.get("/search")
def search(
    q: str = Query(..., min_length=1, max_length=255),
    region_code: str = Query("IN", min_length=2, max_length=2),
    language: str = Query("en", min_length=2, max_length=10),
    max_results: int = Query(config.DEFAULT_SEARCH_RESULTS, ge=1, le=50),
):
    try:
        return search_videos(q, region_code, language, max_results)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Search failed: {exc}") from exc


@app.get("/recommend/{video_id}")
def recommend(
    video_id: str,
    q: str = Query(..., min_length=1, max_length=255,
                   description="Pass the same query used in /search"),
    region_code: str = Query("IN", min_length=2, max_length=2),
    language: str = Query("en", min_length=2, max_length=10),
    max_results: int = Query(config.DEFAULT_SEARCH_RESULTS, ge=1, le=50),
    top_n: int = Query(10, ge=1, le=10),
):
    try:
        return recommend_from_search(video_id, q, region_code, language, max_results, top_n)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Recommendation failed: {exc}") from exc


@app.post("/interactions")
def interactions(body: InteractionRequest):
    try:
        return record_interaction(
            body.video_id, body.interaction_type, body.user_id, body.watch_duration_seconds
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not save interaction: {exc}") from exc
