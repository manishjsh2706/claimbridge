"""
Knowledge package for ClaimBridge.

Everything the assistant is allowed to cite comes through here, loaded from the
provided resources/ folder -- never from the model's own knowledge.

    policy_parser   tenant policy markdown -> section chunks (indexed in Weaviate)
    code_reference  shared CARC/RARC definitions -> exact-match lookup
    reference_data  tenant catalog + sample claims -> rows for Postgres

Two retrieval strategies on purpose: policies are searched semantically because
the claim does not say which policy section applies; codes are looked up exactly
because the claim states them.
"""

import os
from functools import lru_cache
from pathlib import Path

from .code_reference import CodeDefinition, CodeReference, CodeReferenceError, load_code_reference, normalize_code
from .policy_parser import PolicyChunk, PolicyParseError, TenantCorpus, load_tenant_corpora, parse_policy_file
from .reference_data import (
    ClaimFixture, ReferenceDataError, TenantRecord, parse_sample_claims, parse_tenant_catalog,
)


@lru_cache(maxsize=1)
def get_code_reference() -> CodeReference:
    """Process-wide CARC/RARC reference, parsed once from resources/."""
    return load_code_reference(resources_dir() / "carc-rarc-reference.md")


def resources_dir() -> Path:
    """
    Locate resources/. $RESOURCES_DIR wins; otherwise resolve relative to the
    project root (src/claimbridge/knowledge/ -> three levels up), which works
    both on the host and inside the container at /app.
    """
    env = os.getenv("RESOURCES_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "resources"


__all__ = [
    "CodeDefinition", "CodeReference", "CodeReferenceError", "load_code_reference", "normalize_code",
    "PolicyChunk", "PolicyParseError", "TenantCorpus", "load_tenant_corpora", "parse_policy_file",
    "ClaimFixture", "ReferenceDataError", "TenantRecord", "parse_sample_claims", "parse_tenant_catalog",
    "get_code_reference", "resources_dir",
]
