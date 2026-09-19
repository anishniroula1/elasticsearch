from collections import defaultdict

from sentence_search.sentence_summary_service import SentenceSummaryService

ALL_SUMMARY_PAGE_SIZE = 100  # Sentences processed in each internal full-summary page.


class AllSentenceSummaryService:
    def __init__(self, page_summary: SentenceSummaryService):
        """Save the page service used to build the complete summary."""

        self.page_summary = page_summary

    def application_summary(
        self,
        application_id: str,
        analysis_group: str,
        threshold: int,
    ) -> dict:
        """Return every eligible sentence and whole-application totals.

        Input: application A1, analysis group Asylee, and threshold 90.
        Output: all sentence summaries and totals across every internal page.
        """

        sentences = []
        section_counts = defaultdict(int)
        total_matching = 0
        matching_sentences = 0
        total_sentences = 0
        next_token = None

        while True:
            page = self.page_summary.application_summary(
                application_id,
                analysis_group,
                threshold,
                ALL_SUMMARY_PAGE_SIZE,
                next_token,
            )
            if not sentences:
                total_sentences = page["totalSentences"]
            sentences.extend(page["sentences"])
            total_matching += page["totalMatching"]
            matching_sentences += page["matchingSentences"]
            for section in page["sectionMatches"]:
                section_counts[section["sectionName"]] += section["matchingCount"]

            next_token = page["nextToken"]
            if next_token is None:
                break

        return {
            "applicationId": application_id,
            "analysisGroup": analysis_group,
            "thresholdPercentage": threshold,
            "summaryScope": "allSentences",
            "totalMatching": total_matching,
            "sectionMatches": [
                {
                    "sectionName": section_name,
                    "matchingCount": section_counts[section_name],
                }
                for section_name in sorted(section_counts)
            ],
            "totalSentences": total_sentences,
            "returnedSentences": len(sentences),
            "matchingSentences": matching_sentences,
            "sentences": sentences,
            "neuralSearchUsed": False,
        }
