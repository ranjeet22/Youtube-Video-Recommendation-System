CREATE DATABASE IF NOT EXISTS youtube_recommender
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE youtube_recommender;

CREATE TABLE IF NOT EXISTS videos (
    video_id VARCHAR(32) PRIMARY KEY,
    title TEXT NOT NULL,
    description MEDIUMTEXT,
    tags JSON NULL,
    category_id VARCHAR(16) NULL,
    channel_title VARCHAR(255) NULL,
    published_at DATETIME NULL,
    view_count BIGINT NULL,
    like_count BIGINT NULL,
    duration VARCHAR(32) NULL,
    thumbnail_url TEXT NULL,
    video_url TEXT NOT NULL,
    metadata_updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS search_queries (
    query_hash CHAR(64) PRIMARY KEY,
    query_text VARCHAR(255) NOT NULL,
    region_code CHAR(2) NOT NULL DEFAULT 'IN',
    relevance_language VARCHAR(10) NOT NULL DEFAULT 'en',
    max_results TINYINT UNSIGNED NOT NULL DEFAULT 15,
    cached_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS search_query_results (
    query_hash CHAR(64) NOT NULL,
    video_id VARCHAR(32) NOT NULL,
    position_no TINYINT UNSIGNED NOT NULL,
    PRIMARY KEY (query_hash, video_id),
    UNIQUE KEY uq_query_position (query_hash, position_no),
    CONSTRAINT fk_sqr_query FOREIGN KEY (query_hash)
        REFERENCES search_queries(query_hash) ON DELETE CASCADE,
    CONSTRAINT fk_sqr_video FOREIGN KEY (video_id)
        REFERENCES videos(video_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS video_vectors (
    video_id VARCHAR(32) PRIMARY KEY,
    vector_data JSON NOT NULL,
    vector_version VARCHAR(32) NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_vectors_video FOREIGN KEY (video_id)
        REFERENCES videos(video_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS recommendation_cache (
    source_video_id VARCHAR(32) NOT NULL,
    query_hash CHAR(64) NOT NULL,
    recommended_video_id VARCHAR(32) NOT NULL,
    score DOUBLE NOT NULL,
    rank_position TINYINT UNSIGNED NOT NULL,
    cached_at DATETIME NOT NULL,
    PRIMARY KEY (source_video_id, query_hash, recommended_video_id),
    UNIQUE KEY uq_recommendation_rank (source_video_id, query_hash, rank_position),
    CONSTRAINT fk_rc_source FOREIGN KEY (source_video_id)
        REFERENCES videos(video_id) ON DELETE CASCADE,
    CONSTRAINT fk_rc_recommended FOREIGN KEY (recommended_video_id)
        REFERENCES videos(video_id) ON DELETE CASCADE,
    CONSTRAINT fk_rc_query FOREIGN KEY (query_hash)
        REFERENCES search_queries(query_hash) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_interactions (
    interaction_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(128) NULL,
    video_id VARCHAR(32) NOT NULL,
    interaction_type ENUM('click', 'like', 'dislike', 'skip', 'watch') NOT NULL,
    watch_duration_seconds INT UNSIGNED NULL,
    created_at DATETIME NOT NULL,
    KEY idx_interactions_user (user_id, created_at),
    KEY idx_interactions_video (video_id),
    CONSTRAINT fk_interaction_video FOREIGN KEY (video_id)
        REFERENCES videos(video_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
