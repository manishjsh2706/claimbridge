"""
Configuration management for ClaimBridge application.
Loads settings from environment variables and .env files.
"""

from pydantic_settings import BaseSettings
from typing import Optional
import os


class Settings(BaseSettings):
    """Application configuration from environment variables."""
    
    # App Settings
    APP_NAME: str = "ClaimBridge"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    
    # API Settings
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_PREFIX: str = "/v1"
    
    # Database Settings
    DATABASE_URL: str = "postgresql://user:password@localhost:5432/claimbridge"
    DATABASE_ECHO: bool = False
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10
    
    # VectorDB Settings (Weaviate)
    WEAVIATE_URL: str = "http://localhost:8080"
    WEAVIATE_API_KEY: Optional[str] = None
    
    # LLM Settings (OpenAI)
    OPENAI_API_KEY: str = "sk-..."
    LLM_MODEL: str = "gpt-4"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    
    # RAG Settings
    RAG_TOP_K: int = 5
    RAG_SCORE_THRESHOLD: float = 0.7
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 100
    
    # Quality Rubric Settings
    QUALITY_MIN_SCORE: float = 3.5
    QUALITY_DIMENSIONS: int = 7
    
    # Multi-Tenant Settings
    TENANT_ISOLATION_ENABLED: bool = True
    SUPPORTED_TENANTS: list = [
        "pacific-hmo",
        "coastal-ppo",
        "summit-employer"
    ]
    
    # Security Settings
    CORS_ORIGINS: list = ["http://localhost:3000", "http://localhost:8080"]
    JWT_SECRET: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24
    
    # Audit Settings
    AUDIT_LOGGING_ENABLED: bool = True
    AUDIT_LOG_TABLE: str = "audit_logs"
    
    # MCP Settings
    MCP_TOOL_VALIDATION_ENABLED: bool = True
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


# Global settings instance
settings = Settings()
