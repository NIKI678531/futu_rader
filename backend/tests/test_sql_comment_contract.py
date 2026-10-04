"""SQL provider 的评论漏斗、相关性门控与官号归属回归。"""

from datetime import datetime

from sqlalchemy import delete, insert, update

from radar_db.comment_filter import (
    COMMENT_FILTER_DIGEST_META_KEY,
    COMMENT_FILTER_READY_META_KEY,
    extract_feed_mentions,
)
from radar_db.schema import (
    annotation_jobs,
    annotation_runs,
    comments,
    feed_mentions,
    feeds,
    mentions,
    meta_kv,
)
from sql_fixture import OWN_CODE, PEER_CODE, add_annotations, add_comments, make_sql_provider


def _add_run(provider, run_id, provider_name, *, prompt_version="comment-product-v3"):
    with provider._engine.begin() as conn:
        conn.execute(
            insert(annotation_runs).values(
                run_id=run_id,
                task="comment_product",
                provider=provider_name,
                model_id="deterministic" if provider_name == "rule" else "test-model",
                prompt_version=prompt_version,
                taxonomy_version="test-v1",
                schema_version="test-v1",
                started_at=datetime(2026, 8, 25, 12, 0),
                finished_at=datetime(2026, 8, 25, 12, 1),
                status="done",
                input_count=1,
                success_count=1,
                error_count=0,
            )
        )


def _add_comment_job(
    provider,
    *,
    comment_id,
    subject_code,
    status="pending",
    input_hash=None,
):
    now = datetime(2026, 8, 25, 12, 0)
    with provider._engine.begin() as conn:
        conn.execute(
            insert(annotation_jobs).values(
                target_type="comment",
                target_id=comment_id,
                subject_code=subject_code,
                task="comment_product",
                input_hash=input_hash or f"job-{comment_id}-{subject_code}",
                status=status,
                priority=0,
                attempts=0,
                created_at=now,
                updated_at=now,
                stage="student",
            )
        )
    provider._cache.clear()


def _add_official_feed(provider, *, feed_id, author_name, content, anchor_code):
    with provider._engine.begin() as conn:
        conn.execute(
            insert(feeds).values(
                feed_id=feed_id,
                code=anchor_code,
                source_ticker=f"0{anchor_code}.HK",
                posted_at=datetime(2026, 8, 25, 2, 0),
                feed_type=1,
                author_uid=f"official-{feed_id}",
                author_name=author_name,
                title=None,
                content=content,
                like_count=0,
                comment_count=0,
                image_count=0,
                share_count=0,
                raw_json_broken=False,
            )
        )
        conn.execute(
            insert(mentions).values(
                feed_id=feed_id,
                code=anchor_code,
                source="anchor",
                in_pool=True,
            )
        )
        extracted = [mention.as_row(feed_id) for mention in extract_feed_mentions(None, content)]
        if extracted:
            conn.execute(insert(feed_mentions), extracted)
    provider._cache.clear()


def test_product_comment_funnel_keeps_unprocessed_comments_pending_not_rule_eligible():
    provider = make_sql_provider()
    with provider._engine.begin() as conn:
        conn.execute(
            insert(mentions).values(
                feed_id=1,
                code=OWN_CODE,
                source="body",
                in_pool=True,
            )
        )
    provider._cache.clear()

    product = next(row for row in provider.pool("d1")["list"] if row["code"] == OWN_CODE)

    assert product["comments"] == 5
    assert product["commentFunnel"] == {
        "rawPlatformCount": 5,
        "platformCount": 5,
        "qualifyingFeedCount": 2,
        "filterExcludedPlatformCount": 0,
        "parsedCount": 2,
        "ruleEligibleCount": 0,
        "ruleExcludedCount": 0,
        "aiCompletedCount": 0,
        "relevantCount": 0,
        "needsContextCount": 0,
        "pendingCount": 2,
        "sourceCoverage": 0.4,
        "analysisCoverage": 0.0,
    }
    assert next(day for day in provider.daily_for(OWN_CODE) if day["iso"] == "2026-08-25")[
        "comments"
    ] == 5


