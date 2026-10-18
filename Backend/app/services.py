import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app import config
from app.database import db_cursor

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
HTTP_TIMEOUT = 25

# Stateless vectorizer: saved vectors remain compatible between requests.
VECTORIZER = HashingVectorizer(
    n_features=2**18,
    alternate_sign=False,
    norm="l2",
    ngram_range=(1, 2),
    stop_words="english",
    token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9_+#.-]*\b",
)


class ServiceError(RuntimeError):
    pass


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def make_query_hash(query: str, region_code: str, language: str, max_results: int) -> str:
    canonical = "|".join([
        query.strip().lower(), region_code.strip().upper(),
        language.strip().lower(), str(max_results),
    ])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_search_inputs(query: str, region_code: str, language: str, max_results: int):
    query = " ".join(query.split()).strip()
    if not query:
        raise ValueError("Search query cannot be empty.")
    if len(query) > 255:
        raise ValueError("Search query must be 255 characters or fewer.")
    if len(region_code) != 2 or not region_code.isalpha():
        raise ValueError("region_code must be a two-letter code, e.g. IN.")
    if not language or len(language) > 10:
        raise ValueError("language must be a short language code, e.g. en or hi.")
    if not 1 <= max_results <= 50:
        raise ValueError("max_results must be between 1 and 50.")
    return query, region_code.upper(), language.lower(), max_results


def _parse_api_datetime(value: str | None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _ensure_api_key():
    if not config.YOUTUBE_API_KEY:
        raise ServiceError("YOUTUBE_API_KEY is missing. Add your key to .env and restart the server.")


def _youtube_get(url: str, params: dict[str, Any]) -> dict:
    _ensure_api_key()
    try:
        response = requests.get(
            url, params={**params, "key": config.YOUTUBE_API_KEY}, timeout=HTTP_TIMEOUT
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", {}).get("message", response.text)
            except ValueError:
                detail = response.text
            raise ServiceError(f"YouTube API returned HTTP {response.status_code}: {detail}")
        return response.json()
    except requests.RequestException as exc:
        raise ServiceError(f"Could not connect to YouTube API: {exc}") from exc


def _clean_video_from_search(item: dict) -> dict | None:
    video_id = item.get("id", {}).get("videoId")
    snippet = item.get("snippet", {})
    if not video_id or not snippet:
        return None
    thumbnails = snippet.get("thumbnails", {})
    thumbnail = thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}
    return {
        "video_id": video_id,
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "tags": [],
        "category_id": None,
        "channel_title": snippet.get("channelTitle", ""),
        "published_at": _parse_api_datetime(snippet.get("publishedAt")),
        "view_count": None,
        "like_count": None,
        "duration": None,
        "thumbnail_url": thumbnail.get("url", ""),
        "video_url": f"https://www.youtube.com/watch?v={video_id}",
    }


def _enrich_videos(videos: list[dict]) -> list[dict]:
    if not videos:
        return videos
    payload = _youtube_get(
        YOUTUBE_VIDEOS_URL,
        {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(v["video_id"] for v in videos[:50]),
        },
    )
    details = {item["id"]: item for item in payload.get("items", [])}
    for video in videos:
        item = details.get(video["video_id"])
        if not item:
            continue
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})
        video.update({
            "title": snippet.get("title", video["title"]),
            "description": snippet.get("description", video["description"]),
            "tags": snippet.get("tags", []),
            "category_id": snippet.get("categoryId"),
            "channel_title": snippet.get("channelTitle", video["channel_title"]),
            "published_at": _parse_api_datetime(snippet.get("publishedAt")) or video["published_at"],
            "view_count": int(stats["viewCount"]) if "viewCount" in stats else None,
            "like_count": int(stats["likeCount"]) if "likeCount" in stats else None,
            "duration": content.get("duration"),
        })
        thumbnails = snippet.get("thumbnails", {})
        thumb = thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}
        video["thumbnail_url"] = thumb.get("url", video["thumbnail_url"])
    return videos


