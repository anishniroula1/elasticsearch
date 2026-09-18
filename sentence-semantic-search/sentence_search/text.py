import hashlib
import unicodedata


def normalize_sentence(value: str) -> str:
    """Normalize case and spacing while keeping meaningful punctuation.

    Input:
        value="  The U.S. Government  "
    Output:
        "the u.s. government"
    """

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def sentence_key(value: str) -> str:
    """Make the stable catalog ID for one normalized sentence.

    Input:
        value="The U.S. Government"
    Output:
        A 64-character SHA-256 value.
    """

    normalized = normalize_sentence(value)
    if not normalized:
        raise ValueError("sentenceContent cannot be empty")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
