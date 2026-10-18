import os
from dotenv import load_dotenv

load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "youtube_recommender")
CACHE_TTL_HOURS = max(1, int(os.getenv("CACHE_TTL_HOURS", "24")))
VECTOR_VERSION = os.getenv("VECTOR_VERSION", "hashing-v1")
DEFAULT_SEARCH_RESULTS = min(50, max(1, int(os.getenv("DEFAULT_SEARCH_RESULTS", "15"))))

if not YOUTUBE_API_KEY or YOUTUBE_API_KEY == "PASTE_YOUR_YOUTUBE_API_KEY_HERE":
    YOUTUBE_API_KEY = ""