def test_product_comment_funnel_separates_rule_exclusions_ai_results_and_pending():
    provider = make_sql_provider()
    add_comments(
        provider,
        [
            {"comment_id": 15, "feed_id": 1, "content": "要结合上文才知道在说什么"},
            {"comment_id": 16, "feed_id": 1, "content": "尚未分析"},
        ],
    )
    _add_run(provider, "rule-scope", "rule")
    _add_run(provider, "llm-scope", "openai_compatible")
    add_annotations(
        provider,
        [
            {
                "annotation_id": 101,
                "target_id": 11,
                "kind": "relevance",
                "value": "irrelevant",
                "run_id": "rule-scope",
            },
            {
                "annotation_id": 102,
                "target_id": 12,
                "kind": "relevance",
                "value": "relevant",
                "run_id": "llm-scope",
            },
            {
                "annotation_id": 103,
                "target_id": 15,
                "kind": "relevance",
                "value": "needs_context",
                "run_id": "llm-scope",
            },
        ],
    )

    product = next(row for row in provider.pool("d1")["list"] if row["code"] == OWN_CODE)

    assert product["commentFunnel"] == {
        "rawPlatformCount": 5,
        "platformCount": 5,
        "qualifyingFeedCount": 2,
        "filterExcludedPlatformCount": 0,
        "parsedCount": 4,
        # Comment 16 has been parsed but has no durable exact-routing result.
        # It remains pending without being guessed into the eligible stage.
        "ruleEligibleCount": 2,
        "ruleExcludedCount": 1,
        "aiCompletedCount": 2,
        "relevantCount": 1,
        "needsContextCount": 1,
        "pendingCount": 1,
        "sourceCoverage": 0.8,
        "analysisCoverage": 0.6667,
    }


def test_auditable_v2_result_remains_released_during_a_v3_relabel():
    provider = make_sql_provider()
    _add_run(
        provider,
        "old-llm-scope",
        "openai_compatible",
        prompt_version="comment-product-v2",
    )
    add_annotations(
        provider,
        [{
            "annotation_id": 121,
            "target_id": 11,
            "kind": "relevance",
            "value": "relevant",
            "run_id": "old-llm-scope",
            "input_hash": "old-v2-input",
        }],
    )
    _add_comment_job(
        provider,
        comment_id=11,
        subject_code=OWN_CODE,
        status="done",
        input_hash="old-v2-input",
    )
    _add_comment_job(provider, comment_id=11, subject_code=OWN_CODE)

    funnel = next(
        row for row in provider.pool("d1")["list"] if row["code"] == OWN_CODE
    )["commentFunnel"]

    assert funnel["aiCompletedCount"] == 1
    assert funnel["relevantCount"] == 1
    assert funnel["ruleEligibleCount"] == 1
    # The independent v3 job does not turn a valid v2 conclusion into missing
    # data. Comment 12 is the only still-unprocessed parsed reply.
    assert funnel["pendingCount"] == 1
    assert funnel["analysisCoverage"] == 0.5


def test_pending_v3_job_does_not_hide_auditable_v2_downstream_output():

    provider = make_sql_provider()
    _add_run(
        provider,
        "old-v2-opinion",
        "openai_compatible",
        prompt_version="comment-product-v2",
    )
    add_annotations(
        provider,
        [
            {
                "annotation_id": 125,
                "target_id": 11,
                "kind": "relevance",
                "value": "relevant",
                "run_id": "old-v2-opinion",
                "input_hash": "old-v2-input",
            },
            {
                "annotation_id": 126,
                "target_id": 11,
                "kind": "attitude",
                "value": "negative",
                "run_id": "old-v2-opinion",
                "input_hash": "old-v2-input",
            },
            {
                "annotation_id": 127,
                "target_id": 11,
                "kind": "aspect",
                "value": ["fee"],
                "run_id": "old-v2-opinion",
                "input_hash": "old-v2-input",
            },
        ],
    )
    _add_comment_job(
        provider,
        comment_id=11,
        subject_code=OWN_CODE,
        status="done",
        input_hash="old-v2-input",
    )
    _add_comment_job(provider, comment_id=11, subject_code=OWN_CODE)

    product = next(row for row in provider.pool("d1")["list"] if row["code"] == OWN_CODE)

    assert product["commentFunnel"]["aiCompletedCount"] == 1
    assert product["attitude"]["negative"] == 1
    assert len(provider.evidence_for(OWN_CODE, "d1|sum", "negative", 5)) == 1


