"""Core composition and cleanup shared by HTTP and CLI."""

import threading
from contextlib import ExitStack
from functools import partial

from .features import load_catalog


class Runtime:
    def __init__(self, settings, catalog=None):
        self.settings = settings
        self.catalog = catalog if catalog is not None else load_catalog(settings)
        self.features = self.catalog.validate(settings.features)
        self._resources = ExitStack()
        self._services = {}
        self._closed = False
        try:
            from .adapters.catalog import build_adapters
            from .capture_store import CaptureRepository
            from .ingestion import IngestionService
            from .store import Store

            self.store = Store(settings.database, retain_lineage=self.enabled("field_lineage"))
            self.captures = CaptureRepository(self.store.connect)
            self.adapters = build_adapters(self.features)
            self.ingestion = IngestionService(
                self.store, self.captures, self.adapters, self.features, settings,
            )
            self.ingest_slots = threading.BoundedSemaphore(settings.ingest_concurrency)
        except BaseException:
            self.close()
            raise

    def enabled(self, name):
        return name in self.features

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if not self._closed:
            self._closed = True
            self._resources.close()

    def gateway(self):
        if not self.features & {"classification", "replay"}:
            raise ValueError("Model features are disabled")
        if not hasattr(self, "_gateway"):
            from .models import ModelGateway

            self._gateway = ModelGateway(self.settings)
        return self._gateway

    async def receive_import(self, agent, request):
        from .http_ingestion import receive

        return await receive(request, partial(self.ingestion.import_body, agent),
                             slots=self.ingest_slots, limit=self.settings.max_body_bytes)

    async def receive_otlp(self, signal, request):
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse, Response

        from .http_ingestion import receive

        content_type = request.headers.get("content-type", "").split(";")[0]
        if content_type not in ("application/json", "application/x-protobuf"):
            raise HTTPException(415, "Use application/json or application/x-protobuf")
        result = await receive(request, lambda body, encoding: self.ingestion.otlp(
            signal, body, content_type, encoding,
        ), slots=self.ingest_slots, limit=self.settings.max_body_bytes)
        headers = {"X-AgentBoard-Capture-ID": str(result["capture_id"])}
        return (Response(b"", media_type=content_type, headers=headers)
                if content_type == "application/x-protobuf" else JSONResponse({}, headers=headers))

    def services_for(self, name):
        if name not in self.features:
            raise ValueError(f"The {name} feature is disabled")
        if name not in self._services:
            self._services[name] = self._build_services(name)
        return self._services[name]

    def _build_services(self, name):
        from . import services as s

        store = self.store
        if name == "import":
            return s.ImportServices(frozenset(self.adapters), self.receive_import)
        if name in {"otlp_logs", "otlp_traces"}:
            return s.ReceiverServices(partial(self.receive_otlp, name.removeprefix("otlp_")))
        if name == "inputs":
            return s.InputServices(store.get_session, store.events)
        if name == "unified_timeline":
            return s.UnifiedServices(partial(store.unified, include_parallel=self.enabled("parallel_groups")))
        if name == "parallel_groups":
            return s.ParallelServices(store.parallel_groups)
        if name == "token_usage":
            from .pricing import catalog

            return s.UsageServices(store.usage, catalog, self.enabled("export"))
        if name == "export":
            return s.ExportServices(store.get_session, store.export)
        if name == "raw_archive":
            return s.ArchiveServices(
                store.get_session, store.raw_imports, store.event_raw, store.raw_import, store.export_raw,
                self.captures.list, self.captures.get, self.captures.export, self.enabled("export"),
            )
        if name == "field_lineage":
            return s.LineageServices(store.field_lineage)
        if name == "classification":
            from .models import classification_input, classify_session

            gateway = self.gateway()
            return s.ClassificationServices(
                store.get_session, store.classify,
                lambda sid: classify_session(store, sid, gateway, self.settings.max_model_chars),
                lambda sid: classification_input(store, sid, self.settings.max_model_chars),
            )
        if name == "replay":
            from .models import replay_session

            gateway = self.gateway()
            return s.ReplayServices(lambda sid, eid, replacement: replay_session(
                store, sid, eid, replacement, gateway, self.settings.max_model_chars,
            ))
        if name == "native_resume":
            from .resume import make_plan

            return s.ResumeServices(partial(make_plan, store))
        raise ValueError(f"No service factory for {name}")
