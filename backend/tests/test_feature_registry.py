"""Static declarations, lazy imports, route ownership, and lifecycle guarantees."""

import json
import re
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from agentboard.adapters.catalog import AdapterDefinition, validate_adapters
from agentboard.api import create_app, validate_router
from agentboard.cli import main
from agentboard.config import Settings
from agentboard.features import BUILTIN_FEATURES, DEFAULT_FEATURES, Catalog, FeatureDefinition
from agentboard.runtime import Runtime


def test_declarations_are_immutable():
    requires = {"inputs"}
    feature = FeatureDefinition("example", "Example", requires)
    requires.clear()
    assert feature.requires == frozenset({"inputs"})
    with pytest.raises(FrozenInstanceError):
        feature.name = "changed"
    with pytest.raises(ValueError, match="Duplicate"):
        Catalog([feature, feature])


@pytest.mark.parametrize("definitions,enabled,error", [
    ([FeatureDefinition("example", "Example")], {"typo"}, "Unknown features"),
    ([FeatureDefinition("example", "Example", {"missing"})], set(), "Unknown feature dependency"),
    ([FeatureDefinition("one", "One", {"two"}), FeatureDefinition("two", "Two")], {"one"}, "requires: two"),
    ([FeatureDefinition("one", "One", {"two"}), FeatureDefinition("two", "Two", {"one"})], set(), "cycle"),
])
def test_catalog_validation(definitions, enabled, error):
    with pytest.raises(ValueError, match=error):
        Catalog(definitions).validate(enabled)


def test_adapter_ownership():
    invalid = AdapterDefinition("test", frozenset({"missing"}), lambda _: None)
    with pytest.raises(ValueError, match="ownership"):
        validate_adapters({"import"}, [invalid])
    valid = AdapterDefinition("test", frozenset({"import"}), lambda _: None)
    with pytest.raises(ValueError, match="duplicate"):
        validate_adapters({"import"}, [valid, valid])


def test_dependencies_activate_before_dependents():
    catalog = Catalog([FeatureDefinition("replay", "Replay", {"inputs"}), FeatureDefinition("inputs", "Inputs")])
    assert [f.name for f in catalog.enabled_features({"replay", "inputs"})] == ["inputs", "replay"]


def test_frontend_capability_names_exist_in_catalog():
    frontend = Path(__file__).parents[2] / "frontend"
    markup = (frontend / "index.html").read_text()
    script = (frontend / "app.js").read_text()
    names = {name for attribute in re.findall(r'data-feature="([^"]+)"', markup) for name in attribute.split()}
    names.update(re.findall(r"hasFeature\('([a-z_]+)'", script))
    assert names <= {f.name for f in BUILTIN_FEATURES}


def test_defaults_and_allowlist(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTBOARD_FEATURES", raising=False)
    assert Settings().features == DEFAULT_FEATURES
    monkeypatch.setenv("AGENTBOARD_FEATURES", " import, inputs ,export ")
    assert Settings().features == {"import", "inputs", "export"}
    path = tmp_path / "settings.toml"
    path.write_text("features = []\n")
    assert not Settings.from_file(path).features
    assert Catalog().adapters(set()) == ()
    assert Catalog().adapters({"import"}) == ("codex",)
    assert Catalog().validate({"field_lineage", "token_usage"}) == {"field_lineage", "token_usage"}


def test_removed_plugins_cannot_execute(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBOARD_PLUGINS", "module_that_must_not_be_imported")
    with pytest.raises(ValueError, match="no longer supported"):
        Settings()
    assert "module_that_must_not_be_imported" not in sys.modules
    monkeypatch.delenv("AGENTBOARD_PLUGINS")
    path = tmp_path / "settings.toml"
    path.write_text('plugins = ["module_that_must_not_be_imported"]\n')
    with pytest.raises(ValueError, match="Unknown settings"):
        Settings.from_file(path)


def test_invalid_settings_before_database(tmp_path):
    path = tmp_path / "unopened.db"
    with pytest.raises(ValueError, match="Unknown features"):
        Runtime(Settings(database=str(path), features={"typo"}))
    assert not path.exists()


def test_catalog_and_empty_app_do_not_import_optional_modules(tmp_path):
    script = """
import sys
from agentboard.cli import main
sys.argv = ['agentboard', '--database', sys.argv[1], 'features']
main()
assert 'agentboard.store' not in sys.modules
assert 'agentboard.adapters.codex' not in sys.modules
from agentboard.api import create_app
from agentboard.config import Settings
app = create_app(Settings(database=sys.argv[2], features=set()))
for name in ('agentboard.models', 'openai', 'agentboard.otlp', 'google.protobuf', 'agentboard.adapters.codex'):
    assert name not in sys.modules, name
app.state.runtime.close()
"""
    # Script replaces argv; preserve the app path separately.
    script = script.replace("sys.argv = ['agentboard'", "app_path = sys.argv[2]\nsys.argv = ['agentboard'").replace('database=sys.argv[2]', 'database=app_path')
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path/'catalog.db'), str(tmp_path/'app.db')],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)) == len(BUILTIN_FEATURES)
    assert not (tmp_path/'catalog.db').exists()


