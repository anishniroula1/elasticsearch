from collections import defaultdict
from math import ceil

from sentence_search.config import Config
from sentence_search.opensearch_store import OpenSearchStore
from sentence_search.postgres_store import PostgresStore
from sentence_search.search_utils import (
    decode_page_token,
    encode_page_token,
    vector_cosine_percentage,
)


class SentenceSummaryService:
    def __init__(
        self,
        config: Config,
        opensearch: OpenSearchStore,
        postgres: PostgresStore,
    ):
        """Save the settings and stores used by the application summary."""

        self.config = config
        self.opensearch = opensearch
        self.postgres = postgres

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
                "updatedAt": state["summaryUpdatedAt"],
            }
            include_total = False
        else:
            after_global_id = None
            total_sentences = 0
            returned_before = 0
            include_total = True
            if threshold == self.config.match_threshold:
                summary = self.postgres.application_summary(
                    application_id,
                    analysis_group,
                )
            else:
                calculated = self._calculate_application_summary(
                    application_id,
                    analysis_group,
                    threshold,
                )
                summary = {
                    "totalMatching": calculated["totalMatching"],
                    "sectionMatches": calculated["sectionMatches"],
                    "updatedAt": None,
                }
                all_similar_keys = calculated["similarKeysBySource"]

        summary_updated_at = summary["updatedAt"]
        if hasattr(summary_updated_at, "isoformat"):
            summary_updated_at = summary_updated_at.isoformat()

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
        key_rows = self.postgres.matching_keys(
            [sentence["sentenceKey"] for sentence in sentences]
        )
        if all_similar_keys is None:
            similar_keys_by_source = self._similar_keys_at_threshold(
                key_rows,
                threshold,
            )
        else:
            similar_keys_by_source = {
                source_key: all_similar_keys.get(source_key, [])
                for source_key in key_rows
            }
        candidate_keys = set()
        for sentence in sentences:
            source_key = sentence["sentenceKey"]
            candidate_keys.add(source_key)
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
                    "summaryUpdatedAt": summary_updated_at,
                }
            )

        total_pages = ceil(total_sentences / page_size) if total_sentences else 0
        return {
            "applicationId": application_id,
            "analysisGroup": analysis_group,
            "thresholdPercentage": threshold,
            "totalMatching": summary["totalMatching"],
            "sectionMatches": summary["sectionMatches"],
            "summaryUpdatedAt": summary_updated_at,
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

    def refresh_affected_applications(self, sentence_keys: list) -> dict:
        """Refresh every application touched by the given sentence keys.

        Input: one new or changed sentence key.
        Output: finished summaries saved for all affected applications.
        """

        source_keys = sorted(set(sentence_keys))
        key_rows = self.postgres.matching_keys(source_keys)
        affected_keys = set(source_keys)
        for source_key in source_keys:
            affected_keys.update(key_rows[source_key]["matchingSentenceKeys"])
        application_groups = self.opensearch.application_groups_for_keys(
            list(affected_keys)
        )
        refreshed = self.refresh_application_groups(application_groups)
        return {
            "affectedSentenceKeys": len(affected_keys),
            "applicationSummariesRefreshed": refreshed,
        }

    def refresh_application_groups(self, application_groups: list) -> int:
        """Calculate each unique application and analysis-group summary.

        Input: repeated application/group records found in OpenSearch.
        Output: the number of unique summaries saved in PostgreSQL.
        """

        unique_groups = {
            (item["applicationId"], item["analysisGroup"])
            for item in application_groups
        }
        for application_id, analysis_group in sorted(unique_groups):
            self.refresh_application_summary(application_id, analysis_group)
        return len(unique_groups)

    def refresh_application_summary(
        self,
        application_id: str,
        analysis_group: str,
    ) -> dict:
        """Calculate and save one complete application summary."""

        calculated = self._calculate_application_summary(
            application_id,
            analysis_group,
            self.config.match_threshold,
        )
        saved_counts = calculated["sectionMatchCounts"]
        total_matching = calculated["totalMatching"]
        self.postgres.save_application_summary(
            application_id,
            analysis_group,
            saved_counts,
            total_matching,
        )
        return {
            "applicationId": application_id,
            "analysisGroup": analysis_group,
            "sectionMatchCounts": saved_counts,
            "totalMatching": total_matching,
        }

    def _calculate_application_summary(
        self,
        application_id: str,
        analysis_group: str,
        threshold: int,
    ) -> dict:
        """Calculate complete counts for one application and threshold."""

        source_buckets = self.opensearch.application_key_section_counts(
            application_id,
            analysis_group,
        )
        source_keys = sorted({bucket["sentenceKey"] for bucket in source_buckets})
        key_rows = self.postgres.matching_keys(source_keys)
        similar_keys_by_source = self._similar_keys_at_threshold(
            key_rows,
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
            similar_keys = similar_keys_by_source[source_key]
            matches_per_sentence = outside_counts.get(source_key, 0)
            matches_per_sentence += sum(
                outside_counts.get(key, 0) for key in similar_keys
            )
            section_counts[bucket["sectionName"]] += (
                bucket["occurrenceCount"] * matches_per_sentence
            )

        saved_counts = dict(sorted(section_counts.items()))
        total_matching = sum(saved_counts.values())
        return {
            "sectionMatchCounts": saved_counts,
            "sectionMatches": [
                {
                    "sectionName": section_name,
                    "matchingCount": count,
                }
                for section_name, count in saved_counts.items()
            ],
            "totalMatching": total_matching,
            "similarKeysBySource": similar_keys_by_source,
        }

    def _similar_keys_at_threshold(
        self,
        key_rows: dict,
        threshold: int,
    ) -> dict:
        """Keep only saved relationships that still pass the cosine threshold.

        Input: source keys with their saved direct matching-key lists.
        Output: matching-key lists with old or low-score relationships removed.
        """

        candidate_keys = set(key_rows)
        for source_key, row in key_rows.items():
            candidate_keys.update(
                key for key in row["matchingSentenceKeys"] if key != source_key
            )
        if not candidate_keys:
            return {}

        if threshold < self.config.match_threshold:
            vectors = self.opensearch.catalog_vectors(list(key_rows))
            qualified = {}
            for source_key in key_rows:
                if source_key not in vectors:
                    raise RuntimeError(
                        f"Catalog vector does not exist for {source_key}"
                    )
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

        vectors = self.opensearch.catalog_vectors(list(candidate_keys))
        qualified = {}
        for source_key, row in key_rows.items():
            similar_keys = [
                key for key in row["matchingSentenceKeys"] if key != source_key
            ]
            if not similar_keys:
                qualified[source_key] = []
                continue
            if source_key not in vectors:
                raise RuntimeError(f"Catalog vector does not exist for {source_key}")

            source_vector = vectors[source_key]
            source_matches = []
            for similar_key in similar_keys:
                if similar_key not in vectors:
                    raise RuntimeError(
                        f"Catalog vector does not exist for saved key: {similar_key}"
                    )
                percentage = vector_cosine_percentage(
                    source_vector,
                    vectors[similar_key],
                )
                if percentage >= threshold:
                    source_matches.append(similar_key)
            qualified[source_key] = source_matches
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
            "summaryUpdatedAt",
        }
        if not required.issubset(state):
            raise ValueError("Invalid nextToken")
