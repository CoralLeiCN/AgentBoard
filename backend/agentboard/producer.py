"""Application producer identity, independent of agent family and task purpose."""

AGENTBOARD_ORIGINATORS = ("agentboard", "agentboard_classifier")


def codex_producer(metadata):
    return "agentboard" if metadata.get("originator") in AGENTBOARD_ORIGINATORS else None


def otlp_producer(attributes):
    if (attributes.get("agentboard.producer") == "agentboard"
            or attributes.get("originator") in AGENTBOARD_ORIGINATORS
            or attributes.get("service.name") in AGENTBOARD_ORIGINATORS):
        return "agentboard"
    return None


def classification_exclusion(session):
    if session.get("producer") == "agentboard":
        return "AgentBoard-generated sessions are excluded from purpose classification"
    if session["identity_kind"] != "session":
        return "Purpose classification requires a session, not unattributed telemetry"
    return None