def test_explicitly_superseded_input_is_not_released():
    provider = make_sql_provider()
    _add_run(provider, "v3-llm-scope", "openai_compatible")
    add_annotations(
        provider,
        [
            {
                "annotation_id": 122,
                "target_id": 11,
                "kind": "relevance",
                "value": "relevant",
                "run_id": "v3-llm-scope",
                "input_hash": "old-source-input",
            },
            {
                "annotation_id": 123,
                "target_id": 11,
                "kind": "attitude",
                "value": "negative",
                "run_id": "v3-llm-scope",
                "input_hash": "old-source-input",
            },
        ],
    )
    _add_comment_job(
        provider,
        comment_id=11,
        subject_code=OWN_CODE,
        status="superseded",
        input_hash="old-source-input",
    )
    _add_comment_job(provider, comment_id=11, subject_code=OWN_CODE)

    funnel = next(
        row for row in provider.pool("d1")["list"] if row["code"] == OWN_CODE
    )["commentFunnel"]

    assert funnel["aiCompletedCount"] == 0
    assert funnel["relevantCount"] == 0
    assert funnel["analysisCoverage"] == 0.0
    product = next(row for row in provider.pool("d1")["list"] if row["code"] == OWN_CODE)
    assert product["attitude"] is None
    assert provider.evidence_for(OWN_CODE, "d1|sum", "negative", 5) is None


def test_matching_done_v3_job_releases_attitude_and_evidence():
    provider = make_sql_provider()
    _add_run(provider, "current-v3-run", "openai_compatible")
    current_hash = f"job-11-{OWN_CODE}"
    add_annotations(
        provider,
        [
            {
                "annotation_id": 128,
                "target_id": 11,
                "kind": "relevance",
                "value": "relevant",
                "run_id": "current-v3-run",
                "input_hash": current_hash,
            },
            {
                "annotation_id": 129,
                "target_id": 11,
                "kind": "attitude",
                "value": "negative",
                "run_id": "current-v3-run",
                "input_hash": current_hash,
            },
        ],
    )
    _add_comment_job(provider, comment_id=11, subject_code=OWN_CODE, status="done")

    product = next(row for row in provider.pool("d1")["list"] if row["code"] == OWN_CODE)

    assert product["commentFunnel"]["aiCompletedCount"] == 1
    assert product["attitude"]["negative"] == 1
    assert len(provider.evidence_for(OWN_CODE, "d1|sum", "negative", 5)) == 1


def test_v3_near_duplicate_propagation_counts_as_completed_ai_analysis():
    provider = make_sql_provider()
    _add_run(provider, "prop-v3-run", "propagated")
    add_annotations(
        provider,
        [{
            "annotation_id": 123,
            "target_id": 11,
            "kind": "relevance",
            "value": "relevant",
            "run_id": "prop-v3-run",
            "input_hash": "propagated-input",
        }],
    )

    funnel = next(
        row for row in provider.pool("d1")["list"] if row["code"] == OWN_CODE
    )["commentFunnel"]

    assert funnel["aiCompletedCount"] == 1
    assert funnel["relevantCount"] == 1


def test_pending_near_duplicate_is_rule_eligible_before_propagation():
    """The duplicate-cluster row is the rule stage's durable positive result."""

    provider = make_sql_provider()
    _add_run(provider, "rule-near-duplicate", "rule")
    add_annotations(
        provider,
        [{
            "annotation_id": 124,
            "target_id": 11,
            "kind": "duplicate_cluster",
            "value": {"of": 12},
            "run_id": "rule-near-duplicate",
        }],
    )

    funnel = next(
        row for row in provider.pool("d1")["list"] if row["code"] == OWN_CODE
    )["commentFunnel"]

    assert funnel["ruleEligibleCount"] == 1
    assert funnel["aiCompletedCount"] == 0
    assert funnel["pendingCount"] == 2


