import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from providers import reset_provider  # noqa: E402


def _client():
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture
def client():
    with _client() as c:
        yield c


@pytest.fixture
def sixstate_client(monkeypatch):
    """接在六态场景数据上的 client（fixtures/sixstate/，见 fixtures/make_sixstate.py）。

    演示数据字段永远齐全，六态里有几态在它身上一次都不会发生。要断言「null 没被渲染成
    0」就得先有一个 null，这个 fixture 负责提供。provider 是进程单例，用完必须重置，
    否则六态数据会漏给后面的测试。
    """
    monkeypatch.setenv("DEMO_SCENARIO", "sixstate")
    reset_provider()
    with _client() as c:
        yield c
    reset_provider()
