"""Feature combinations exercise public behavior and retained evidence, using synthetic data."""

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentboard.adapters.codex import CodexAdapter
from agentboard.api import create_app
from agentboard.config import Settings
from agentboard.domain import RawLine, Session
from agentboard.features import BUILTIN_FEATURES, DEFAULT_FEATURES
from agentboard.store import Store

FIXTURE = Path(__file__).parents[2] / "examples/fixtures/codex-session.jsonl"
ALL = {feature.name for feature in BUILTIN_FEATURES}
ROUTES = {
    "import": ("POST", "/api/v1/import/codex"),
    "otlp_logs": ("POST", "/v1/logs"),
    "otlp_traces": ("POST", "/v1/traces"),
    "raw_archive": ("GET", "/api/v1/sessions/any/raw-imports"),
    "field_lineage": ("GET", "/api/v1/sessions/any/lineage"),
    "unified_timeline": ("GET", "/api/v1/sessions/any/unified"),
    "parallel_groups": ("GET", "/api/v1/sessions/any/parallel-groups"),
    "token_usage": ("GET", "/api/v1/pricing"),
    "inputs": ("GET", "/api/v1/sessions/any/inputs"),
    "export": ("GET", "/api/v1/sessions/any/export"),
    "classification": ("GET", "/api/v1/classification-schema"),
    "replay": ("POST", "/api/v1/sessions/any/replay"),
    "native_resume": ("POST", "/api/v1/sessions/any/codex-plan"),
}


def without(name):
    disabled = {name}
    for feature in BUILTIN_FEATURES:
        if feature.requires & disabled:
            disabled.add(feature.name)
    return ALL - disabled


@pytest.mark.parametrize("feature", sorted(ALL))
def test_disabled_features_are_not_mounted(tmp_path, feature):
    settings = Settings(database=str(tmp_path / "features.db"), features=without(feature))
    with TestClient(create_app(settings)) as client:
        method, path = ROUTES[feature]
        assert client.request(method, path).status_code == 404
        config = client.get("/api/v1/config").json()
        assert feature not in config["features"]
        assert not next(row for row in config["feature_catalog"] if row["name"] == feature)["enabled"]
        # Mandatory UI/API remain available, including when ingestion is off.
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/api/v1/sessions").status_code == 200


def test_empty_allowlist_browses_existing_data_without_optional_routes_or_gateway(tmp_path):
    database = str(tmp_path / "existing.db")
    store = Store(database)
    imported = store.ingest(CodexAdapter().parse(io.StringIO(FIXTURE.read_text())))
    sid = imported["session_ids"][0]
    with TestClient(create_app(Settings(database=database, features=set()))) as client:
        paths = client.get("/openapi.json").json()["paths"]
        assert set(paths) == {
            "/health", "/api/v1/config", "/api/v1/sessions", "/api/v1/sessions/{sid}",
            "/api/v1/sessions/{sid}/events", "/api/v1/sessions/{sid}/stats",
        }
        assert not hasattr(client.app.state, "gateway")
        assert client.get(f"/api/v1/sessions/{sid}/events").json()["items"]
        assert client.get(f"/api/v1/sessions/{sid}/stats").json()["counts"]
        assert client.get("/api/v1/config").json()["classification_taxonomy"] is None
    assert store.raw_imports(sid)  # Disabling a capability does not delete existing evidence.


@pytest.mark.parametrize("signal", ["logs", "traces"])
def test_otlp_signals_can_be_enabled_independently(tmp_path, signal):
    with TestClient(create_app(Settings(database=str(tmp_path / "signals.db"), features={f"otlp_{signal}"}))) as client:
        assert client.post(f"/v1/{signal}", json={}).status_code == 200
        other = "logs" if signal == "traces" else "traces"
        assert client.post(f"/v1/{other}", json={}).status_code == 404


def test_export_switch_covers_derived_and_raw_downloads(tmp_path):
    with TestClient(create_app(Settings(database=str(tmp_path / "exports.db"), features=without("export")))) as client:
        sid = client.post("/api/v1/import/codex", content=FIXTURE.read_bytes()).json()["session_ids"][0]
        for suffix in ("export", "raw", "usage/export"):
            assert client.get(f"/api/v1/sessions/{sid}/{suffix}").status_code == 404
        assert client.get(f"/api/v1/sessions/{sid}/usage").status_code == 200
        assert client.get(f"/api/v1/sessions/{sid}/raw-imports").status_code == 200


