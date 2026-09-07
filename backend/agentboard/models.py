"""Optional OpenAI-compatible model operations. No model is called during ingestion."""

import hashlib
import json
import re
import time

from .classification import (
    CLASSIFICATION_PROMPT,
    TAXONOMY_VERSION,
    ClassificationLabel,
    ClassificationResult,
    classification_format,
    classification_schema,
)
from .domain import Event, Session, stable_id
from .timestamps import format_timestamp


class ModelServiceError(RuntimeError):
    pass


class ModelGateway:
    def __init__(self, settings):
        self.settings = settings

    def complete(self, messages, classification=False):
        settings = self.settings
        if settings.model_mode not in ("auto", "local", "dummy"):
            raise ValueError("Model mode must be auto, local, or dummy")
        if settings.model_api not in ("chat_completions", "responses"):
            raise ValueError("Model API must be chat_completions or responses")
        if settings.model_timeout_seconds <= 0:
            raise ValueError("Model timeout must be positive")
        reason = "Dummy mode configured"
        if settings.model_mode != "dummy":
            try:
                from openai import OpenAI
            except ImportError:
                if settings.model_mode == "local":
                    raise ValueError("Install agentboard[models] to use the local model")
                reason = "OpenAI client is not installed"
            else:
                from openai import APIConnectionError, APIStatusError, APITimeoutError

                # Fall back only on connection failure; auth/model errors must remain visible.
                try:
                    with OpenAI(
                        base_url=settings.model_base_url,
                        api_key=settings.model_key,
                        timeout=settings.model_timeout_seconds,
                        max_retries=0,
                    ) as client:
                        model = settings.model
                        if not model:
                            found = client.with_options(timeout=2).models.list().data
                            if not found:
                                raise ValueError(
                                    "Local service returned no models; configure AGENTBOARD_MODEL"
                                )
                            model = found[0].id
                        if settings.model_api == "responses":
                            response = client.responses.create(
                                model=model, input=messages, max_output_tokens=2048, store=False,
                                **({"text": {"format": classification_format()}} if classification else {}),
                            )
                            if response.status != "completed" or response.error:
                                detail = response.incomplete_details
                                suffix = f": {detail.reason}" if detail and detail.reason else ""
                                raise ModelServiceError(f"Model response did not complete{suffix}")
                            output = response.output_text
                            if classification and any(
                                content.type == "refusal"
                                for item in response.output if item.type == "message"
                                for content in item.content
                            ):
                                raise ModelServiceError("Model refused classification")
                            if not output.strip():
                                raise ModelServiceError("Model response contained no output text")
                        else:
                            output_format = classification_format()
                            response = client.chat.completions.create(
                                model=model, messages=messages, temperature=0, max_tokens=600,
                                **({"response_format": {
                                    "type": "json_schema",
                                    "json_schema": {k: v for k, v in output_format.items() if k != "type"},
                                }} if classification else {}),
                            )
                            choice = response.choices[0]
                            if classification:
                                if choice.message.refusal:
                                    raise ModelServiceError("Model refused classification")
                                if choice.finish_reason != "stop":
                                    raise ModelServiceError("Model classification did not complete")
                            output = choice.message.content or ""
                        return {
                            "text": output,
                            "model": model,
                            "provider": "openai-compatible",
                            "api": settings.model_api,
                            "dummy": False,
                        }
                except APITimeoutError:
                    reason = f"Local model request timed out after {settings.model_timeout_seconds} seconds"
                    if settings.model_mode == "local":
                        raise ValueError(reason) from None
                except APIConnectionError:
                    if settings.model_mode == "local":
                        raise ValueError("Local model service is unavailable")
                    reason = "Local model service is unavailable"
                except APIStatusError as exc:
                    raise ModelServiceError(
                        f"Model service rejected the request (HTTP {exc.status_code})"
                    ) from exc
        if classification:
            # Test placeholder: prioritize user requests over incidental assistant text.
            transcript = messages[-1]["content"]
            text = "\n".join(line[6:] for line in transcript.splitlines() if line.startswith("user: "))
            text = (text or transcript).lower()
            category = "other"
            for candidate, words in (
                ("bug-fixing", ("bug", "fix", "debug", "error", "regression")),
                ("coding", ("implement", "code", "refactor", "function", "unit test")),
                ("analysis", ("analyze", "analyse", "calculate", "statistics", "spreadsheet")),
                ("creative-media", ("image", "illustration", "logo", "video", "audio")),
                ("writing", ("write", "article", "essay", "document", "translate", "summarize")),
                ("research", ("research", "investigate", "compare", "find sources")),
                ("guidance", ("how to", "how do", "teach", "explain", "guide")),
            ):
                if any(re.search(r"\b" + re.escape(word) + r"\b", text) for word in words):
                    category = candidate
                    break
            output = json.dumps(
                {"category": category, "reason": "Dummy keyword classification; replace with model result."}
            )
        else:
            output = (
                "[Dummy response] Continued the saved conversation with your replacement input: "
                + messages[-1]["content"]
            )
        return {
            "text": output,
            "model": "dummy",
            "provider": "dummy",
            "dummy": True,
            "fallback_reason": reason,
        }


