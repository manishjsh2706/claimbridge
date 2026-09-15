# ClaimBridge Development Setup Guide

## Quick Start

### 1. Clone Repository
```bash
git clone <your-repo-url>
cd claims-assistant-essential
```

### 2. Environment Setup

#### Option A: Virtual Environment (Recommended)
```bash
# Create virtual environment
python -m venv venv

# Activate it
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
make dev
```

#### Option B: Docker
```bash
# Copy environment file
cp .env.example .env

# Start services
make docker-up

# Check logs
docker-compose logs -f claimbridge
```

### 3. Configuration
```bash
# Copy example env
cp .env.example .env

# Edit .env with your settings
# - Update OpenAI API key
# - Configure database URL
# - Set Weaviate connection
```

### 4. Database Setup
```bash
# If using local PostgreSQL:
createdb claimbridge

# Run migrations (when available)
alembic upgrade head
```

### 5. Run Application
```bash
# Development mode with auto-reload
make run

# Or with uvicorn directly:
uvicorn claimbridge.main:app --reload
```

### 6. Access Application
- **API**: http://localhost:8000
- **Docs**: http://localhost:8000/docs (Swagger)
- **ReDoc**: http://localhost:8000/redoc

## Development Workflow

### Running Tests
```bash
make test              # Run all tests with coverage
pytest tests/          # Run tests with custom options
pytest tests/unit -v   # Run unit tests only
```

### Code Quality
```bash
make format            # Format code (black + isort)
make lint              # Check code quality
```

### Common Tasks
```bash
make install           # Install dependencies only
make clean             # Clean cache and build files
make docker-logs       # View container logs
```

## Project Structure
```
claims-assistant-essential/
├── src/claimbridge/           # Main package
│   ├── api/                   # API endpoints
│   ├── rag/                   # RAG implementation
│   ├── models/                # Domain models
│   ├── mcp/                   # MCP integration
│   ├── utils/                 # Utilities
│   ├── schemas/               # Pydantic schemas
│   ├── main.py                # FastAPI app
│   └── config.py              # Configuration
├── tests/
│   ├── unit/                  # Unit tests
│   ├── integration/           # Integration tests
│   └── conftest.py            # Pytest fixtures
├── config/                    # Configuration files
├── docs/                      # Documentation
├── requirements.txt           # Dependencies
├── setup.py                   # Package setup
├── Dockerfile                 # Container image
├── docker-compose.yml         # Docker services
├── .env.example               # Environment template
└── Makefile                   # Common commands
```

## Key Technologies

- **FastAPI**: Modern Python web framework
- **LangChain/LangGraph**: LLM orchestration and state management
- **Weaviate**: Vector database for semantic search
- **PostgreSQL**: Relational database for tenant data
- **OpenAI**: LLM for claim analysis
- **Pydantic**: Data validation

## Environment Variables

See `.env.example` for all configuration options:
- API configuration
- Database connections
- LLM settings
- Vector database
- Multi-tenant settings
- Security settings

## Troubleshooting

### Port Already in Use
```bash
# Check what's using port 8000
lsof -i :8000

# Use different port
uvicorn claimbridge.main:app --port 8001
```

### Database Connection Issues
```bash
# Test PostgreSQL connection
psql -h localhost -U claimbridge -d claimbridge

# Check connection string in .env
DATABASE_URL=postgresql://user:password@host:port/dbname
```

### Docker Issues
```bash
# View all logs
docker-compose logs

# Restart services
docker-compose down && docker-compose up -d

# Clean volumes and rebuild
docker-compose down -v
docker-compose build --no-cache
```

## Next Steps

1. Review architecture documentation in `docs/architecture/`
2. Explore API endpoints in `src/claimbridge/api/`
3. Review data schemas in `src/claimbridge/schemas/`
4. Run tests to verify setup: `make test`
5. Start implementing features based on iteration backlog

## Support

- Check `docs/` folder for detailed documentation
- Review test files for usage examples
- Refer to configuration file comments for settings details

