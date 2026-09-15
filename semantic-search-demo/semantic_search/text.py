import hashlib
import unicodedata


def normalize_text(value: str) -> str:
    """Make simple lowercase text so duplicate names use one catalog row.

    Input:
        value="  ÁCME, Inc. "
    Output:
        "acme inc"
    """

    plain_text = "".join(
        character
        for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )
    return " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in plain_text
        ).split()
    )


def semantic_key(value: str) -> str:
    """Return a stable SHA-256 catalog ID for normalized entity text.

    Input:
        value="ÁCME, Inc."
    Output:
        The same ID returned for value="acme inc".
    """

    normalized = normalize_text(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
