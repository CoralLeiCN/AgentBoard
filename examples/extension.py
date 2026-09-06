"""Opt in with AGENTBOARD_PLUGINS=examples.extension. No new agent is implemented here."""


def register(*, app, store, adapters, settings):
    @app.get("/api/v1/extensions/example")
    def example():
        return {"message": "Optional extension enabled", "adapters": sorted(adapters)}
