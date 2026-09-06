import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentboard.api import create_app
from agentboard.cli import main
from agentboard.config import Settings
from agentboard.domain import Session
from agentboard.snapshot import snapshot_database
from agentboard.store import Store

ROOT = Path(__file__).parents[2]
DEV = ROOT / 'config/dev.toml'


def test_dev_settings_override_live_environment_and_resolve_paths(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENTBOARD_DATABASE', str(tmp_path / 'live.db'))
    monkeypatch.setenv('AGENTBOARD_MODEL_MODE', 'local')
    monkeypatch.setenv('AGENTBOARD_PLUGINS', 'live_plugin')
    settings = Settings.from_file(DEV)
    assert settings.port == 4319 and settings.host == '127.0.0.1'
    assert settings.database == str(ROOT / '.agentboard/dev.db')
    assert settings.environment == 'dev' and settings.model_mode == 'dummy'
    assert not settings.otlp_enabled and settings.plugins == ()
    assert settings.reload and not Settings().reload
    assert Settings().port == 4318 and Settings().otlp_enabled


@pytest.mark.parametrize('contents', ['porrt = 4319', 'port = "4319"', 'port = 0', 'otlp_enabled = "false"'])
def test_bad_config_fails_before_opening_database(tmp_path, contents):
    path = tmp_path / 'dev.toml'
    path.write_text(contents)
    with pytest.raises(ValueError):
        Settings.from_file(path)


def test_dev_rejects_live_telemetry_but_supports_explicit_imports(tmp_path):
    settings = Settings.from_file(DEV)
    settings.database = str(tmp_path / 'dev.db')
    with TestClient(create_app(settings)) as client:
        for route in ('/v1/traces', '/v1/logs'):
            response = client.post(route, json={})
            assert response.status_code == 403 and 'disabled' in response.json()['detail']
        assert client.get('/api/v1/sessions?identity_kind=all').json()['total'] == 0
        response = client.post('/api/v1/import/codex', content=(ROOT / 'examples/fixtures/codex-session.jsonl').read_bytes())
        assert response.status_code == 200
        config = client.get('/api/v1/config').json()
        assert config['environment'] == 'dev' and not config['otlp_enabled']
        assert config['database_name'] == 'dev.db'


def test_cli_uses_dev_port_and_database(monkeypatch, tmp_path):
    captured = {}
    import uvicorn

    from agentboard.server import create_reload_app

    def capture(app, **kwargs):
        assert app == 'agentboard.server:create_reload_app'
        captured.update(settings=create_reload_app().state.settings, **kwargs)

    monkeypatch.setattr(uvicorn, 'run', capture)
    monkeypatch.setattr(sys, 'argv', ['agentboard', '--config', str(DEV), '--database', str(tmp_path / 'dev.db'), 'serve'])
    main()
    assert captured['port'] == 4319 and not captured['settings'].otlp_enabled
    assert captured['settings'].database == str(tmp_path / 'dev.db')
    assert captured['reload'] and captured['factory']
    assert captured['reload_dirs'] == [str(ROOT / 'backend/agentboard')]


def test_default_server_does_not_reload(monkeypatch, tmp_path):
    import uvicorn

    captured = {}
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: captured.update(app=app, **kwargs))
    monkeypatch.setattr(sys, 'argv', ['agentboard', '--database', str(tmp_path / 'local.db'), 'serve'])
    main()
    assert captured['port'] == 4318
    assert not captured['app'].state.settings.reload
    assert not captured.get('reload', False)


def test_snapshot_includes_wal_and_does_not_follow_live_changes(tmp_path):
    source, destination = tmp_path / 'live.db', tmp_path / 'snapshot.db'
    live = Store(str(source))
    live.ingest([Session(id='before', started_at='2026-09-06T00:00:00Z')])
    with sqlite3.connect(source) as writer:
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("UPDATE sessions SET title='WAL title' WHERE id='before'")
        writer.commit()
        result = snapshot_database(source, destination)
        assert Store(str(destination)).get_session('before')['title'] == 'WAL title'
        writer.execute("UPDATE sessions SET title='Later title' WHERE id='before'")
        writer.commit()
    assert Store(str(destination)).get_session('before')['title'] == 'WAL title'
    assert live.get_session('before')['title'] == 'Later title'
    assert json.loads(Path(result['manifest']).read_text())['counts_at_creation']['sessions'] == 1
    with pytest.raises(ValueError, match='already exists'):
        snapshot_database(source, destination)
    with pytest.raises(ValueError, match='must differ'):
        snapshot_database(source, source)
