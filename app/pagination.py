import base64
import binascii
import json

from fastapi import HTTPException


def encode_page_token(value):
    """Convert Elasticsearch page key into a token for next request."""

    if not value:
        return None

    raw_value = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw_value).decode()


def decode_page_token(value):
    """This method helps to decode page token and throws error if its invalid."""

    if not value:
        return None

    try:
        raw_value = base64.urlsafe_b64decode(value.encode()).decode()
        decoded = json.loads(raw_value)
        if not isinstance(decoded, dict):
            raise ValueError("The page key must be an object")
        return decoded
    except (
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
        binascii.Error,
    ) as exc:
        raise HTTPException(status_code=400, detail="Invalid nextToken") from exc
