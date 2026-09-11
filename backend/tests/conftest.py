import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
# tests/ 自己也要在 path 上：sql_fixture.py 是被两个测试文件共用的普通模块，
# 不靠 pytest 的 rootdir 推断（那个推断会随调用目录变）。
sys.path.insert(0, _HERE)

import providers  # noqa: E402
from app import create_app  # noqa: E402
from providers import reset_provider  # noqa: E402
from providers.demo import DemoProvider  # noqa: E402
from sql_fixture import make_sql_provider  # noqa: E402


def _client(provider):
    """绑定到某个 provider 实例的 test client。

    **provider 在每次请求前重新装一遍**，不是在建 client 的时候装一次。provider 是
    进程单例（`providers._instance`），而一条测试可以同时要 `client` 和 `sql_client` ——
    在 fixture 阶段装的话，后建的那个 client 会把先建的那个的 provider 顶掉，两个 client
    从此指向同一个 provider。

    这不是假设：`test_provider_parity.py` 第一版就是这么写的，于是它拿 sql 的返回和
    sql 的返回比形状，**一条差异都发现不了**，还绿得很自信。一条永远为真的断言比没有
    断言更糟 —— 没有断言至少不会让人以为查过了。
    """
    app = create_app()
    app.config.update(TESTING=True)

    @app.before_request
    def _install_provider():
        providers._instance = provider

    return app.test_client()


@pytest.fixture
def client():
    """demo provider（fixtures/demo/，逐字来自设计源）上的 client。"""
    reset_provider()
    with _client(DemoProvider()) as c:
        yield c
    reset_provider()


@pytest.fixture
def sixstate_client(monkeypatch):
    """接在六态场景数据上的 client（fixtures/sixstate/，见 fixtures/make_sixstate.py）。

    演示数据字段永远齐全，六态里有几态在它身上一次都不会发生。要断言「null 没被渲染成
    0」就得先有一个 null，这个 fixture 负责提供。provider 是进程单例，用完必须重置，
    否则六态数据会漏给后面的测试。
    """
    monkeypatch.setenv("DEMO_SCENARIO", "sixstate")
    reset_provider()
    # DemoProvider 在 __init__ 里读 DEMO_SCENARIO，所以要在 setenv 之后构造。
    with _client(DemoProvider()) as c:
        yield c
    reset_provider()


@pytest.fixture
def sql_provider():
    """内存瘦库上的 `sql` provider（构造见 tests/sql_fixture.py）。"""
    return make_sql_provider()


@pytest.fixture
def sql_client():
    """跑在 `sql` provider 上的 HTTP client。

    在此之前，22 个端点**只**在 demo provider 下被测过 —— 而 demo 读的是 fixture，
    fixture 里字段永远齐全。于是「sql 下这个端点返回的形状不对」这类问题一条测试都碰不到，
    只能等有人手工起一遍 `DATA_PROVIDER=sql` 才看得见（`etfMentionsFor` 的 `own` 发成
    计数就是这么漏掉的）。

    不走 `DATA_PROVIDER` 环境变量：那条路会去连 `RADAR_DB_URL` 指的真库
    （4.9 GB、带 PII、不进 git），测试不该依赖它。
    """
    reset_provider()
    with _client(make_sql_provider()) as c:
        yield c
    reset_provider()
