import base64
import binascii
import json

TOKEN_VERSION = 2
TEXT_SEARCH_TOKEN_VERSION = 1


def _encode_token(payload: dict) -> str:
    """Turn page data into text that is safe to send in a URL.

    Input:
        A dictionary with page data.
    Output:
        A text token.
    """

    serialized = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(serialized).decode("ascii")


def _decode_token(token: str) -> dict:
    """Turn a page token back into page data.

    Input:
        A token returned by this API.
    Output:
        A dictionary with page data.
    """

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
    return payload


def encode_semantic_page_token(
    *,
    application_id: str,
    threshold: int,
    after_key: dict | None,
    total_unique_entities: int,
    returned_entities: int,
) -> str | None:
    """Make the token for the next application-summary page.

    Input:
        The current request and the OpenSearch after key.
    Output:
        A token, or None when there is no next page.
    """

    if not after_key:
        return None
    payload = {
        "version": TOKEN_VERSION,
        "applicationId": application_id,
        "threshold": threshold,
        "afterKey": after_key,
        "totalUniqueEntities": total_unique_entities,
        "returnedEntities": returned_entities,
    }
    return _encode_token(payload)


def decode_semantic_page_token(
    token: str,
    *,
    application_id: str,
    threshold: int,
) -> dict:
    """Read and check an application-summary page token.

    Input:
        A token, application ID, and threshold.
    Output:
        The saved page data.
    """

    payload = _decode_token(token)
    if payload.get("version") != TOKEN_VERSION:
        raise ValueError("Unsupported nextToken version")

    # Binding cursor state to the original request prevents a valid token from
    # silently continuing a different application's result set.
    if payload.get("applicationId") != application_id:
        raise ValueError("nextToken belongs to a different application")
    if payload.get("threshold") != threshold:
        raise ValueError("nextToken threshold does not match the request")

    after_key = payload.get("afterKey")
    if not isinstance(after_key, dict) or not after_key:
        raise ValueError("Invalid nextToken afterKey")

    for field in ("totalUniqueEntities", "returnedEntities"):
        value = payload.get(field)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise ValueError(f"Invalid nextToken {field}")

    return payload


def encode_text_search_page_token(
    *,
    application_id: str,
    threshold: int,
    query_key: str,
    after_key: dict | None,
    total_matches: int,
    returned_matches: int,
) -> str | None:
    """Make the token for the next text-search page.

    Input:
        The current search and the OpenSearch after key.
    Output:
        A token, or None when there is no next page.
    """

    if not after_key:
        return None
    return _encode_token(
        {
            "version": TEXT_SEARCH_TOKEN_VERSION,
            "tokenType": "textSearch",
            "applicationId": application_id,
            "threshold": threshold,
            "queryKey": query_key,
            "afterKey": after_key,
            "totalMatches": total_matches,
            "returnedMatches": returned_matches,
        }
    )


def decode_text_search_page_token(
    token: str,
    *,
    application_id: str,
    threshold: int,
    query_key: str,
) -> dict:
    """Read and check a text-search page token.

    Input:
        A token and the current text-search request.
    Output:
        The saved page data.
    """

    payload = _decode_token(token)
    if payload.get("version") != TEXT_SEARCH_TOKEN_VERSION:
        raise ValueError("Unsupported nextToken version")
    if payload.get("tokenType") != "textSearch":
        raise ValueError("nextToken is not for text search")
    if payload.get("applicationId") != application_id:
        raise ValueError("nextToken belongs to a different application")
    if payload.get("threshold") != threshold:
        raise ValueError("nextToken threshold does not match the request")
    if payload.get("queryKey") != query_key:
        raise ValueError("nextToken belongs to different search text")

    after_key = payload.get("afterKey")
    if not isinstance(after_key, dict) or not after_key:
        raise ValueError("Invalid nextToken afterKey")
    for field in ("totalMatches", "returnedMatches"):
        value = payload.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Invalid nextToken {field}")
    return payload
