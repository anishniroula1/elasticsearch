from collections import defaultdict
from math import ceil

from sentence_search.opensearch_store import OpenSearchStore
from sentence_search.search_utils import decode_page_token, encode_page_token


class SentenceSummaryService:
    def __init__(self, opensearch: OpenSearchStore):
        """Save the OpenSearch store used by the application summary."""

        self.opensearch = opensearch

    def application_summary(
        self,
        application_id: str,
        analysis_group: str,
        threshold: int,
        page_size: int,
        next_token: str | None,
    ) -> dict:
        """Return one application sentence page with outside match counts.

        Input: application A1, analysis group Asylee, and page size 100.
        Output: up to 100 sentences with exact, similar, and total counts.
        """

        if not 1 <= threshold <= 100:
            raise ValueError("threshold must be between 1 and 100")

        all_similar_keys = None
        if next_token:
            state = decode_page_token(next_token)
            self._validate_token(
                state,
                application_id,
                analysis_group,
                threshold,
                page_size,
            )
            after_global_id = state["afterGlobalId"]
            total_sentences = int(state["totalSentences"])
            returned_before = int(state["returnedSentences"])
            summary = {
                "totalMatching": int(state["totalMatching"]),
                "sectionMatches": state["sectionMatches"],
            }
            include_total = False
        else:
            after_global_id = None
            total_sentences = 0
            returned_before = 0
            include_total = True
            calculated = self._calculate_application_summary(
                application_id,
                analysis_group,
                threshold,
            )
            summary = {
                "totalMatching": calculated["totalMatching"],
                "sectionMatches": calculated["sectionMatches"],
            }
            # The first page already searched every source key. Reuse that work
            # instead of running the same vector searches for its 100 rows.
            all_similar_keys = calculated["similarKeysBySource"]

        result = self.opensearch.application_occurrence_page(
            application_id,
            analysis_group,
            page_size,
            after_global_id,
            include_total,
        )
        if include_total:
            total_sentences = int(result["totalSentences"] or 0)

        sentences = result["sentences"]
        source_keys = sorted({sentence["sentenceKey"] for sentence in sentences})
        if all_similar_keys is None:
            similar_keys_by_source = self._similar_keys_at_threshold(
                source_keys,
                threshold,
            )
        else:
            similar_keys_by_source = {
                source_key: all_similar_keys.get(source_key, [])
                for source_key in source_keys
            }

        candidate_keys = set(source_keys)
        for source_key in source_keys:
            candidate_keys.update(similar_keys_by_source[source_key])
        counts = self.opensearch.occurrence_counts_by_key(
            list(candidate_keys),
            application_id,
            analysis_group,
        )

        response_sentences = []
        for sentence in sentences:
            source_key = sentence["sentenceKey"]
            similar_keys = similar_keys_by_source[source_key]
            exact_count = counts.get(source_key, 0)
            similar_count = sum(counts.get(key, 0) for key in similar_keys)
            response_sentences.append(
                {
                    **sentence,
                    "exactMatchCount": exact_count,
                    "similarMatchCount": similar_count,
                    "totalMatchCount": exact_count + similar_count,
                    "directMatchingKeyCount": len(similar_keys),
                }
            )

        returned_after = returned_before + len(response_sentences)
        new_token = None
        next_after = result["nextAfterGlobalId"]
        if next_after:
            new_token = encode_page_token(
                {
                    "type": "applicationSentenceSummary",
                    "applicationId": application_id,
                    "analysisGroup": analysis_group,
                    "threshold": threshold,
                    "pageSize": page_size,
                    "afterGlobalId": next_after,
                    "totalSentences": total_sentences,
                    "returnedSentences": returned_after,
                    "totalMatching": summary["totalMatching"],
                    "sectionMatches": summary["sectionMatches"],
                }
            )

        total_pages = ceil(total_sentences / page_size) if total_sentences else 0
        return {
            "applicationId": application_id,
            "analysisGroup": analysis_group,
            "thresholdPercentage": threshold,
            "totalMatching": summary["totalMatching"],
            "sectionMatches": summary["sectionMatches"],
            "totalSentences": total_sentences,
            "returnedSentences": len(response_sentences),
            "pageMatchingSentences": sum(
                sentence["totalMatchCount"] > 0 for sentence in response_sentences
            ),
            "pageTotalMatches": sum(
                sentence["totalMatchCount"] for sentence in response_sentences
            ),
            "pagination": {
                "page": (returned_before // page_size) + 1,
                "pageSize": page_size,
                "totalPages": total_pages,
                "hasPreviousPage": returned_before > 0,
                "hasNextPage": new_token is not None,
            },
            "nextToken": new_token,
            "sentences": response_sentences,
            "neuralSearchUsed": False,
        }

    def _calculate_application_summary(
        self,
        application_id: str,
        analysis_group: str,
        threshold: int,
    ) -> dict:
        """Calculate complete section totals live from both OpenSearch indexes."""

        source_buckets = self.opensearch.application_key_section_counts(
            application_id,
            analysis_group,
        )
        source_keys = sorted({bucket["sentenceKey"] for bucket in source_buckets})
        similar_keys_by_source = self._similar_keys_at_threshold(
            source_keys,
            threshold,
        )

        candidate_keys = set(source_keys)
        for source_key in source_keys:
            candidate_keys.update(similar_keys_by_source[source_key])
        outside_counts = self.opensearch.occurrence_counts_by_key(
            list(candidate_keys),
            application_id,
            analysis_group,
        )

        section_counts = defaultdict(int)
        for bucket in source_buckets:
            source_key = bucket["sentenceKey"]
            matches_per_sentence = outside_counts.get(source_key, 0)
            matches_per_sentence += sum(
                outside_counts.get(key, 0)
                for key in similar_keys_by_source[source_key]
            )
            section_counts[bucket["sectionName"]] += (
                bucket["occurrenceCount"] * matches_per_sentence
            )

        saved_counts = dict(sorted(section_counts.items()))
        return {
            "sectionMatches": [
                {
                    "sectionName": section_name,
                    "matchingCount": count,
                }
                for section_name, count in saved_counts.items()
            ],
            "totalMatching": sum(saved_counts.values()),
            "similarKeysBySource": similar_keys_by_source,
        }

    def _similar_keys_at_threshold(
        self,
        source_keys: list,
        threshold: int,
    ) -> dict:
        """Run live catalog vector searches for the requested source keys."""

        if not source_keys:
            return {}
        vectors = self.opensearch.catalog_vectors(source_keys)
        qualified = {}
        for source_key in source_keys:
            if source_key not in vectors:
                raise RuntimeError(f"Catalog vector does not exist for {source_key}")
            matches = self.opensearch.catalog_matches(
                source_key,
                vectors[source_key],
                threshold,
            )
            qualified[source_key] = sorted(
                {
                    match["sentenceKey"]
                    for match in matches
                    if match["sentenceKey"] != source_key
                }
            )
        return qualified

    @staticmethod
    def _validate_token(
        state: dict,
        application_id: str,
        analysis_group: str,
        threshold: int,
        page_size: int,
    ):
        """Stop a token from being reused for a different summary request."""

        expected = {
            "type": "applicationSentenceSummary",
            "applicationId": application_id,
            "analysisGroup": analysis_group,
            "threshold": threshold,
            "pageSize": page_size,
        }
        for name, value in expected.items():
            if state.get(name) != value:
                raise ValueError("nextToken does not belong to this request")
        required = {
            "totalSentences",
            "returnedSentences",
            "totalMatching",
            "sectionMatches",
        }
        if not required.issubset(state):
            raise ValueError("Invalid nextToken")