def _save_videos(videos: list[dict]):
    if not videos:
        return
    sql = """
        INSERT INTO videos
        (video_id, title, description, tags, category_id, channel_title,
         published_at, view_count, like_count, duration, thumbnail_url, video_url)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          title=VALUES(title), description=VALUES(description), tags=VALUES(tags),
          category_id=VALUES(category_id), channel_title=VALUES(channel_title),
          published_at=VALUES(published_at), view_count=VALUES(view_count),
          like_count=VALUES(like_count), duration=VALUES(duration),
          thumbnail_url=VALUES(thumbnail_url), video_url=VALUES(video_url)
    """
    rows = [
        (
            v["video_id"], v.get("title", ""), v.get("description", ""),
            json.dumps(v.get("tags", []), ensure_ascii=False), v.get("category_id"),
            v.get("channel_title"), v.get("published_at"), v.get("view_count"),
            v.get("like_count"), v.get("duration"), v.get("thumbnail_url"),
            v.get("video_url") or f'https://www.youtube.com/watch?v={v["video_id"]}',
        )
        for v in videos
    ]
    with db_cursor() as (_, cursor):
        cursor.executemany(sql, rows)


def _read_video_rows(video_ids: list[str]) -> list[dict]:
    if not video_ids:
        return []
    placeholders = ",".join(["%s"] * len(video_ids))
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            f"""SELECT video_id, title, description, tags, category_id, channel_title,
                       published_at, view_count, like_count, duration, thumbnail_url, video_url
                FROM videos WHERE video_id IN ({placeholders})""",
            tuple(video_ids),
        )
        rows = cursor.fetchall()
    by_id = {row["video_id"]: row for row in rows}
    output = []
    for video_id in video_ids:
        row = by_id.get(video_id)
        if row:
            if isinstance(row.get("tags"), str):
                try:
                    row["tags"] = json.loads(row["tags"])
                except (ValueError, TypeError):
                    row["tags"] = []
            output.append(row)
    return output


def _get_fresh_search_cache(query_hash: str) -> list[dict] | None:
    cutoff = utcnow_naive() - timedelta(hours=config.CACHE_TTL_HOURS)
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute("SELECT cached_at FROM search_queries WHERE query_hash=%s", (query_hash,))
        cached = cursor.fetchone()
        if not cached or cached["cached_at"] < cutoff:
            return None
        cursor.execute(
            "SELECT video_id FROM search_query_results WHERE query_hash=%s ORDER BY position_no",
            (query_hash,),
        )
        ids = [row["video_id"] for row in cursor.fetchall()]
    return _read_video_rows(ids)


def search_videos(query: str, region_code: str = "IN", language: str = "en",
                  max_results: int | None = None) -> dict:
    max_results = max_results or config.DEFAULT_SEARCH_RESULTS
    query, region_code, language, max_results = validate_search_inputs(
        query, region_code, language, max_results
    )
    query_hash = make_query_hash(query, region_code, language, max_results)
    cached = _get_fresh_search_cache(query_hash)
    if cached is not None:
        return {"query": query, "region_code": region_code, "language": language,
                "cached": True, "videos": cached}

    payload = _youtube_get(
        YOUTUBE_SEARCH_URL,
        {
            "part": "snippet", "q": query, "type": "video",
            "maxResults": max_results, "regionCode": region_code,
            "relevanceLanguage": language, "safeSearch": "moderate",
        },
    )
    videos, seen = [], set()
    for item in payload.get("items", []):
        video = _clean_video_from_search(item)
        if video and video["video_id"] not in seen:
            videos.append(video)
            seen.add(video["video_id"])
    videos = _enrich_videos(videos)
    _save_videos(videos)

    with db_cursor() as (_, cursor):
        cursor.execute(
            """INSERT INTO search_queries
               (query_hash, query_text, region_code, relevance_language, max_results, cached_at)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE query_text=VALUES(query_text),
                 region_code=VALUES(region_code), relevance_language=VALUES(relevance_language),
                 max_results=VALUES(max_results), cached_at=VALUES(cached_at)""",
            (query_hash, query, region_code, language, max_results, utcnow_naive()),
        )
        cursor.execute("DELETE FROM search_query_results WHERE query_hash=%s", (query_hash,))
        for position, video in enumerate(videos, start=1):
            cursor.execute(
                "INSERT INTO search_query_results (query_hash, video_id, position_no) VALUES (%s, %s, %s)",
                (query_hash, video["video_id"], position),
            )
    return {"query": query, "region_code": region_code, "language": language,
            "cached": False, "videos": videos}


