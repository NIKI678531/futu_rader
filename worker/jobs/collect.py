"""Compatibility entry point for the automatic MarketInsight refresh.

对象是 PRD §3.8 的产品池：61 只自家（CSOP 南方东英）＋ 59 只竞品，客户维护固定对位映射。
采集帖子与可取得的评论正文。评论分页可能只覆盖一部分，因此平台评论总数与实际正文数
分别保存；AI 只读取已抓到的正文，绝不把部分覆盖描述成完整评论集。

生产数据先由 Airflow 采集进 MarketInsight MySQL，本任务只使用只读账号做 keyset 增量同步。
实现位于 ``collection.FutuRefresh``；本模块保留旧入口，避免已有运维命令失效。

写入原则（CLAUDE.md 铁律 1、2）：
- 只落原始字段，不在这里做任何聚合或口径计算 —— 那是 backend/core/ 的事。
- 采集不到的字段落 NULL，**绝不落 0**：0 是「已取得数据且确实为零」的专用值。
- 平台计数字段（赞/评论数/转发/浏览）取发布后约 24 小时的值（PRD §3.10）。
"""

from jobs.refresh import main as refresh_main


def run(argv=None):
    return refresh_main(["sync", *(argv or [])])


if __name__ == "__main__":
    raise SystemExit(run())
