import re

from sentence_search.opensearch_store import OpenSearchStore

SENTENCE_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SentenceKeySearchService:
    def __init__(self, opensearch: OpenSearchStore):
        """Save the OpenSearch store used by sentence-key search."""

        self.opensearch = opensearch

    def search(
        self,
        application_id: str,
        sentence_key: str,
        analysis_group: str,
        threshold: int,
    ) -> dict:
        """Return all exact and similar sentences with their scores.

        Input: one SHA-256 sentence key and application A1.
        Output: every matching sentence outside A1 with exact or similar type.
        """

        sentence_key = sentence_key.strip().lower()
        if not SENTENCE_KEY_PATTERN.fullmatch(sentence_key):
            raise ValueError("sentenceKey must be a 64-character SHA-256 value")
        if not 1 <= threshold <= 100:
            raise ValueError("threshold must be between 1 and 100")

        source_vector = self.opensearch.catalog_vector(sentence_key)
        catalog_matches = self.opensearch.catalog_matches(
            sentence_key,
            source_vector,
            threshold,
        )
        score_by_key = {sentence_key: 100.0}
        similar_keys = []
        for match in catalog_matches:
            matching_key = match["sentenceKey"]
            if matching_key == sentence_key:
                continue
            percentage = float(match["similarityPercentage"])
            if percentage < threshold:
                continue
            similar_keys.append(matching_key)
            score_by_key[matching_key] = percentage

        search_keys = [sentence_key, *sorted(set(similar_keys))]
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
            "directMatchingKeyCount": len(search_keys) - 1,
            "exactMatchCount": exact_count,
            "similarMatchCount": similar_count,
            "totalMatches": len(matches),
            "returnedMatches": len(matches),
            "matches": matches,
            "neuralSearchUsed": False,
        }