def _video_text(video: dict) -> str:
    """Build descriptive text only. URLs and IDs are intentionally excluded."""
    tags = video.get("tags") or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (ValueError, TypeError):
            tags = [tags]
    parts = [
        video.get("title") or "", video.get("description") or "",
        " ".join(str(tag) for tag in tags), video.get("channel_title") or "",
        video.get("category_id") or "",
    ]
    return " ".join(part for part in parts if part).strip()


def _serialize_sparse_vector(vector) -> str:
    vector = vector.tocsr()
    return json.dumps(
        {"indices": vector.indices.tolist(), "values": [float(x) for x in vector.data]},
        separators=(",", ":"),
    )


def _deserialize_sparse_vector(serialized: str):
    from scipy.sparse import csr_matrix
    data = json.loads(serialized)
    indices, values = data.get("indices", []), data.get("values", [])
    return csr_matrix((values, indices, [0, len(indices)]), shape=(1, 2**18))


def _get_stored_vector(video_id: str):
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            "SELECT vector_data, vector_version FROM video_vectors WHERE video_id=%s",
            (video_id,),
        )
        row = cursor.fetchone()
    if not row or row["vector_version"] != config.VECTOR_VERSION:
        return None
    try:
        serialized = row["vector_data"]
        if not isinstance(serialized, str):
            serialized = json.dumps(serialized)
        return _deserialize_sparse_vector(serialized)
    except (ValueError, TypeError, KeyError):
        return None


def _save_vector(video_id: str, vector):
    with db_cursor() as (_, cursor):
        cursor.execute(
            """INSERT INTO video_vectors (video_id, vector_data, vector_version, updated_at)
               VALUES (%s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE vector_data=VALUES(vector_data),
                 vector_version=VALUES(vector_version), updated_at=VALUES(updated_at)""",
            (video_id, _serialize_sparse_vector(vector), config.VECTOR_VERSION, utcnow_naive()),
        )


def get_or_create_vectors(videos: list[dict]):
    vectors, missing = {}, []
    for video in videos:
        vector = _get_stored_vector(video["video_id"])
        if vector is None:
            missing.append(video)
        else:
            vectors[video["video_id"]] = vector
    if missing:
        matrix = VECTORIZER.transform([_video_text(v) for v in missing])
        for index, video in enumerate(missing):
            vector = matrix[index]
            vectors[video["video_id"]] = vector
            _save_vector(video["video_id"], vector)
    return vectors


def _get_cached_recommendations(source_video_id: str, query_hash: str) -> list[dict] | None:
    cutoff = utcnow_naive() - timedelta(hours=config.CACHE_TTL_HOURS)
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """SELECT MAX(cached_at) AS cached_at FROM recommendation_cache
               WHERE source_video_id=%s AND query_hash=%s""",
            (source_video_id, query_hash),
        )
        row = cursor.fetchone()
        if not row or not row["cached_at"] or row["cached_at"] < cutoff:
            return None
        cursor.execute(
            """SELECT recommended_video_id, score, rank_position FROM recommendation_cache
               WHERE source_video_id=%s AND query_hash=%s ORDER BY rank_position""",
            (source_video_id, query_hash),
        )
        recs = cursor.fetchall()
    if not recs:
        return None
    videos = _read_video_rows([r["recommended_video_id"] for r in recs])
    by_id = {v["video_id"]: v for v in videos}
    result = []
    for rec in recs:
        video = by_id.get(rec["recommended_video_id"])
        if video:
            result.append({**video, "similarity_score": round(float(rec["score"]), 4),
                           "rank": int(rec["rank_position"])})
    return result