def test_partial_feed_never_reports_full_source_coverage_when_counts_happen_to_match():
    provider = make_sql_provider()
    with provider._engine.begin() as conn:
        conn.execute(
            update(feeds)
            .where(feeds.c.feed_id == 1)
            .values(comment_count=2, comment_coverage_status="partial")
        )
        conn.execute(
            update(feeds)
            .where(feeds.c.feed_id == 2)
            .values(comment_count=0, comment_coverage_status="complete")
        )
    provider._cache.clear()

    funnel = next(
        row for row in provider.pool("d1")["list"] if row["code"] == OWN_CODE
    )["commentFunnel"]

    assert funnel["platformCount"] == funnel["parsedCount"] == 2
    assert funnel["sourceCoverage"] is None


def test_3037_exclude_mode_drives_every_product_metric_from_the_same_parents():
    provider = make_sql_provider(broken_share=False)
    rows = [
        {
            "feed_id": 21,
            "code": "3037",
            "source_ticker": "03037.HK",
            "posted_at": datetime(2026, 8, 25, 2, 0),
            "feed_type": 1,
            "author_uid": "excluded-author",
            "author_name": "被筛掉作者",
            "title": None,
            "content": "$800000.HK$",
            "like_count": 100,
            "comment_count": 100,
            "image_count": 0,
            "share_count": 100,
            "raw_json_broken": False,
            "comment_coverage_status": "complete",
        },
        {
            "feed_id": 22,
            "code": "3037",
            "source_ticker": "03037.HK",
            "posted_at": datetime(2026, 8, 25, 3, 0),
            "feed_type": 1,
            "author_uid": "kept-author-1",
            "author_name": "保留作者一",
            "title": None,
            "content": "$03037.HK$ $800000.HK$",
            "like_count": 2,
            "comment_count": 10,
            "image_count": 0,
            "share_count": 1,
            "raw_json_broken": False,
            "comment_coverage_status": "complete",
        },
        {
            "feed_id": 23,
            "code": "3037",
            "source_ticker": "03037.HK",
            "posted_at": datetime(2026, 8, 25, 4, 0),
            "feed_type": 1,
            "author_uid": "kept-author-2",
            "author_name": "保留作者二",
            "title": None,
            "content": "$00700.HK$",
            "like_count": 3,
            "comment_count": 5,
            "image_count": 0,
            "share_count": 2,
            "raw_json_broken": False,
            "comment_coverage_status": "complete",
        },
    ]
    with provider._engine.begin() as conn:
        conn.execute(insert(feeds), rows)
        conn.execute(
            insert(feed_mentions),
            [
                {"feed_id": 21, "raw_ticker": "800000.HK", "market": "HK", "occurrences": 1},
                {"feed_id": 22, "raw_ticker": "03037.HK", "market": "HK", "occurrences": 1},
                {"feed_id": 22, "raw_ticker": "800000.HK", "market": "HK", "occurrences": 1},
                {"feed_id": 23, "raw_ticker": "00700.HK", "market": "HK", "occurrences": 1},
            ],
        )
        conn.execute(
            insert(comments),
            [
                {"comment_id": 21, "feed_id": 21, "author_uid": "excluded-commenter",
                 "content": "不应计入", "like_count": 50},
                {"comment_id": 22, "feed_id": 22, "author_uid": "kept-commenter",
                 "content": "应计入", "like_count": 1},
            ],
        )
    provider._cache.clear()

    product = next(row for row in provider.pool("d1")["list"] if row["code"] == "3037")

    assert product["mentions"] == 2
    assert product["comments"] == 15
    assert product["likes"] == 6
    assert product["shares"] == 3
    assert product["activeAccounts"] == 3
    assert product["commentFunnel"] == {
        "rawPlatformCount": 115,
        "platformCount": 15,
        "qualifyingFeedCount": 2,
        "filterExcludedPlatformCount": 100,
        "parsedCount": 1,
        "ruleEligibleCount": 0,
        "ruleExcludedCount": 0,
        "aiCompletedCount": 0,
        "relevantCount": 0,
        "needsContextCount": 0,
        "pendingCount": 1,
        "sourceCoverage": 0.0667,
        "analysisCoverage": 0.0,
    }
    day = next(row for row in provider.daily_for("3037") if row["iso"] == "2026-08-25")
    assert (day["comments"], day["active"]) == (15, 3)
    assert provider.ranks("d1")["map"]["3037"] == 1
    # The collection control plane remains an unfiltered source audit.
    assert provider.collection_metadata()["platformCommentCount"] == 132
    assert provider.collection_metadata()["parsedCommentCount"] == 6


