import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scheduler import build_scheduler  # noqa: E402


def test_heartbeat_job_is_registered():
    jobs = build_scheduler(interval_seconds=30).get_jobs()
    assert [j.id for j in jobs] == ["heartbeat"]


def test_interval_comes_from_the_argument():
    job = build_scheduler(interval_seconds=30).get_jobs()[0]
    assert job.trigger.interval.total_seconds() == 30


def test_interval_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setenv("COLLECT_INTERVAL_SECONDS", "120")
    job = build_scheduler().get_jobs()[0]
    assert job.trigger.interval.total_seconds() == 120
