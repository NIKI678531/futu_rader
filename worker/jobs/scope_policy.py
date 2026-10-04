"""Trust boundary for running paid comment analysis from an extraction scope."""

import json

from sqlalchemy import select

from collection.exact import EXACT_RULE_VERSION
from radar_db.schema import analysis_scopes


COMMENT_PRODUCT_SCOPE_TASKS = frozenset(("comment_product", "both"))


def load_scope(engine, scope_id):
    with engine.connect() as conn:
        return conn.execute(
            select(analysis_scopes).where(analysis_scopes.c.scope_id == scope_id)
        ).mappings().first()


def require_current_exact_scope(scope, *, require_comment_product=False):
    """Return *scope* only when its comment candidates came through current exact routing.

    ``stats_json`` was nullable before exact routing existed.  Missing, malformed, and
    stale provenance are therefore all untrusted; operators must extract a new scope
    instead of paying to process legacy candidates.
    """

    if scope is None:
        raise ValueError("Unknown scope")
    scope_id = scope["scope_id"]
    task = scope["task"]
    carries_comment_products = task in COMMENT_PRODUCT_SCOPE_TASKS
    if require_comment_product and not carries_comment_products:
        raise ValueError(
            f"Scope {scope_id} is task={task!r}, not a comment_product extraction scope"
        )
    if not carries_comment_products:
        return scope

    try:
        stats = json.loads(scope["stats_json"] or "")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Scope {scope_id} has invalid stats_json and no trustworthy exactRuleVersion; "
            "run jobs.extract again"
        ) from exc
    if not isinstance(stats, dict):
        stats = {}
    actual = stats.get("exactRuleVersion")
    if actual != EXACT_RULE_VERSION:
        shown = "missing" if actual is None else repr(actual)
        raise ValueError(
            f"Scope {scope_id} exactRuleVersion is {shown}; expected {EXACT_RULE_VERSION!r}. "
            "Run jobs.extract again before paid comment analysis"
        )
    return scope


def load_current_exact_scope(engine, scope_id, *, require_comment_product=False):
    return require_current_exact_scope(
        load_scope(engine, scope_id),
        require_comment_product=require_comment_product,
    )