def test_default_exact_mode_rejects_a_parent_without_its_own_cashtag():
    provider = make_sql_provider(broken_share=False)
    with provider._engine.begin() as conn:
        conn.execute(
            insert(feeds).values(
                feed_id=24,
                code=OWN_CODE,
                source_ticker="03033.HK",
                posted_at=datetime(2026, 8, 25, 5, 0),
                feed_type=1,
                author_uid="wrong-ticker-author",
                author_name="其他标的作者",
                title=None,
                content="$03032.HK$",
                like_count=200,
                comment_count=99,
                image_count=0,
                share_count=50,
                raw_json_broken=False,
                comment_coverage_status="complete",
            )
        )
        conn.execute(
            insert(feed_mentions).values(
                feed_id=24,
                raw_ticker="03032.HK",
                market="HK",
                occurrences=1,
            )
        )
        conn.execute(
            insert(comments).values(
                comment_id=24,
                feed_id=24,
                author_uid="wrong-ticker-commenter",
                content="回复也不能自救",
                like_count=100,
            )
        )
    provider._cache.clear()

    product = next(row for row in provider.pool("d1")["list"] if row["code"] == OWN_CODE)

    assert product["mentions"] == 2
    assert product["comments"] == 5
    assert product["likes"] == 13
    assert product["commentFunnel"]["rawPlatformCount"] == 104
    assert product["commentFunnel"]["platformCount"] == 5
    assert product["commentFunnel"]["filterExcludedPlatformCount"] == 99
    assert product["commentFunnel"]["parsedCount"] == 2


def test_filter_metrics_are_unavailable_until_versioned_backfill_is_ready():
    provider = make_sql_provider()
    with provider._engine.begin() as conn:
        conn.execute(delete(meta_kv).where(meta_kv.c.k == COMMENT_FILTER_READY_META_KEY))
    provider.refresh()

    assert provider.build_range("d1") is not None
    assert provider.pool("d1") is None
    assert provider.ranks("d1") is None
    assert provider.benchmark(OWN_CODE, "d1") is None
    assert provider.heat_series_for(OWN_CODE, "d1") is None
    assert provider.daily_for(OWN_CODE) is None


def test_unresolved_source_ticker_keeps_filter_metrics_unavailable():
    provider = make_sql_provider()
    with provider._engine.begin() as conn:
        conn.execute(
            update(feeds).where(feeds.c.feed_id == 1).values(source_ticker=None)
        )
    provider._cache.clear()

    assert provider.pool("d1") is None
    assert provider.ranks("d1") is None


def test_stale_filter_config_digest_keeps_filter_metrics_unavailable():
    provider = make_sql_provider()
    with provider._engine.begin() as conn:
        conn.execute(
            update(meta_kv)
            .where(meta_kv.c.k == COMMENT_FILTER_DIGEST_META_KEY)
            .values(v="stale-digest")
        )
    provider.refresh()

    assert provider.pool("d1") is None


