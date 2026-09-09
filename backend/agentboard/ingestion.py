"""Core capture and normalization policy shared by HTTP and CLI."""

import io
import shutil
import sqlite3
import tempfile
import zlib


class CaptureError(ValueError):
    def __init__(self, capture_id, message, status_code=422):
        super().__init__(message)
        self.capture_id = capture_id
        self.status_code = status_code


def decoded_body(body, encoding, limit):
    if encoding == "identity":
        return body
    if encoding != "gzip":
        raise ValueError("Only identity and gzip content encoding are supported")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    result = decoder.decompress(body, limit + 1)
    if len(result) > limit or decoder.unconsumed_tail:
        raise ValueError("Decompressed request exceeds body limit")
    if not decoder.eof or decoder.unused_data:
        raise ValueError("Invalid gzip stream")
    return result


class IngestionService:
    def __init__(self, store, captures, adapters, features, settings):
        self._store, self._captures, self._adapters = store, captures, adapters
        self._features, self._settings = features, settings

    @property
    def _limit(self):
        return self._settings.max_body_bytes

    def _normalize(self, capture_id, operation, status_code=422):
        try:
            result = operation()
        except Exception as exc:
            # Retain bytes even for unforeseen mapping errors; canonical writes roll back.
            message = str(exc) if isinstance(exc, (ValueError, UnicodeError)) else type(exc).__name__
            self._captures.finish(capture_id, error=message)
            code = 413 if message == "Decompressed request exceeds body limit" else status_code
            if isinstance(exc, sqlite3.OperationalError):
                code = 503
            raise CaptureError(capture_id, message, code) from exc
        self._captures.finish(capture_id, result=result)
        return {**result, "capture_id": capture_id, "capture_status": "retained", "normalization_status": "normalized"}

    def import_file(self, agent, stream):
        if agent not in self._adapters:
            raise ValueError("Unknown or disabled adapter")
        # A snapshot prevents a concurrently growing source changing between capture and parse.
        with tempfile.TemporaryFile() as snapshot:
            shutil.copyfileobj(stream, snapshot, length=1024 * 1024)
            capture_id = self._captures.capture(snapshot, agent, "application/x-ndjson")

            def parse():
                text = io.TextIOWrapper(snapshot, encoding="utf-8", newline="")
                try:
                    return self._store.ingest(self._adapters[agent].parse(text))
                finally:
                    text.detach()

            return self._normalize(capture_id, parse)

    def import_body(self, agent, body, encoding="identity"):
        if agent not in self._adapters:
            raise ValueError("Unknown or disabled adapter")
        capture_id = self._captures.capture(io.BytesIO(body), agent, "application/x-ndjson", encoding)
        return self._normalize(capture_id, lambda: self._store.ingest(self._adapters[agent].parse(
            io.StringIO(decoded_body(body, encoding, self._limit).decode("utf-8"), newline=""),
        )))

    def otlp(self, signal, body, content_type, encoding="identity"):
        if f"otlp_{signal}" not in self._features:
            raise ValueError("OTLP receiver disabled")
        capture_id = self._captures.capture(io.BytesIO(body), f"otlp_{signal}", content_type, encoding)

        def normalize():
            from .otlp import decode, normalize

            payload = decoded_body(body, encoding, self._limit)
            return self._store.ingest(normalize(decode(payload, signal, content_type == "application/x-protobuf"), signal))

        return self._normalize(capture_id, normalize, 400)

    def reprocess(self, capture_id):
        metadata = self._captures.get(capture_id)
        with tempfile.TemporaryFile() as snapshot:
            for chunk in self._captures.export(capture_id):
                snapshot.write(chunk)
            snapshot.seek(0)
            source = metadata["source"]
            if source in self._adapters and metadata["content_encoding"] == "identity":
                return self.import_file(source, snapshot)
            if metadata["byte_count"] > self._limit:
                raise ValueError("Stored HTTP payload exceeds configured body limit")
            body = snapshot.read()
            if source in self._adapters:
                return self.import_body(source, body, metadata["content_encoding"])
            if source in ("otlp_logs", "otlp_traces"):
                return self.otlp(source.removeprefix("otlp_"), body, metadata["content_type"], metadata["content_encoding"])
            raise ValueError("Unknown or disabled capture source")
