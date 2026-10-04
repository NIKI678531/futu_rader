# Exact comment eligibility and official product attribution

Status: accepted

> **2026-09-29 修订**：产品评论资格与评论量口径已由 [ADR-0029](0029-parent-feed-comment-qualification.md) 取代。回复不再自救或跨产品改路由，平台原始量不再作为产品主指标。本 ADR 的官号归属规则继续有效。

Platform discussion areas are provenance, not evidence that their posts or comments discuss the anchored ETF. Product-comment analysis uses the parent-feed policy defined by ADR-0029: a reply can inherit only its real discussion-area anchor after that parent qualifies, and can never override inheritance through its own text. Other ETF tickers in the parent are filter evidence, not permission to copy replies across products. Official-account posts are attributed independently from their source anchor, using explicit body evidence first and issuer plus a uniquely mapped asset class second; ambiguous posts remain unattributed.

## Consequences

- Filtered platform comment volume is the product popularity metric; pre-filter platform volume remains audit-only, while confirmed relevant comments are a separate, coverage-qualified metric.
- Anchor and body mentions remain separate facts, including when both point to the same product.
- Parent-filter exclusions are auditable; cross-product reroutes are not produced.
- `comment-product-v3` eligible jobs go directly to the quality-gated LLM. The older local student remains available only for pre-v3 compatibility because its outputs are not covered by the v3 400-row gold report.
- The external `opinion-radar` project is a reference only for cashtag input normalization and deterministic filtering; its NLP and business modules are not dependencies.