def test_comment_funnel_hides_an_old_cross_product_annotation():
    """Replies inherit the parent section; old cross-product rows stay audit-only."""

    provider = make_sql_provider()
    routed_code = "3037"
    _add_run(provider, "routed-llm", "openai_compatible")
    add_annotations(
        provider,
        [
            {
                "annotation_id": 151,
                "target_id": 13,
                "subject_code": routed_code,
                "kind": "relevance",
                "value": "relevant",
                "run_id": "routed-llm",
            },
            {
                "annotation_id": 152,
                "target_id": 13,
                "subject_code": routed_code,
                "kind": "attitude",
                "value": "negative",
                "run_id": "routed-llm",
            },
        ],
    )

    product = next(row for row in provider.pool("d1")["list"] if row["code"] == routed_code)

    # The parent belongs to PEER_CODE, so neither its source counter nor its old
    # 3037 annotation may leak into 3037's product contract.
    assert product["comments"] == 0
    assert product["commentFunnel"] == {
        "rawPlatformCount": 0,
        "platformCount": 0,
        "qualifyingFeedCount": 0,
        "filterExcludedPlatformCount": 0,
        "parsedCount": 0,
        "ruleEligibleCount": 0,
        "ruleExcludedCount": 0,
        "aiCompletedCount": 0,
        "relevantCount": 0,
        "needsContextCount": 0,
        "pendingCount": 0,
        "sourceCoverage": 1.0,
        "analysisCoverage": 1.0,
    }
    assert product["attitude"] is None


def test_comment_funnel_hides_an_old_cross_product_pending_job():
    provider = make_sql_provider()
    routed_code = "3037"
    _add_comment_job(provider, comment_id=13, subject_code=routed_code)

    product = next(row for row in provider.pool("d1")["list"] if row["code"] == routed_code)

    assert product["commentFunnel"] == {
        "rawPlatformCount": 0,
        "platformCount": 0,
        "qualifyingFeedCount": 0,
        "filterExcludedPlatformCount": 0,
        "parsedCount": 0,
        "ruleEligibleCount": 0,
        "ruleExcludedCount": 0,
        "aiCompletedCount": 0,
        "relevantCount": 0,
        "needsContextCount": 0,
        "pendingCount": 0,
        "sourceCoverage": 1.0,
        "analysisCoverage": 1.0,
    }


def test_current_irrelevant_verdict_hides_an_older_attitude_from_product_totals():
    provider = add_annotations(
        make_sql_provider(),
        [
            {
                "annotation_id": 201,
                "target_id": 11,
                "kind": "relevance",
                "value": "relevant",
            },
            {
                "annotation_id": 202,
                "target_id": 11,
                "kind": "attitude",
                "value": "positive",
            },
            {
                "annotation_id": 203,
                "target_id": 11,
                "kind": "relevance",
                "value": "irrelevant",
                "supersedes_id": 201,
                "input_hash": "relevance-flipped",
            },
        ],
    )

    product = next(row for row in provider.pool("d1")["list"] if row["code"] == OWN_CODE)

    assert product["attitude"] == {
        "positive": 0,
        "negative": 0,
        "neutral": 0,
        "sampleSufficient": False,
    }


def test_current_irrelevant_verdict_gates_evidence_themes_and_topic_aggregates():
    provider = add_annotations(
        make_sql_provider(),
        [
            {"annotation_id": 301, "target_id": 11, "kind": "relevance", "value": "relevant"},
            {"annotation_id": 302, "target_id": 11, "kind": "attitude", "value": "negative"},
            {"annotation_id": 303, "target_id": 11, "kind": "aspect", "value": ["fee"]},
            {
                "annotation_id": 304,
                "target_id": 11,
                "kind": "market_direction",
                "value": "bearish",
            },
            {
                "annotation_id": 305,
                "target_id": 11,
                "kind": "relevance",
                "value": "irrelevant",
                "supersedes_id": 301,
                "input_hash": "relevance-flipped-for-all-read-paths",
            },
        ],
    )

    assert provider.evidence_for(OWN_CODE, "d1|sum", "negative", 5) == []
    assert provider.themes_for(OWN_CODE, "d1") == {"positive": [], "negative": []}
    assert provider.topics_for(OWN_CODE, "d1") == []


def test_official_attribution_ignores_anchor_and_prefers_body_mentions():
    post = make_sql_provider().official_posts("d1")[0]

    assert post["sourceAnchorCode"] == OWN_CODE
    assert [item["code"] for item in post["attributedProducts"]] == [PEER_CODE]
    assert post["attributionStatus"] == "explicit"
    assert post["attributedCamp"] == "competitor"
    # 旧字段暂留作兼容，但也必须消费归属结果，不能让挂载标的从旁路漏回来。
    assert post["code"] == PEER_CODE
    assert [item["code"] for item in post["mentioned"]] == [PEER_CODE]


