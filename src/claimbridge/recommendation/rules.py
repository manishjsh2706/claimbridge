"""
Tenant recommendation rules - ClaimBridge
=========================================

Each rule is a plain data record derived from a tenant policy section, and
names that section as its citation. A unit test resolves every citation
against the parsed policy corpus, so a rule can never cite a section that does
not exist -- if a policy document is edited, the test fails before a wrong
citation ever reaches a reviewer.

WHY DATA, NOT AN LLM
The spec asks for recommendation logic that is "reproducible for the same
inputs" and forbids the AI from overriding outcomes. Prior-auth lists and
exclusions are exact lookups; a model would only add variance. Bump
RULES_VERSION whenever a rule changes: it is stored with every recommendation.

NOT MODELLED (on purpose, documented for the mentor)
- Summit's 90-day filing limit (medical-policy#professional.documentation):
  every provided fixture has a 2025 date of service, so the rule would deny
  them all relative to today; it needs a received-date the fixtures lack.
"""

RULES_VERSION = "rules-2026-09-22.1"

TENANT_RULES = {
    "pacific-hmo": {
        "prior_auth": [
            {
                "rule_id": "pacific.prior-auth.imaging",
                "codes": {"72148", "73721"},
                "emergency_pos_exempt": {"23"},
                "carc": "CO-197",
                "message": "Advanced imaging requires prior authorization before the date of service, "
                           "and no authorization number is on the claim.",
                "citations": ["pacific-hmo-prior-auth-rules#imaging.mri",
                              "pacific-hmo-plan-summary#prior-auth.imaging",
                              "pacific-hmo-prior-auth-rules#denial.prior-auth"],
            },
        ],
        "exclusions": [],
        "completeness_citations": [],
    },
    "coastal-ppo": {
        "prior_auth": [],
        "exclusions": [
            {
                "rule_id": "coastal.exclusion.cosmetic",
                "codes": {"11900"},
                "carc": "CO-50",
                "message": "The procedure is a cosmetic dermatology service, which the plan excludes.",
                "citations": ["coastal-ppo-benefits-guide#exclusions.cosmetic"],
            },
        ],
        "completeness_citations": ["coastal-ppo-oon-policy#billing.resubmission"],
    },
    "summit-employer": {
        "prior_auth": [],
        "exclusions": [],
        "completeness_citations": ["summit-employer-spd-excerpt#billing.completeness",
                                   "summit-employer-medical-policy#professional.documentation"],
    },
}

# CARC used for "information missing" (resources/carc-rarc-reference.md).
INCOMPLETE_CARC = "CO-16"
