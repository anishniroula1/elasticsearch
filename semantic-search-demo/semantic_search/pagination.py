"""Opaque continuation tokens for semantic composite aggregations."""

import base64
import binascii
import json
from typing import Any

TOKEN_VERSION = 1


def encode_semantic_page_token(
    *,
    application_id: str,
    threshold: int,
    after_key: dict[str, Any] | None,
    total_unique_entities: int,
    total_matching_entities: int,
    returned_matching_entities: int,
) -> str | None:
    """Wrap an OpenSearch after_key and first-page totals for the client."""

    if not after_key or returned_matching_entities >= total_matching_entities:
        return None
    payload = {
        "version": TOKEN_VERSION,
        "applicationId": application_id,
        "threshold": threshold,
        "afterKey": after_key,
        "totalUniqueEntities": total_unique_entities,
        "totalMatchingEntities": total_matching_entities,
        "returnedMatchingEntities": returned_matching_entities,
    }
    serialized = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(serialized).decode("ascii")


def decode_semantic_page_token(
    token: str,
    *,
    application_id: str,
    threshold: int,
) -> dict[str, Any]:
    """Validate a token and return its OpenSearch continuation state."""

    try:
        serialized = base64.b64decode(
            token.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(serialized.decode("utf-8"))
    except (
        binascii.Error,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise ValueError("Invalid nextToken") from error

    if not isinstance(payload, dict):
        raise ValueError("Invalid nextToken")
    if payload.get("version") != TOKEN_VERSION:
        raise ValueError("Unsupported nextToken version")
    if payload.get("applicationId") != application_id:
        raise ValueError("nextToken belongs to a different application")
    if payload.get("threshold") != threshold:
        raise ValueError("nextToken threshold does not match the request")

    after_key = payload.get("afterKey")
    if not isinstance(after_key, dict) or not after_key:
        raise ValueError("Invalid nextToken afterKey")

    integer_fields = (
        "totalUniqueEntities",
        "totalMatchingEntities",
        "returnedMatchingEntities",
    )
    for field in integer_fields:
        value = payload.get(field)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise ValueError(f"Invalid nextToken {field}")
    if (
        payload["returnedMatchingEntities"]
        >= payload["totalMatchingEntities"]
    ):
        raise ValueError("nextToken has no remaining results")

    return payload
