"""Core HTTP admission, bounded transport reads, and worker scheduling."""

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool


async def receive(request, operation, *, slots, limit):
    encoding = request.headers.get("content-encoding", "identity")
    if encoding not in ("identity", "gzip"):
        raise HTTPException(415, "Only identity and gzip content encoding are supported")
    if not slots.acquire(blocking=False):
        raise HTTPException(503, "Ingestion capacity reached", headers={"Retry-After": "1"})
    try:
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, "Request exceeds body limit; capture not accepted")
            chunks.append(chunk)
        return await run_in_threadpool(operation, b"".join(chunks), encoding)
    finally:
        slots.release()
