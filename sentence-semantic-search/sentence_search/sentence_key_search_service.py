import re

from sentence_search.config import Config
from sentence_search.opensearch_store import OpenSearchStore
from sentence_search.postgres_store import PostgresStore
from sentence_search.search_utils import vector_cosine_percentage

SENTENCE_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SentenceKeySearchService:
    def __init__(
        self,
        config: Config,
        opensearch: OpenSearchStore,
        postgres: PostgresStore,
    ):
        """Save the settings and stores used by sentence-key search."""

        self.config = config
        self.opensearch = opensearch
        self.postgres = postgres

    def search(
        self,
        application_id: str,
        sentence_key: str,
        analysis_group: str,
        threshold: int,
    ) -> dict:
        """Return all exact and saved similar sentences with their scores.

        Input: one SHA-256 sentence key and application A1.
        Output: every matching sentence outside A1 with exact or similar type.
        """

        sentence_key = sentence_key.strip().lower()
        if not SENTENCE_KEY_PATTERN.fullmatch(sentence_key):
            raise ValueError("sentenceKey must be a 64-character SHA-256 value")
        if not 1 <= threshold <= 100:
            raise ValueError("threshold must be between 1 and 100")

        if threshold < self.config.match_threshold:
            source_vector = self.opensearch.catalog_vector(sentence_key)
            catalog_matches = self.opensearch.catalog_matches(
                sentence_key,
                source_vector,
                threshold,
            )
            similar_keys = sorted(
                {
                    match["sentenceKey"]
                    for match in catalog_matches
                    if match["sentenceKey"] != sentence_key
                }
            )
            vectors = {sentence_key: source_vector}
            vectors.update(self.opensearch.catalog_vectors(similar_keys))
        else:
            key_row = self.postgres.matching_keys([sentence_key])[sentence_key]
            similar_keys = [
                key for key in key_row["matchingSentenceKeys"] if key != sentence_key
            ]
            candidate_keys = [sentence_key, *similar_keys]
            vectors = self.opensearch.catalog_vectors(candidate_keys)
            if sentence_key not in vectors:
                raise ValueError(f"sentenceKey does not exist: {sentence_key}")
            source_vector = vectors[sentence_key]
        score_by_key = {sentence_key: 100.0}
        qualified_similar_keys = []
        for similar_key in similar_keys:
            if similar_key not in vectors:
                raise RuntimeError(
                    f"Catalog vector does not exist for saved key: {similar_key}"
                )
            match_percentage = vector_cosine_percentage(
                source_vector,
                vectors[similar_key],
            )
            if match_percentage < threshold:
                continue
            qualified_similar_keys.append(similar_key)
            score_by_key[similar_key] = match_percentage

        search_keys = [sentence_key, *qualified_similar_keys]

        occurrences = self.opensearch.matching_occurrences(
            search_keys,
            application_id,
            analysis_group,
        )
        matches = []
        exact_count = 0
        similar_count = 0
        for occurrence in occurrences:
            occurrence_key = occurrence["sentenceKey"]
            if occurrence_key == sentence_key:
                match_type = "exact"
                exact_count += 1
            else:
                match_type = "similar"
                similar_count += 1
            matches.append(
                {
                    **occurrence,
                    "matchPercentage": score_by_key[occurrence_key],
                    "matchType": match_type,
                }
            )

        return {
            "applicationId": application_id,
            "analysisGroup": analysis_group,
            "sentenceKey": sentence_key,
            "thresholdPercentage": threshold,
            "directMatchingKeyCount": len(qualified_similar_keys),
            "exactMatchCount": exact_count,
            "similarMatchCount": similar_count,
            "totalMatches": len(matches),
            "returnedMatches": len(matches),
            "matches": matches,
            "neuralSearchUsed": False,
        }
