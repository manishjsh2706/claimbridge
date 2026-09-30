# Reference endpoints — `whoami`, `policy-search`, `codes`

Three read-only lookups. None of them changes anything.

---

# 1. `GET /v1/whoami`

```
GET /v1/whoami
Headers: X-Api-Key
```

No permission check beyond a valid key — and **no tenant in the path**. This is
the only route in the API that is not tenant-scoped.

```json
{
  "principal_id": "reviewer-demo",
  "role": "reviewer",
  "tenant_id": "pacific-hmo",
  "permissions": ["audit:read", "claims:read", "review:act", "review:read"]
}
```

From the endpoint's docstring:

> *"The only route that is not tenant-scoped, because its answer is which
> tenant the caller is scoped to. A client that had to be told its own tenant
> could also be told a different one; here the key decides and the caller only
> finds out."*

This is what the MCP server calls at startup. It learns its tenant from the key
and puts that in every later path — which is why **no MCP tool takes a tenant
argument**. An argument that does not exist cannot be talked into a different
value by a prompt.

`permissions` is derived from the role at request time
(`ROLE_PERMISSIONS[role]`), not stored on the key. Adding a permission to a role
reaches every existing key immediately; there is one place where permissions are
defined.

---

# 2. `GET /v1/tenants/{tenant_id}/policy-search`

```
GET /v1/tenants/{t}/policy-search?q=<text>&limit=<1-10>
Headers: X-Api-Key
```

Permission: **`claims:read`**. `q` is 3–500 characters; `limit` defaults to 4.

Hybrid search over this tenant's policy corpus, with no model in the path. The
retrieval step of RAG, callable on its own.

Each hit: `doc_key`, `document_title`, `section_path`, `section_title`,
`content`, `effective_date`, `corpus_version`, `score`.

## Four design points

**Tenant filter goes inside the query.** From the docstring:

> *"...applied as a filter INSIDE the Weaviate query, so another tenant's
> sections are never candidates for ranking -- the same pre-filtering the
> summary pipeline uses, not a retrieve-then-discard pass that would still leak
> through scores."*

Post-filtering would let another tenant's documents influence the ranking, and
scores are observable.

**Defence in depth.** Every hit is re-checked after the filtered search; a
foreign-tenant section is dropped *and* logged at error level. Two layers, so a
regression in the search layer cannot leak silently.

**Outage degrades, it does not fail.** A Weaviate outage returns an empty list
with `degraded: true` and a note, never a 500. The caller can tell "no relevant
policy" from "the search did not run" — without the flag it would assume the
first.

**Auditing never masks the answer.** The `POLICY_SEARCHED` audit write is
wrapped in its own try/except: if auditing fails, the search result is still
returned and the failure is logged.

## The scores are the useful part

Observed on `pacific-hmo`, 2026-09-30, for *"what happens when the provider
bills more than the allowed amount"*:

| section | score |
|---|---|
| `fee-schedule.allowed-amounts` | **1.0** |
| `denial.prior-auth` | 0.240 |
| `provider.prior-auth-submission` | 0.214 |
| `appeals.member` | 0.116 |

Roughly 4x between the top hit and the next. The relevance signal is strong and
already computed — and the template fallback ignores it entirely, which is open
issue 7.

Note also that each chunk's `content` begins with its breadcrumb
(`Document > Section > Subsection`). The heading path is embedded *with* the
text, not just held as metadata, so the vector carries the context that
fixed-size chunking would lose.

---

# 3. `GET /v1/tenants/{tenant_id}/codes`

```
GET /v1/tenants/{t}/codes?code=CO-45&code=CO-197
Headers: X-Api-Key
```

`code` is repeatable. Returns `known` and `unknown`.

```json
{
  "known": [{
    "code": "CO-45", "kind": "CARC",
    "title": "Charges exceed fee schedule / maximum allowable",
    "member_friendly_name": "Charge reduced to allowed amount",
    "fields": { "typical_meaning": "...", "member_impact": "...",
                "member_next_steps": "..." }
  }],
  "unknown": ["ZZ-999"]
}
```

## Points worth making

**Unknown codes are surfaced, not swallowed.** `ZZ-999` comes back under
`unknown` rather than being dropped. That list becomes `unexplained_codes` on a
draft. Silently dropping would let a member believe a partial explanation was a
complete one.

**Not audited — deliberately.** `policy-search` writes `POLICY_SEARCHED`; this
endpoint writes nothing. CARC/RARC definitions are industry-standard shared
reference data, not a tenant's private information. What gets audited is a
decision, not blanket coverage.

**The plain-English names come from here, not from the model.** Verified by
comparison, 2026-09-30:

| This endpoint | Member draft 10193 |
|---|---|
| `member_friendly_name`: "Charge reduced to allowed amount" | `plain_name`: "Charge reduced to allowed amount" |
| `typical_meaning`: "The provider billed above the plan's allowed amount for this service" | `meaning`: "The provider billed above the plan's allowed amount for this service" |

Word for word. A human curated "Charge reduced to allowed amount" as the
member-facing name for the official "Charges exceed fee schedule / maximum
allowable"; the model copies it. If the model rephrased it each time, two members
would get two different explanations of the same code — and eventually one that
was wrong.

**The field set is data-driven.** CO-45 has four fields; CO-197 has three (no
`member_impact`). Whatever the reference data holds is what comes back.

## Who calls these

| Endpoint | Callers |
|---|---|
| `whoami` | MCP server at startup; any client checking its own scope |
| `policy-search` | reviewer console, MCP `search_policy` tool |
| `codes` | reviewer console, MCP `explain_codes` tool, provider portals |

The drafts pipeline calls **none** of them over HTTP — it uses
`get_code_reference()` and the policy search callable in-process. These exist for
callers outside the process, which is the point made in `api/v1.py`: the MCP
server is an ordinary API client holding a key, not a privileged insider, so
tenant scoping and auditing are enforced in one place rather than
re-implemented inside the tool code.
