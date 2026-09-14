from unittest.mock import Mock

import pytest

import providers


@pytest.fixture
def provider_factories(monkeypatch):
    factories = {name: Mock() for name in ("sql", "demo", "mysql")}
    monkeypatch.setattr(providers, "_PROVIDERS", factories)
    monkeypatch.setattr(providers, "_instance", None)
    monkeypatch.delenv("DATA_PROVIDER", raising=False)
    return factories


def test_default_provider_is_sql(provider_factories):
    assert providers.get_provider() is provider_factories["sql"].return_value
    provider_factories["sql"].assert_called_once_with()
    provider_factories["demo"].assert_not_called()


def test_sql_failure_does_not_fall_back_to_demo(provider_factories):
    provider_factories["sql"].side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        providers.get_provider()

    provider_factories["demo"].assert_not_called()


@pytest.mark.parametrize("name", ["sql", "demo", "mysql"])
def test_explicit_provider_selection(monkeypatch, provider_factories, name):
    monkeypatch.setenv("DATA_PROVIDER", name)

    assert providers.get_provider() is provider_factories[name].return_value
    provider_factories[name].assert_called_once_with()