def test_feedback_posts_never_attribute_huaxia_content_to_source_anchor_3068():
    provider = make_sql_provider()
    huaxia = next(item for item in provider._officials if item["short"] == "华夏")
    _add_official_feed(
        provider,
        feed_id=117149481304068,
        author_name=huaxia["full"],
        content="比特币和以太币近期成交活跃，市场关注度上升。",
        anchor_code="3068",
    )
    _add_official_feed(
        provider,
        feed_id=117125636685828,
        author_name=huaxia["full"],
        content="华夏比特币ETF及华夏以太币ETF为投资者提供虚拟资产配置工具。",
        anchor_code="3068",
    )

    by_id = {post["id"]: post for post in provider.official_posts("d1")}
    inferred = by_id["of-117149481304068"]
    explicit = by_id["of-117125636685828"]

    assert inferred["sourceAnchorCode"] == "3068"
    assert inferred["attributionStatus"] == "inferred"
    assert [item["code"] for item in inferred["attributedProducts"]] == ["3042", "3046"]
    assert explicit["attributionStatus"] == "explicit"
    assert [item["code"] for item in explicit["attributedProducts"]] == ["3042", "3046"]
    assert all(
        "3068" not in {item["code"] for item in post["attributedProducts"]}
        for post in (inferred, explicit)
    )


def test_official_attribution_accepts_cashtag_but_leaves_ambiguous_asset_text_unattributed():
    provider = make_sql_provider()
    huaxia = next(item for item in provider._officials if item["short"] == "华夏")
    _add_official_feed(
        provider,
        feed_id=901,
        author_name=huaxia["full"],
        content="关注 $03042.HK$ 的最新表现。",
        anchor_code="3068",
    )
    _add_official_feed(
        provider,
        feed_id=902,
        author_name=huaxia["full"],
        content="虚拟资产 ETF 市场近期波动较大。",
        anchor_code="3068",
    )
    _add_official_feed(
        provider,
        feed_id=903,
        author_name=huaxia["full"],
        content="2026 年指数在 3042 点附近震荡。",
        anchor_code="3068",
    )
    _add_official_feed(
        provider,
        feed_id=904,
        author_name=huaxia["full"],
        content="关注 $产品名 (03042.HK)$ 的最新表现。",
        anchor_code="3068",
    )

    by_id = {post["id"]: post for post in provider.official_posts("d1")}
    assert by_id["of-901"]["attributionStatus"] == "explicit"
    assert [item["code"] for item in by_id["of-901"]["attributedProducts"]] == ["3042"]
    assert by_id["of-902"]["attributionStatus"] == "unattributed"
    assert by_id["of-902"]["attributedProducts"] == []
    assert by_id["of-902"]["attributedCamp"] == "none"
    assert by_id["of-903"]["attributionStatus"] == "unattributed"
    assert by_id["of-903"]["attributedProducts"] == []
    assert by_id["of-904"]["attributionStatus"] == "explicit"
    assert [item["code"] for item in by_id["of-904"]["attributedProducts"]] == ["3042"]


def test_official_attribution_matches_a_unique_product_name_across_chinese_forms():
    provider = make_sql_provider()
    bosera = next(item for item in provider._officials if item["short"] == "博时")
    _add_official_feed(
        provider,
        feed_id=905,
        author_name=bosera["full"],
        content="博时港元货币市场ETF发布最新月报。",
        anchor_code="3068",
    )

    post = next(post for post in provider.official_posts("d1") if post["id"] == "of-905")

    assert post["sourceAnchorCode"] == "3068"
    assert post["attributionStatus"] == "explicit"
    assert [item["code"] for item in post["attributedProducts"]] == ["3152"]


def test_etf_mentions_use_official_attribution_not_source_anchor():
    mentions_result = make_sql_provider().etf_mentions_for("恒生投资", "d1")

    assert [item["code"] for item in mentions_result["list"]] == [PEER_CODE]
    assert mentions_result["postCount"] == 1


