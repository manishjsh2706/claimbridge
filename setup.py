from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="claimbridge",
    version="0.1.0",
    author="Your Team",
    author_email="team@claimbridge.com",
    description="Multi-tenant AI claims processing system with RAG and LangGraph orchestration",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/claimbridge",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.10",
    install_requires=[
        "fastapi>=0.104.0",
        "uvicorn>=0.24.0",
        "pydantic>=2.5.0",
        "langchain>=0.1.0",
        "langgraph>=0.0.15",
        "weaviate-client>=4.1.0",
        "sqlalchemy>=2.0.0",
        "psycopg2-binary>=2.9.0",
        "python-dotenv>=1.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.21.0",
            "pytest-cov>=4.1.0",
            "black>=23.12.0",
            "flake8>=6.1.0",
            "mypy>=1.7.0",
        ],
        "pinecone": ["pinecone-client>=3.0.0"],
        "chromadb": ["chromadb>=0.4.0"],
    },
)
