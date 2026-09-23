"""
Tenant policy parser - ClaimBridge
==================================

Turns the tenant policy markdown in resources/policies/{tenant_id}/*.md into
section-level chunks ready for indexing.

WHY CHUNK ON THE `**Section:**` MARKERS
The policy documents already declare their own citation anchors:

    **Section:** `prior-auth.imaging`

The golden eval cases check for exactly these strings
("required_policy_sections": ["prior-auth.imaging"]), and the recommended
citation format references them. Chunking on those anchors means one chunk ==
one citable section, so a citation always points at a real, retrievable unit.
Fixed-size token chunking would split sections mid-sentence and leave the
model citing half a rule.

WHY THE TENANT IS VERIFIED TWICE
The folder name (policies/pacific-hmo/) and the in-document header
(**Tenant:** `pacific-hmo`) must agree. If someone drops a Coastal document into
the Pacific folder, indexing it under the folder's tenant would silently leak
Coastal policy into Pacific member summaries. A mismatch is a hard error.
"""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_META_RE = re.compile(r"^\*\*(Tenant|Effective|Document ID|Status):\*\*\s*`?([^`\n]+?)`?\s*$", re.MULTILINE)
_SECTION_RE = re.compile(r"^\*\*Section:\*\*\s*`([^`]+)`\s*$", re.MULTILINE)
_HEADING_RE = re.compile(r"^(#{2,4})\s+(.+?)\s*$", re.MULTILINE)
_SEPARATOR_RE = re.compile(r"^\s*---\s*$", re.MULTILINE)


class PolicyParseError(ValueError):
    """Raised when a policy document is malformed or mis-filed."""


@dataclass
class PolicyChunk:
    tenant_id: str
    document_id: str
    doc_key: str            # "{tenant_id}-{document_id}", e.g. pacific-hmo-plan-summary
    document_title: str
    section_path: str       # citation anchor, e.g. prior-auth.imaging
    section_title: str      # human heading trail, e.g. "Prior authorization > Imaging"
    body: str               # section text without headings or the Section marker
    effective_date: str     # "" when the document does not state one -- never invented
    source_file: str        # path relative to the project root

    @property
    def content(self) -> str:
        """
        Text that gets embedded and keyword-indexed.

        The document title and heading trail are prefixed so a chunk carries its
        own context: "Pacific HMO - Plan Summary > Prior authorization > Imaging"
        embeds very differently from a bare bullet list about MRI codes, and the
        BM25 half of hybrid search can match the heading words.
        """
        return f"{self.document_title} > {self.section_title}\n\n{self.body}".strip()

    def to_properties(self, corpus_version: str, ingested_at: str) -> Dict[str, str]:
        return {
            "content": self.content,
            "tenant_id": self.tenant_id,
            "document_id": self.document_id,
            "doc_key": self.doc_key,
            "document_title": self.document_title,
            "section_path": self.section_path,
            "section_title": self.section_title,
            "effective_date": self.effective_date,
            "source_file": self.source_file,
            "corpus_version": corpus_version,
            "ingested_at": ingested_at,
        }


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_policy_file(path: Path, expected_tenant_id: str, project_root: Path) -> List[PolicyChunk]:
    """Parse one policy markdown file into section chunks."""
    raw = path.read_text(encoding="utf-8")

    title_match = _TITLE_RE.search(raw)
    if not title_match:
        raise PolicyParseError(f"{path}: missing '# Title' line")
    document_title = title_match.group(1).replace("(Synthetic)", "").strip()

    meta = {k: v.strip() for k, v in _META_RE.findall(raw)}
    declared_tenant = meta.get("Tenant")
    if declared_tenant != expected_tenant_id:
        raise PolicyParseError(
            f"{path}: declares tenant {declared_tenant!r} but is filed under "
            f"{expected_tenant_id!r}. Refusing to index -- a mis-filed policy "
            "would leak across tenants."
        )

    document_id = meta.get("Document ID") or path.stem
    effective_date = meta.get("Effective", "")

    blocks = _SEPARATOR_RE.split(raw)
    # Block 0 is the title + metadata header; content sections follow.
    chunks: List[PolicyChunk] = []
    seen_paths = set()

    for block in blocks[1:]:
        block = block.strip()
        if not block:
            continue

        headings = [h[1].strip() for h in _HEADING_RE.findall(block)]
        section_match = _SECTION_RE.search(block)

        body = _HEADING_RE.sub("", block)
        body = _SECTION_RE.sub("", body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()

        if not headings and not body:
            continue

        section_title = " > ".join(headings) if headings else "General"
        # Sections without an explicit marker (e.g. "Overview") get a slug of
        # their heading so they are still citable, rather than being dropped.
        section_path = section_match.group(1).strip() if section_match else _slugify(headings[0] if headings else "general")

        if section_path in seen_paths:
            raise PolicyParseError(f"{path}: duplicate section path {section_path!r}")
        seen_paths.add(section_path)

        chunks.append(
            PolicyChunk(
                tenant_id=expected_tenant_id,
                document_id=document_id,
                doc_key=f"{expected_tenant_id}-{document_id}",
                document_title=document_title,
                section_path=section_path,
                section_title=section_title,
                body=body,
                effective_date=effective_date,
                source_file=path.relative_to(project_root).as_posix(),
            )
        )

    if not chunks:
        raise PolicyParseError(f"{path}: no content sections found")
    return chunks


@dataclass
class TenantCorpus:
    tenant_id: str
    chunks: List[PolicyChunk] = field(default_factory=list)
    corpus_version: str = ""
    files: List[str] = field(default_factory=list)


def load_tenant_corpora(policies_dir: Path, project_root: Path, tenant_ids: Optional[List[str]] = None) -> Dict[str, TenantCorpus]:
    """
    Parse every tenant folder under resources/policies/.

    corpus_version is a content hash of the tenant's policy files. The onboarding
    checklist asks us to "record policy_corpus_version"; a hash means the version
    changes if and only if the policy text changes, so a summary can later be
    traced to the exact policy wording it was generated from.
    """
    if not policies_dir.is_dir():
        raise PolicyParseError(f"Policies directory not found: {policies_dir}")

    corpora: Dict[str, TenantCorpus] = {}
    for tenant_dir in sorted(p for p in policies_dir.iterdir() if p.is_dir()):
        tenant_id = tenant_dir.name
        if tenant_ids and tenant_id not in tenant_ids:
            continue

        files = sorted(tenant_dir.glob("*.md"))
        if not files:
            continue

        digest = hashlib.sha256()
        corpus = TenantCorpus(tenant_id=tenant_id)
        for f in files:
            digest.update(f.name.encode())
            digest.update(f.read_bytes())
            corpus.chunks.extend(parse_policy_file(f, tenant_id, project_root))
            corpus.files.append(f.relative_to(project_root).as_posix())

        corpus.corpus_version = digest.hexdigest()[:12]
        corpora[tenant_id] = corpus

    return corpora