def recommend_from_search(source_video_id: str, query: str, region_code: str = "IN",
                          language: str = "en", max_results: int | None = None,
                          top_n: int = 10) -> dict:
    """Rank the search pool that contains the selected video; avoids a second search."""
    max_results = max_results or config.DEFAULT_SEARCH_RESULTS
    top_n = min(10, max(1, top_n))
    query, region_code, language, max_results = validate_search_inputs(
        query, region_code, language, max_results
    )
    query_hash = make_query_hash(query, region_code, language, max_results)
    search_result = search_videos(query, region_code, language, max_results)
    candidates = search_result["videos"]
    source = next((v for v in candidates if v["video_id"] == source_video_id), None)
    if source is None:
        known = _read_video_rows([source_video_id])
        if not known:
            raise ValueError(
                "Selected video is not in this search pool. Search for it first and pass the same query."
            )
        source = known[0]

    cached = _get_cached_recommendations(source_video_id, query_hash)
    if cached is not None:
        return {"source_video": source, "query": query, "cached": True,
                "candidate_count": len(candidates), "recommendations": cached[:top_n]}

    pool_by_id = {v["video_id"]: v for v in candidates}
    pool_by_id[source_video_id] = source
    pool = list(pool_by_id.values())
    vectors = get_or_create_vectors(pool)
    source_vector = vectors[source_video_id]
    ranked = []
    for video in pool:
        if video["video_id"] == source_video_id:
            continue
        score = float(cosine_similarity(source_vector, vectors[video["video_id"]])[0][0])
        ranked.append((video, score))
    ranked.sort(key=lambda item: item[1], reverse=True)

    now = utcnow_naive()
    with db_cursor() as (_, cursor):
        cursor.execute(
            "DELETE FROM recommendation_cache WHERE source_video_id=%s AND query_hash=%s",
            (source_video_id, query_hash),
        )
        for rank, (video, score) in enumerate(ranked, start=1):
            cursor.execute(
                """INSERT INTO recommendation_cache
                   (source_video_id, query_hash, recommended_video_id, score, rank_position, cached_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (source_video_id, query_hash, video["video_id"], score, rank, now),
            )

    recommendations = [
        {**video, "similarity_score": round(score, 4), "rank": rank}
        for rank, (video, score) in enumerate(ranked[:top_n], start=1)
    ]
    return {"source_video": source, "query": query, "cached": False,
            "candidate_count": len(candidates), "recommendations": recommendations}


def record_interaction(video_id: str, interaction_type: str, user_id: str | None = None,
                       watch_duration_seconds: int | None = None) -> dict:
    allowed = {"click", "like", "dislike", "skip", "watch"}
    if interaction_type not in allowed:
        raise ValueError(f"interaction_type must be one of: {', '.join(sorted(allowed))}")
    if watch_duration_seconds is not None and watch_duration_seconds < 0:
        raise ValueError("watch_duration_seconds cannot be negative")
    with db_cursor() as (_, cursor):
        cursor.execute("SELECT video_id FROM videos WHERE video_id=%s", (video_id,))
        if not cursor.fetchone():
            raise ValueError("Video not found. Search for it before recording interactions.")
        cursor.execute(
            """INSERT INTO user_interactions
               (user_id, video_id, interaction_type, watch_duration_seconds, created_at)
               VALUES (%s, %s, %s, %s, %s)""",
            (user_id, video_id, interaction_type, watch_duration_seconds, utcnow_naive()),
        )
    return {"saved": True, "video_id": video_id, "interaction_type": interaction_type}
