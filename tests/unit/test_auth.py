"""Unit tests for the RBAC permission matrix (no DB)."""

from src.claimbridge.auth import KEY_PREFIX, ROLE_PERMISSIONS, Principal, hash_key


def test_permission_matrix():
    sub = Principal("a", "submitter", "pacific-hmo")
    rev = Principal("r", "reviewer", "pacific-hmo")
    aud = Principal("x", "auditor", "pacific-hmo")
    assert sub.can("claims:submit") and sub.can("claims:process") and not sub.can("review:act")
    assert rev.can("review:act") and not rev.can("claims:submit") and not rev.can("claims:process")
    assert aud.can("audit:read") and not aud.can("review:act") and not aud.can("claims:submit")
    assert all(Principal("z", "admin", None).can(p) for perms in ROLE_PERMISSIONS.values() for p in perms)


def test_nobody_can_both_generate_and_approve_except_admin():
    # separation of duties by role; admin is covered by the four-eyes check in review/state.py
    for role, perms in ROLE_PERMISSIONS.items():
        if role != "admin":
            assert not ({"claims:process", "review:act"} <= perms), role


def test_tenant_scope():
    assert Principal("r", "reviewer", "pacific-hmo").covers("pacific-hmo")
    assert not Principal("r", "reviewer", "pacific-hmo").covers("coastal-ppo")
    assert Principal("ops", "admin", None).covers("coastal-ppo")


def test_key_hash_is_stable_and_not_the_key():
    k = KEY_PREFIX + "abc"
    assert hash_key(k) == hash_key(k) and k not in hash_key(k) and len(hash_key(k)) == 64