@pytest.mark.parametrize("raw,lineage", [(False, False), (True, False), (True, True), (False, True)])
def test_raw_is_mandatory_and_only_lineage_persistence_is_optional(tmp_path, raw, lineage):
    features = {"import"} | ({"raw_archive"} if raw else set()) | ({"field_lineage"} if lineage else set())
    with TestClient(create_app(Settings(database=str(tmp_path / "retention.db"), features=features))) as client:
        response = client.post("/api/v1/import/codex", content=FIXTURE.read_bytes())
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["raw_import_ids"]
        store = client.app.state.store
        with store.connect() as db:
            for table in ("raw_imports", "raw_lines", "event_raw_sources"):
                assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > 0
            for table in ("event_field_sources", "session_field_sources"):
                assert bool(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]) == lineage
        baseline = Store(str(tmp_path / "baseline.db"))
        baseline.ingest(CodexAdapter().parse(io.StringIO(FIXTURE.read_text())))
        assert list(store.export(result["session_ids"][0])) == list(baseline.export(result["session_ids"][0]))


def test_reenable_lineage_backfills_explicit_reimport(tmp_path):
    database = str(tmp_path / "reenable.db")
    store = Store(database, retain_lineage=False)
    result = store.ingest(CodexAdapter().parse(io.StringIO(FIXTURE.read_text())))
    sid = result["session_ids"][0]
    assert store.raw_imports(sid)
    assert not store.field_lineage(sid)["fields"] or not any(f["available"] for f in store.field_lineage(sid)["fields"].values())
    enabled = Store(database)
    result = enabled.ingest(CodexAdapter().parse(io.StringIO(FIXTURE.read_text())))
    assert result["inserted_events"] == 0
    assert result["raw_import_ids"]
    assert b"".join(enabled.export_raw(sid)) == FIXTURE.read_bytes()
    assert enabled.field_lineage(sid)["fields"]


def test_normalized_archive_rolls_back_incomplete_internal_stream(tmp_path):
    store = Store(str(tmp_path / "incomplete.db"))
    with pytest.raises(ValueError, match="Incomplete"):
        store.ingest([RawLine(1, "{}\n"), Session(id="partial", started_at="2026-09-07T00:00:00Z")])
    assert store.list_sessions()["total"] == 0


def test_default_profile_is_tracing_only(tmp_path):
    with TestClient(create_app(Settings(database=str(tmp_path / "default.db")))) as client:
        assert set(client.get("/api/v1/config").json()["features"]) == DEFAULT_FEATURES
        assert not hasattr(client.app.state, "gateway")


@pytest.mark.parametrize("feature", BUILTIN_FEATURES, ids=lambda f: f.name)
def test_single_capability_and_dependencies_have_only_owned_endpoints(tmp_path, feature):
    enabled = {feature.name} | feature.requires
    app = create_app(Settings(database=str(tmp_path / "single.db"), features=enabled, model_mode="dummy"))
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()["paths"]
        for name, (method, path) in ROUTES.items():
            path = path.replace("/any/", "/{sid}/").replace("/import/codex", "/import/{agent}")
            assert (method.lower() in schema.get(path, {})) == (name in enabled), name
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/sessions").json()["items"] == []


def test_unified_does_not_compute_disabled_parallel_analysis(tmp_path, monkeypatch):
    import agentboard.parallel

    def forbidden(*args, **kwargs):
        pytest.fail("Disabled parallel analysis ran")

    monkeypatch.setattr(agentboard.parallel, "parallel_groups", forbidden)
    with TestClient(create_app(Settings(database=str(tmp_path / "unified.db"),
                                       features={"import", "unified_timeline"}))) as client:
        sid = client.post("/api/v1/import/codex", content=FIXTURE.read_bytes()).json()["session_ids"][0]
        response = client.get(f"/api/v1/sessions/{sid}/unified")
        assert response.status_code == 200
        assert response.json()["items"]
        assert response.json()["parallel"] is None


@pytest.mark.parametrize("features", [{"import"}, {"import", "raw_archive"}])
def test_disabled_lineage_does_not_compute_adapter_mappings(tmp_path, monkeypatch, features):
    from agentboard.adapters import codex

    def forbidden():
        pytest.fail("Disabled Codex lineage builder initialized")

    monkeypatch.setattr(codex, "CodexLineage", forbidden)
    with TestClient(create_app(Settings(database=str(tmp_path / "no-lineage.db"), features=features))) as client:
        response = client.post("/api/v1/import/codex", content=FIXTURE.read_bytes())
        assert response.status_code == 200, response.text
        sid = response.json()["session_ids"][0]
        assert len(client.get(f"/api/v1/sessions/{sid}/events").json()["items"]) == 24
