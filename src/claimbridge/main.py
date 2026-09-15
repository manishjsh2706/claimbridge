"""
ClaimBridge FastAPI Application
Main entry point for the claims processing system.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

from .config import settings
from .api import router as api_router

# Configure logging
logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Multi-tenant AI claims processing system with RAG and LangGraph orchestration",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(api_router.router, prefix=settings.API_PREFIX)

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }

@app.get("/")
async def root():
    """Root endpoint with API documentation."""
    return {
        "message": f"Welcome to {settings.APP_NAME} v{settings.APP_VERSION}",
        "docs_url": "/docs",
        "openapi_schema_url": "/openapi.json",
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "claimbridge.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG,
    )