def test_core_rejects_route_collisions_and_lifecycle():
    router = APIRouter()
    router.add_api_route('/api/v1/sessions/{sid}/inputs', lambda: {})
    with pytest.raises(ValueError, match="duplicate"):
        validate_router('inputs', router, {('GET', '/api/v1/sessions/{sid}/inputs')})
    with pytest.raises(ValueError, match="Unowned"):
        validate_router('export', router, set())
    router = APIRouter(on_startup=[lambda: None])
    with pytest.raises(ValueError, match="lifecycle"):
        validate_router('inputs', router, set())


def test_shutdown_and_failed_construction_close_resources_once(tmp_path, monkeypatch):
    calls = []

    @contextmanager
    def resource():
        calls.append('open')
        try:
            yield
        finally:
            calls.append('close')

    class ObservedRuntime(Runtime):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._resources.enter_context(resource())

    monkeypatch.setattr('agentboard.api.Runtime', ObservedRuntime)
    settings = Settings(database=str(tmp_path/'life.db'), features=set())
    app = create_app(settings)
    with TestClient(app):
        assert calls == ['open']
    app.state.runtime.close()
    assert calls == ['open', 'close']
    monkeypatch.setattr('agentboard.api.frontend_directory', lambda: (_ for _ in ()).throw(ValueError('setup failed')))
    with pytest.raises(ValueError, match='setup failed'):
        create_app(settings)
    assert calls == ['open', 'close', 'open', 'close']


@pytest.mark.parametrize("features, command, error", [
    ("", ["import", "missing.jsonl"], "import feature is disabled"),
    ("", ["export", "missing"], "export feature is disabled"),
    ("export", ["export", "missing", "--raw"], "raw_archive feature is disabled"),
    ("export", ["export", "missing", "--inputs-only"], "inputs feature is disabled"),
    ("export", ["export", "missing", "--import-id", "1"], "--import-id requires --raw"),
    ("", ["classify", "missing"], "classification feature is disabled"),
    ("inputs,replay", ["resume", "missing", "input", "--prompt", "hello"], "native_resume feature is disabled"),
    ("import", ["import", "missing.jsonl", "--agent", "unknown"], "Unknown or disabled adapter: unknown"),
    ("typo", ["repair-otlp-sessions"], "Unknown features: typo"),
])
def test_cli_rejects_disabled_operations_before_database_creation(monkeypatch, capsys, tmp_path,
                                                               features, command, error):
    database = tmp_path / "unopened.db"
    monkeypatch.setenv("AGENTBOARD_FEATURES", features)
    monkeypatch.setattr(sys, "argv", ["agentboard", "--database", str(database), *command])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert error in capsys.readouterr().err
    assert not database.exists()