def classification_input(store, sid, max_chars):
    session = store.get_session(sid)
    if session["identity_kind"] != "session":
        raise ValueError("Purpose classification requires a session, not unattributed telemetry")
    if max_chars < 1:
        raise ValueError("Classification context limit must be positive")
    parts, length, truncated = [], 0, False
    event_ids, source = [], None
    for e in store.classification_messages(sid):
        text = f"{e['kind']}: {e['text']}\n"
        remaining = max_chars - length
        if remaining == 0:
            truncated = True
            break
        parts.append(text[:remaining])
        event_ids.append(e["id"])
        source = e["source"]
        length += len(text[:remaining])
        if len(text) > remaining:
            truncated = True
            break
    if not parts:
        raise ValueError("No recorded conversation text is available to classify")
    transcript = "".join(parts)
    return {
        "messages": [
            {"role": "system", "content": CLASSIFICATION_PROMPT},
            {"role": "user", "content": transcript},
        ],
        "taxonomy_version": TAXONOMY_VERSION,
        "prompt_sha256": hashlib.sha256(CLASSIFICATION_PROMPT.encode()).hexdigest(),
        "output_schema_sha256": hashlib.sha256(
            json.dumps(classification_schema(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "input_sha256": hashlib.sha256(transcript.encode()).hexdigest(),
        "input_selection": "conversation-messages-v1",
        "input_source": source,
        "input_event_ids": event_ids,
        "input_chars": length,
        "truncated": truncated,
    }


def classify_session(store, sid, gateway, max_chars):
    context = classification_input(store, sid, max_chars)
    result = gateway.complete(context.pop("messages"), classification=True)
    try:
        label = ClassificationLabel.model_validate_json(result.pop("text"))
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Model did not return a valid classification") from exc
    result.update(
        {
            **label.model_dump(mode="json"),
            **context,
            "classified_at": format_timestamp(time.time_ns()),
            "content_origin": "inferred" if result["dummy"] else "model_generated",
        }
    )
    result = ClassificationResult.model_validate(result).model_dump(mode="json", exclude_none=True)
    store.classify(sid, result)
    return result


def branch_context(store, sid, input_id, replacement, max_chars):
    source = store.get_session(sid)
    selected = store.event(sid, input_id)
    if selected["kind"] != "user":
        raise ValueError("Select a user input to replace")
    if selected["source"] != "codex_jsonl" and selected["source"] != "replay":
        raise ValueError("Replay needs a historical transcript, not telemetry-only prompts")
    history = []
    length = len(replacement)
    # Sequence is the rollout order, independent of span completion/insert order.
    if length > max_chars:
        raise ValueError("Replacement exceeds the configured context limit")
    for e in store.ordered_events(sid, selected["sequence"]):
        if e["source"] != selected["source"]:
            continue
        if e["name"] in ("compacted", "thread_rolled_back"):
            raise ValueError("This history contains compaction/rollback; use native Codex branching")
        if e["kind"] in ("user", "assistant"):
            text = e["text"]
            role = "user" if e["kind"] == "user" else "assistant"
        elif e["kind"] == "tool" or (
            e["kind"] == "user_wait" and e["attributes"].get("wait_type") == "input_request"
        ):
            text = f"[Recorded tool call: {e['name']}]\n{e['text']}\n{e['attributes'].get('output', '')}"
            role = "assistant"
        else:
            continue
        length += len(text)
        if length > max_chars:
            raise ValueError(
                "Replay context exceeds the configured character limit; use native Codex branching"
            )
        history.append({"role": role, "content": text})
    history.append({"role": "user", "content": replacement})
    return source, selected, history


def replay_session(store, sid, input_id, replacement, gateway, max_chars):
    source, selected, history = branch_context(store, sid, input_id, replacement, max_chars)
    start = format_timestamp(time.time_ns())
    result = gateway.complete(history)
    end = format_timestamp(time.time_ns())
    branch_id = stable_id(sid, input_id, start)
    batch = [
        Session(
            id=branch_id,
            agent="replay",
            title=replacement[:100],
            started_at=start,
            metadata={
                "parent_session_id": sid,
                "replaced_input_id": input_id,
                "mode": "conversation_replay",
                "model": result["model"],
                "dummy": result["dummy"],
            },
        )
    ]
    for i, message in enumerate(history):
        batch.append(
            Event(
                id=stable_id(branch_id, i),
                session_id=branch_id,
                sequence=i,
                kind="user" if message["role"] == "user" else "assistant",
                name="User input" if message["role"] == "user" else "Retained context",
                start_time=start,
                source="replay",
                text=message["content"],
                attributes={"retained_context": i < len(history) - 1},
            )
        )
    batch.extend(
        [
            Event(
                id=stable_id(branch_id, "llm"),
                session_id=branch_id,
                sequence=len(history),
                kind="llm",
                name="Dummy response" if result["dummy"] else "Model response",
                start_time=start,
                end_time=end,
                timing="measured",
                source="replay",
                attributes={"dummy": result["dummy"]},
            ),
            Event(
                id=stable_id(branch_id, "answer"),
                session_id=branch_id,
                sequence=len(history) + 1,
                kind="assistant",
                name="Assistant",
                start_time=end,
                source="replay",
                text=result["text"],
                attributes={"content_origin": "inferred" if result["dummy"] else "model_generated"},
            ),
        ]
    )
    store.ingest(batch)
    return {"session_id": branch_id, **result, "mode": "conversation_replay"}
