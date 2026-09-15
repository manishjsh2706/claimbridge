# Golden Eval Cases — Essential Track

Pre-built regression cases. **Copy** `resources/golden/` into your project (e.g. `tests/golden/`).

| File | Cases | Use |
|------|-------|-----|
| `essential-all.jsonl` | 11 | Full Essential regression |
| `essential-leakage.jsonl` | 1 | Cross-tenant citation check |
| `essential-adversarial.jsonl` | 2 | Injection + safety |

## Case fields

- `audience`: `member` or `provider` — which output to evaluate
- `fixture_claim_id`: maps to `sample-claims.md`
- `required_citations`, `forbidden_doc_prefixes`, `rubric_min_scores`

## Quick start

```bash
# Mentee implements runner; fixtures are inputs
cat resources/golden/essential-all.jsonl
```

Run against your API after each iteration; mentor uses same files for sign-off.
