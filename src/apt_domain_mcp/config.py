from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str
    milvus_uri: str
    milvus_collection: str
    server_port: int
    wiki_llm_model: str
    wiki_llm_api_key: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", ""),
            milvus_uri=os.getenv("MILVUS_URI", ""),
            milvus_collection=os.getenv("MILVUS_COLLECTION", "apt_meeting_chunks"),
            server_port=int(os.getenv("SERVER_PORT", "8002")),
            wiki_llm_model=os.getenv("WIKI_LLM_MODEL", "claude-sonnet-4-6"),
            wiki_llm_api_key=os.getenv("WIKI_LLM_API_KEY", ""),
        )


settings = Settings.from_env()
