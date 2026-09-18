import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import (
    and_,
    create_engine,
    delete,
    distinct,
    func,
    or_,
    select,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError, TimeoutError
from sqlalchemy.orm import sessionmaker

from sentence_search.config import Config
from sentence_search.database_models import (
    ApplicationSentenceSummary,
    Base,
    SentenceRecord,
    SentenceRelationship,
    model_values,
)
from sentence_search.matching_rules import is_matchable_sentence
from sentence_search.search_utils import (
    decode_id_token,
    decode_page_token,
    encode_id_token,
    encode_page_token,
)

MATCH_INSERT_BATCH_SIZE = 2_000  # Keeps PostgreSQL bulk statements manageable.
APPLICATION_SCOPE = "application"
ANALYSIS_SCOPE = "analysis"
SECTION_SCOPE = "section"


def _empty_match_counts() -> dict:
    """Make counters for one sentence while match rows are being saved."""

    return {"exact": 0, "semantic": 0}


def _now() -> datetime:
    """Return the current UTC time for worker locks and retries."""

    return datetime.now(UTC)


class PostgresStore:
    def __init__(self, config: Config, engine=None):
        """Create the SQLAlchemy engine and session factory."""

        self.config = config
        self.engine = engine or create_engine(
            config.database_url,
            pool_size=10,
            max_overflow=0,
            pool_pre_ping=True,
        )
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def wait_until_ready(self, attempts: int = 60):
        """Wait until SQLAlchemy can connect to PostgreSQL."""

        last_error = None
        for _ in range(attempts):
            try:
                with self.engine.connect() as connection:
                    connection.execute(select(1))
                return
            except (OperationalError, TimeoutError) as error:
                last_error = error
                time.sleep(2)
        raise RuntimeError(
            f"PostgreSQL did not become ready: {last_error}"
        ) from last_error

    def close(self):
        """Close SQLAlchemy's PostgreSQL connection pool."""

        self.engine.dispose()

    def init_schema(self):
        """Create the three SQLAlchemy tables and their indexes."""

        Base.metadata.create_all(self.engine)

    def reset_data(self):
        """Delete relationships, summaries, and sentence records."""

        with self.sessions.begin() as session:
            session.execute(delete(SentenceRelationship))
            session.execute(delete(ApplicationSentenceSummary))
            session.execute(delete(SentenceRecord))

    def register_sentences(self, records: list, threshold: int) -> dict:
        """Save sentences and leave new records pending for the worker."""

        if not records:
            return {"registered": 0, "jobsQueued": 0}

        input_records = records
        unique_records = {}
        for record in input_records:
            unique_records[record["globalId"]] = record
        records = list(unique_records.values())
        application_ids = sorted({record["applicationId"] for record in records})

        with self.sessions.begin() as session:
            existing_ids = self._validate_sentence_identity(session, input_records)
            record_insert = insert(SentenceRecord).values(
                [self._record_values(record, threshold) for record in records]
            )
            session.execute(
                record_insert.on_conflict_do_update(
                    index_elements=[SentenceRecord.globalId],
                    set_={
                        "tspId": record_insert.excluded.tspId,
                        "documentId": record_insert.excluded.documentId,
                        "sectionName": record_insert.excluded.sectionName,
                        "analysisGroup": record_insert.excluded.analysisGroup,
                        "sentIdLocal": record_insert.excluded.sentIdLocal,
                        "sentenceContent": record_insert.excluded.sentenceContent,
                        "isTracer": record_insert.excluded.isTracer,
                        "isFormLanguage": record_insert.excluded.isFormLanguage,
                        "sourceType": record_insert.excluded.sourceType,
                        "updatedAt": record_insert.excluded.updatedAt,
                    },
                )
            )
            self._rebuild_application_summaries(session, application_ids)

        jobs_queued = sum(
            record["globalId"] not in existing_ids and is_matchable_sentence(record)
            for record in records
        )
        return {"registered": len(records), "jobsQueued": jobs_queued}

    @staticmethod
    def _record_values(record: dict, threshold: int) -> dict:
        """Add worker defaults to one validated sentence record."""

        return {
            "globalId": record["globalId"],
            "applicationId": record["applicationId"],
            "tspId": record["tspId"],
            "documentId": record["documentId"],
            "sectionName": record["sectionName"],
            "analysisGroup": record["analysisGroup"],
            "sentIdLocal": record["sentIdLocal"],
            "sentenceContent": record["sentenceContent"],
            "sentenceKey": record["sentenceKey"],
            "isTracer": record["isTracer"],
            "isFormLanguage": record["isFormLanguage"],
            "sourceType": record["sourceType"],
            "exactMatchCount": 0,
            "semanticMatchCount": 0,
            "matchThreshold": threshold,
            "matchStatus": ("pending" if is_matchable_sentence(record) else "skipped"),
            "matchAttempts": 0,
            "matchAvailableAt": _now(),
            "createdAt": record["createdAt"],
            "updatedAt": record["updatedAt"],
        }

    def validate_sentence_identity(self, records: list):
        """Reject matching identity changes before OpenSearch is updated."""

        if not records:
            return
        with self.sessions() as session:
            self._validate_sentence_identity(session, records)

    @staticmethod
    def _validate_sentence_identity(session, records: list) -> set:
        """Keep a global ID tied to one stable matching identity."""

        incoming_sentences = {}
        for record in records:
            global_id = record["globalId"]
            identity = {
                "applicationId": record["applicationId"],
                "sentenceKey": record["sentenceKey"],
                "analysisGroup": record["analysisGroup"],
                "isTracer": record["isTracer"],
                "isFormLanguage": record["isFormLanguage"],
            }
            previous_identity = incoming_sentences.get(global_id)
            if previous_identity and previous_identity != identity:
                raise ValueError(
                    "The same globalId has different matching identity values "
                    f"in one batch: {global_id}"
                )
            incoming_sentences[global_id] = identity

        existing = session.execute(
            select(
                SentenceRecord.globalId,
                SentenceRecord.applicationId,
                SentenceRecord.sentenceKey,
                SentenceRecord.analysisGroup,
                SentenceRecord.isTracer,
                SentenceRecord.isFormLanguage,
            ).where(SentenceRecord.globalId.in_(incoming_sentences))
        ).mappings()
        existing_ids = set()
        for row in existing:
            global_id = row["globalId"]
            existing_ids.add(global_id)
            incoming = incoming_sentences[global_id]
            if row["sentenceKey"] != incoming["sentenceKey"]:
                raise ValueError(
                    "globalId cannot be reused for different sentence content "
                    f"without reset: {global_id}"
                )
            if row["applicationId"] != incoming["applicationId"]:
                raise ValueError(
                    "globalId cannot move to a different application without "
                    f"reset: {global_id}"
                )
            matching_fields = ("analysisGroup", "isTracer", "isFormLanguage")
            if any(row[field] != incoming[field] for field in matching_fields):
                raise ValueError(
                    "globalId cannot change analysisGroup, isTracer, or "
                    f"isFormLanguage without reset: {global_id}"
                )
        return existing_ids

    def claim_job(self) -> dict | None:
        """Lock and return one pending sentence without blocking another worker."""

        with self.sessions.begin() as session:
            global_id = session.execute(
                select(SentenceRecord.globalId)
                .where(
                    SentenceRecord.matchStatus == "pending",
                    SentenceRecord.matchAvailableAt <= _now(),
                )
                .order_by(SentenceRecord.matchAvailableAt, SentenceRecord.globalId)
                .with_for_update(skip_locked=True)
                .limit(1)
            ).scalar_one_or_none()
            if global_id is None:
                return None

            row = (
                session.execute(
                    update(SentenceRecord)
                    .where(SentenceRecord.globalId == global_id)
                    .values(
                        matchStatus="running",
                        matchAttempts=SentenceRecord.matchAttempts + 1,
                        matchLockedAt=_now(),
                        matchLastError=None,
                    )
                    .returning(*SentenceRecord.__table__.columns)
                )
                .mappings()
                .one()
            )
            self._change_summary_job_status(session, row, "pending", "running")
            return dict(row)

    def requeue_stale_jobs(self):
        """Return abandoned running records to the pending queue."""

        stale_time = _now() - timedelta(minutes=10)
        with self.sessions.begin() as session:
            stale_records = (
                session.execute(
                    select(SentenceRecord).where(
                        SentenceRecord.matchStatus == "running",
                        SentenceRecord.matchLockedAt < stale_time,
                    )
                )
                .scalars()
                .all()
            )
            for record in stale_records:
                self._change_summary_job_status(
                    session,
                    model_values(record),
                    "running",
                    "pending",
                )
                record.matchStatus = "pending"
                record.matchAvailableAt = _now()
                record.matchLockedAt = None
                record.matchLastError = "Worker stopped before completing the job"

    def complete_job(self, global_id: str):
        """Mark one sentence's background matching work as successful."""

        with self.sessions.begin() as session:
            record = session.get(SentenceRecord, global_id, with_for_update=True)
            if not record:
                return
            values = model_values(record)
            self._change_summary_job_status(session, values, "running", "completed")
            record.matchStatus = "completed"
            record.matchAttempts = 0
            record.matchLockedAt = None
            record.matchLastError = None
            record.matchCompletedAt = _now()

    def retry_failed_jobs(self) -> int:
        """Put stopped sentence records back in the worker queue."""

        with self.sessions.begin() as session:
            failed_records = (
                session.execute(
                    select(SentenceRecord)
                    .where(SentenceRecord.matchStatus == "failed")
                    .with_for_update()
                )
                .scalars()
                .all()
            )
            for record in failed_records:
                values = model_values(record)
                self._change_summary_job_status(
                    session,
                    values,
                    "failed",
                    "pending",
                )
                record.matchStatus = "pending"
                record.matchAttempts = 0
                record.matchAvailableAt = _now()
                record.matchLockedAt = None
                record.matchLastError = None
        return len(failed_records)

    def fail_job(self, job: dict, error: Exception):
        """Retry one sentence or stop it after the configured limit."""

        final_failure = job["matchAttempts"] >= self.config.worker_max_attempts
        next_status = "failed" if final_failure else "pending"
        available_at = job["matchAvailableAt"]
        if not final_failure:
            available_at = _now() + timedelta(seconds=self.config.worker_retry_seconds)

        with self.sessions.begin() as session:
            record = session.get(
                SentenceRecord,
                job["globalId"],
                with_for_update=True,
            )
            if not record:
                return
            self._change_summary_job_status(
                session,
                model_values(record),
                "running",
                next_status,
            )
            record.matchStatus = next_status
            record.matchAvailableAt = available_at
            record.matchLockedAt = None
            record.matchLastError = str(error)[:4_000]

    def _change_summary_job_status(
        self,
        session,
        record: dict,
        old_status: str,
        new_status: str,
    ):
        """Move one worker count in every prepared summary for the sentence."""

        old_column = self._status_column(old_status)
        new_column = self._status_column(new_status)
        for scope, section_name, analysis_group in self._summary_keys(record):
            session.execute(
                update(ApplicationSentenceSummary)
                .where(
                    ApplicationSentenceSummary.applicationId == record["applicationId"],
                    ApplicationSentenceSummary.summaryScope == scope,
                    ApplicationSentenceSummary.sectionName == section_name,
                    ApplicationSentenceSummary.analysisGroup == analysis_group,
                )
                .values(
                    {
                        old_column: func.greatest(old_column - 1, 0),
                        new_column: new_column + 1,
                        ApplicationSentenceSummary.updatedAt: _now(),
                    }
                )
            )

    @staticmethod
    def _status_column(status: str):
        """Return the summary column used by one worker status."""

        columns = {
            "pending": ApplicationSentenceSummary.pendingCount,
            "running": ApplicationSentenceSummary.runningCount,
            "completed": ApplicationSentenceSummary.completedCount,
            "failed": ApplicationSentenceSummary.failedCount,
        }
        return columns[status]

    @staticmethod
    def _summary_keys(record: dict) -> list:
        """Return application, analysis, and section keys for one sentence."""

        return [
            (APPLICATION_SCOPE, "", ""),
            (ANALYSIS_SCOPE, "", record["analysisGroup"]),
            (
                SECTION_SCOPE,
                record["sectionName"],
                record["analysisGroup"],
            ),
        ]

    def sentence(self, global_id: str) -> dict | None:
        """Get one sentence record for the background worker."""

        with self.sessions() as session:
            record = session.get(SentenceRecord, global_id)
            return model_values(record) if record else None

    def save_matches(
        self,
        source: dict,
        catalog_matches: list,
        model_version: str,
    ) -> dict:
        """Save unique sentence relationships and update both sentence counts."""

        if not is_matchable_sentence(source) or not catalog_matches:
            return {"candidatesFound": 0, "relationshipsInserted": 0}

        match_by_key = {match["sentenceKey"]: match for match in catalog_matches}
        candidate_keys = list(match_by_key)
        with self.sessions.begin() as session:
            targets = session.execute(
                select(
                    SentenceRecord.globalId,
                    SentenceRecord.applicationId,
                    SentenceRecord.sentenceKey,
                ).where(
                    SentenceRecord.sentenceKey.in_(candidate_keys),
                    SentenceRecord.globalId != source["globalId"],
                    SentenceRecord.analysisGroup == source["analysisGroup"],
                    SentenceRecord.isTracer.is_(False),
                    SentenceRecord.isFormLanguage.is_(False),
                )
            ).mappings()

            edges = []
            for target in targets:
                if (
                    self.config.match_across_applications_only
                    and target["applicationId"] == source["applicationId"]
                ):
                    continue
                match = match_by_key[target["sentenceKey"]]
                edges.append(self._edge(source, dict(target), match, model_version))

            if not edges:
                return {
                    "candidatesFound": len(catalog_matches),
                    "relationshipsInserted": 0,
                }

            inserted = []
            for offset in range(0, len(edges), MATCH_INSERT_BATCH_SIZE):
                batch = edges[offset : offset + MATCH_INSERT_BATCH_SIZE]
                relationship_insert = insert(SentenceRelationship).values(batch)
                rows = session.execute(
                    relationship_insert.on_conflict_do_nothing().returning(
                        SentenceRelationship.sentenceIdLow,
                        SentenceRelationship.sentenceIdHigh,
                        SentenceRelationship.matchType,
                    )
                ).mappings()
                inserted.extend(dict(row) for row in rows)

            if inserted:
                deltas = defaultdict(_empty_match_counts)
                for edge in inserted:
                    match_type = edge["matchType"]
                    deltas[edge["sentenceIdLow"]][match_type] += 1
                    deltas[edge["sentenceIdHigh"]][match_type] += 1
                self._apply_match_deltas(session, deltas)

        return {
            "candidatesFound": len(catalog_matches),
            "relationshipsInserted": len(inserted),
        }

    def _apply_match_deltas(self, session, deltas: dict):
        """Update sentence and prepared summary counts in one transaction."""

        records = (
            session.execute(
                select(SentenceRecord)
                .where(SentenceRecord.globalId.in_(deltas))
                .order_by(SentenceRecord.globalId)
                .with_for_update()
            )
            .scalars()
            .all()
        )

        summary_deltas = defaultdict(_empty_match_counts)
        matched_sentence_deltas = defaultdict(int)
        for record in records:
            counts = deltas[record.globalId]
            was_unmatched = record.exactMatchCount + record.semanticMatchCount == 0
            record.exactMatchCount += counts["exact"]
            record.semanticMatchCount += counts["semantic"]
            became_matched = was_unmatched and counts["exact"] + counts["semantic"] > 0

            values = model_values(record)
            for scope, section_name, analysis_group in self._summary_keys(values):
                key = (
                    record.applicationId,
                    scope,
                    section_name,
                    analysis_group,
                )
                summary_deltas[key]["exact"] += counts["exact"]
                summary_deltas[key]["semantic"] += counts["semantic"]
                if became_matched:
                    matched_sentence_deltas[key] += 1

        for key, counts in summary_deltas.items():
            application_id, scope, section_name, analysis_group = key
            session.execute(
                update(ApplicationSentenceSummary)
                .where(
                    ApplicationSentenceSummary.applicationId == application_id,
                    ApplicationSentenceSummary.summaryScope == scope,
                    ApplicationSentenceSummary.sectionName == section_name,
                    ApplicationSentenceSummary.analysisGroup == analysis_group,
                )
                .values(
                    exactMatchCount=(
                        ApplicationSentenceSummary.exactMatchCount + counts["exact"]
                    ),
                    semanticMatchCount=(
                        ApplicationSentenceSummary.semanticMatchCount
                        + counts["semantic"]
                    ),
                    totalMatchCount=(
                        ApplicationSentenceSummary.totalMatchCount
                        + counts["exact"]
                        + counts["semantic"]
                    ),
                    matchedSentences=(
                        ApplicationSentenceSummary.matchedSentences
                        + matched_sentence_deltas[key]
                    ),
                    updatedAt=_now(),
                )
            )

    @staticmethod
    def _edge(
        source: dict,
        target: dict,
        match: dict,
        model_version: str,
    ) -> dict:
        """Put the smaller global ID first so one pair has one row."""

        if source["globalId"] < target["globalId"]:
            low_id = source["globalId"]
            high_id = target["globalId"]
        else:
            low_id = target["globalId"]
            high_id = source["globalId"]
        return {
            "sentenceIdLow": low_id,
            "sentenceIdHigh": high_id,
            "similarityPercentage": match["similarityPercentage"],
            "matchType": match["matchType"],
            "modelVersion": model_version,
        }

    def _rebuild_application_summaries(self, session, application_ids: list):
        """Rebuild prepared rows after sentence inserts or deletions."""

        for application_id in application_ids:
            session.execute(
                delete(ApplicationSentenceSummary).where(
                    ApplicationSentenceSummary.applicationId == application_id
                )
            )
            records_exist = session.scalar(
                select(func.count())
                .select_from(SentenceRecord)
                .where(
                    SentenceRecord.applicationId == application_id,
                    SentenceRecord.isTracer.is_(False),
                    SentenceRecord.isFormLanguage.is_(False),
                )
            )
            if not records_exist:
                continue

            overall = (
                session.execute(
                    self._summary_query(
                        SentenceRecord.applicationId == application_id,
                    )
                )
                .mappings()
                .one()
            )
            rows = [
                self._summary_values(
                    application_id,
                    APPLICATION_SCOPE,
                    "",
                    "",
                    overall,
                )
            ]

            analyses = session.execute(
                self._summary_query(
                    SentenceRecord.applicationId == application_id,
                    SentenceRecord.analysisGroup,
                ).group_by(SentenceRecord.analysisGroup)
            ).mappings()
            for analysis in analyses:
                rows.append(
                    self._summary_values(
                        application_id,
                        ANALYSIS_SCOPE,
                        "",
                        analysis["analysisGroup"],
                        analysis,
                    )
                )

            sections = session.execute(
                self._summary_query(
                    SentenceRecord.applicationId == application_id,
                    SentenceRecord.sectionName,
                    SentenceRecord.analysisGroup,
                ).group_by(
                    SentenceRecord.sectionName,
                    SentenceRecord.analysisGroup,
                )
            ).mappings()
            for section in sections:
                rows.append(
                    self._summary_values(
                        application_id,
                        SECTION_SCOPE,
                        section["sectionName"],
                        section["analysisGroup"],
                        section,
                    )
                )
            session.execute(insert(ApplicationSentenceSummary).values(rows))

    @staticmethod
    def _summary_query(condition, *group_columns):
        """Build aggregate columns for matchable sentences only."""

        total_matches = (
            SentenceRecord.exactMatchCount + SentenceRecord.semanticMatchCount
        )
        return select(
            *group_columns,
            func.count(distinct(SentenceRecord.documentId)).label("totalDocuments"),
            func.count(SentenceRecord.globalId).label("totalSentences"),
            func.count(SentenceRecord.globalId)
            .filter(total_matches > 0)
            .label("matchedSentences"),
            func.coalesce(func.sum(SentenceRecord.exactMatchCount), 0).label(
                "exactMatchCount"
            ),
            func.coalesce(func.sum(SentenceRecord.semanticMatchCount), 0).label(
                "semanticMatchCount"
            ),
            func.count(SentenceRecord.globalId)
            .filter(SentenceRecord.matchStatus == "pending")
            .label("pendingCount"),
            func.count(SentenceRecord.globalId)
            .filter(SentenceRecord.matchStatus == "running")
            .label("runningCount"),
            func.count(SentenceRecord.globalId)
            .filter(SentenceRecord.matchStatus == "completed")
            .label("completedCount"),
            func.count(SentenceRecord.globalId)
            .filter(SentenceRecord.matchStatus == "failed")
            .label("failedCount"),
        ).where(
            condition,
            SentenceRecord.isTracer.is_(False),
            SentenceRecord.isFormLanguage.is_(False),
        )

    @staticmethod
    def _summary_values(
        application_id: str,
        scope: str,
        section_name: str,
        analysis_group: str,
        counts,
    ) -> dict:
        """Turn aggregate query values into one summary table row."""

        exact_count = int(counts["exactMatchCount"])
        semantic_count = int(counts["semanticMatchCount"])
        return {
            "applicationId": application_id,
            "summaryScope": scope,
            "sectionName": section_name,
            "analysisGroup": analysis_group,
            "totalDocuments": int(counts["totalDocuments"]),
            "totalSentences": int(counts["totalSentences"]),
            "matchedSentences": int(counts["matchedSentences"]),
            "exactMatchCount": exact_count,
            "semanticMatchCount": semantic_count,
            "totalMatchCount": exact_count + semantic_count,
            "pendingCount": int(counts["pendingCount"]),
            "runningCount": int(counts["runningCount"]),
            "completedCount": int(counts["completedCount"]),
            "failedCount": int(counts["failedCount"]),
            "updatedAt": _now(),
        }

    def application_summary(
        self,
        application_id: str,
        analysis_group: str,
    ) -> dict | None:
        """Return prepared totals for one application and analysis group."""

        with self.sessions() as session:
            overall = session.get(
                ApplicationSentenceSummary,
                (application_id, ANALYSIS_SCOPE, "", analysis_group),
            )
            if not overall:
                return None
            section_rows = session.execute(
                select(ApplicationSentenceSummary)
                .where(
                    ApplicationSentenceSummary.applicationId == application_id,
                    ApplicationSentenceSummary.summaryScope == SECTION_SCOPE,
                    ApplicationSentenceSummary.analysisGroup == analysis_group,
                )
                .order_by(ApplicationSentenceSummary.sectionName)
            ).scalars()
            response = model_values(overall)
            response.pop("summaryScope")
            response.pop("sectionName")
            response["sectionSummaries"] = []
            for row in section_rows:
                section = model_values(row)
                section.pop("applicationId")
                section.pop("summaryScope")
                response["sectionSummaries"].append(section)

        if response["pendingCount"] or response["runningCount"]:
            response["status"] = "processing"
        elif response["failedCount"]:
            response["status"] = "completed_with_failures"
        else:
            response["status"] = "completed"
        return response

    def application_sentences(
        self,
        application_id: str,
        analysis_group: str,
        page_size: int,
        next_token: str | None,
    ) -> dict:
        """List matchable sentences for one application and analysis group."""

        after_global_id = decode_id_token(next_token) if next_token else ""
        with self.sessions() as session:
            rows = (
                session.execute(
                    select(
                        SentenceRecord.globalId,
                        SentenceRecord.tspId,
                        SentenceRecord.documentId,
                        SentenceRecord.sectionName,
                        SentenceRecord.analysisGroup,
                        SentenceRecord.sentIdLocal,
                        SentenceRecord.sentenceContent,
                        SentenceRecord.isTracer,
                        SentenceRecord.isFormLanguage,
                        SentenceRecord.sentenceKey,
                        SentenceRecord.sourceType,
                        SentenceRecord.exactMatchCount,
                        SentenceRecord.semanticMatchCount,
                        SentenceRecord.matchStatus,
                    )
                    .where(
                        SentenceRecord.applicationId == application_id,
                        SentenceRecord.analysisGroup == analysis_group,
                        SentenceRecord.isTracer.is_(False),
                        SentenceRecord.isFormLanguage.is_(False),
                        SentenceRecord.globalId > after_global_id,
                    )
                    .order_by(SentenceRecord.globalId)
                    .limit(page_size + 1)
                )
                .mappings()
                .all()
            )

        has_more = len(rows) > page_size
        sentences = []
        for row in rows[:page_size]:
            item = dict(row)
            item["totalMatchCount"] = int(item["exactMatchCount"]) + int(
                item["semanticMatchCount"]
            )
            sentences.append(item)

        new_token = None
        if has_more and sentences:
            new_token = encode_id_token(sentences[-1]["globalId"])
        return {
            "applicationId": application_id,
            "analysisGroup": analysis_group,
            "pageSize": page_size,
            "sentences": sentences,
            "nextToken": new_token,
        }

    def sentence_matches(
        self,
        global_id: str,
        page_size: int,
        next_token: str | None,
    ) -> dict | None:
        """Read one sentence's prepared relationships with keyset pagination."""

        after_score = None
        after_id = None
        if next_token:
            after_score, after_id = decode_page_token(next_token)

        with self.sessions() as session:
            source = session.get(SentenceRecord, global_id)
            if not source:
                return None

            directed = union_all(
                select(
                    SentenceRelationship.sentenceIdHigh.label("matchedGlobalId"),
                    SentenceRelationship.similarityPercentage,
                    SentenceRelationship.matchType,
                    SentenceRelationship.createdAt,
                ).where(SentenceRelationship.sentenceIdLow == global_id),
                select(
                    SentenceRelationship.sentenceIdLow.label("matchedGlobalId"),
                    SentenceRelationship.similarityPercentage,
                    SentenceRelationship.matchType,
                    SentenceRelationship.createdAt,
                ).where(SentenceRelationship.sentenceIdHigh == global_id),
            ).subquery()

            page_query = (
                select(
                    directed,
                    SentenceRecord.applicationId.label("matchedApplicationId"),
                    SentenceRecord.sentenceKey.label("matchedSentenceKey"),
                    SentenceRecord.sentenceContent,
                    SentenceRecord.tspId,
                    SentenceRecord.documentId,
                    SentenceRecord.sectionName,
                    SentenceRecord.analysisGroup,
                    SentenceRecord.sentIdLocal,
                    SentenceRecord.isTracer,
                    SentenceRecord.isFormLanguage,
                    SentenceRecord.sourceType,
                )
                .join(
                    SentenceRecord,
                    SentenceRecord.globalId == directed.c.matchedGlobalId,
                )
                .order_by(
                    directed.c.similarityPercentage.desc(),
                    directed.c.matchedGlobalId,
                )
                .limit(page_size + 1)
            )
            if after_score is not None:
                page_query = page_query.where(
                    or_(
                        directed.c.similarityPercentage < after_score,
                        and_(
                            directed.c.similarityPercentage == after_score,
                            directed.c.matchedGlobalId > after_id,
                        ),
                    )
                )
            rows = session.execute(page_query).mappings().all()

            source_values = model_values(source)

        has_more = len(rows) > page_size
        page = [dict(row) for row in rows[:page_size]]
        new_token = None
        if has_more and page:
            last = page[-1]
            new_token = encode_page_token(
                float(last["similarityPercentage"]),
                last["matchedGlobalId"],
            )

        return {
            "globalId": source_values["globalId"],
            "applicationId": source_values["applicationId"],
            "sentenceKey": source_values["sentenceKey"],
            "sentenceContent": source_values["sentenceContent"],
            "exactMatchCount": source_values["exactMatchCount"],
            "semanticMatchCount": source_values["semanticMatchCount"],
            "totalMatchCount": (
                source_values["exactMatchCount"] + source_values["semanticMatchCount"]
            ),
            "pageSize": page_size,
            "matches": page,
            "nextToken": new_token,
        }

    def deletion_catalog_keys(
        self,
        application_id: str | None = None,
        tsp_id: str | None = None,
    ) -> list:
        """Return unique catalog keys before a scoped deletion starts."""

        conditions = []
        if application_id is not None:
            conditions.append(SentenceRecord.applicationId == application_id)
        if tsp_id is not None:
            conditions.append(SentenceRecord.tspId == tsp_id)
        if not conditions:
            raise ValueError("applicationId or tspId is required for deletion")
        with self.sessions() as session:
            return list(
                session.scalars(
                    select(distinct(SentenceRecord.sentenceKey)).where(*conditions)
                )
            )

    def delete_sentences(
        self,
        application_id: str | None = None,
        tsp_id: str | None = None,
    ) -> dict:
        """Delete one application scope and repair surviving sentence counts."""

        conditions = []
        if application_id is not None:
            conditions.append(SentenceRecord.applicationId == application_id)
        if tsp_id is not None:
            conditions.append(SentenceRecord.tspId == tsp_id)
        if not conditions:
            raise ValueError("applicationId or tspId is required for deletion")

        with self.sessions.begin() as session:
            # Lock target rows first so a worker cannot create a new relationship
            # while this transaction repairs the surviving sentence counts.
            target_rows = (
                session.execute(
                    select(
                        SentenceRecord.globalId,
                        SentenceRecord.applicationId,
                    )
                    .where(*conditions)
                    .order_by(SentenceRecord.globalId)
                    .with_for_update()
                )
                .mappings()
                .all()
            )
            target_ids = [row["globalId"] for row in target_rows]
            if not target_ids:
                return {
                    "sentencesDeleted": 0,
                    "relationshipsDeleted": 0,
                }

            low_target_survivors = (
                select(
                    SentenceRelationship.sentenceIdHigh.label("globalId"),
                    SentenceRelationship.matchType,
                    func.count().label("count"),
                )
                .where(
                    SentenceRelationship.sentenceIdLow.in_(target_ids),
                    SentenceRelationship.sentenceIdHigh.not_in(target_ids),
                )
                .group_by(
                    SentenceRelationship.sentenceIdHigh,
                    SentenceRelationship.matchType,
                )
            )
            high_target_survivors = (
                select(
                    SentenceRelationship.sentenceIdLow.label("globalId"),
                    SentenceRelationship.matchType,
                    func.count().label("count"),
                )
                .where(
                    SentenceRelationship.sentenceIdHigh.in_(target_ids),
                    SentenceRelationship.sentenceIdLow.not_in(target_ids),
                )
                .group_by(
                    SentenceRelationship.sentenceIdLow,
                    SentenceRelationship.matchType,
                )
            )
            survivor_rows = session.execute(
                union_all(low_target_survivors, high_target_survivors)
            ).mappings()
            survivor_deltas = defaultdict(_empty_match_counts)
            for row in survivor_rows:
                survivor_deltas[row["globalId"]][row["matchType"]] += int(row["count"])

            relationship_delete = session.execute(
                delete(SentenceRelationship)
                .where(
                    or_(
                        SentenceRelationship.sentenceIdLow.in_(target_ids),
                        SentenceRelationship.sentenceIdHigh.in_(target_ids),
                    )
                )
                .execution_options(synchronize_session=False)
            )
            relationships_deleted = relationship_delete.rowcount or 0

            affected_applications = {row["applicationId"] for row in target_rows}
            if survivor_deltas:
                survivors = session.execute(
                    select(SentenceRecord)
                    .where(SentenceRecord.globalId.in_(survivor_deltas))
                    .order_by(SentenceRecord.globalId)
                    .with_for_update()
                ).scalars()
                for survivor in survivors:
                    counts = survivor_deltas[survivor.globalId]
                    survivor.exactMatchCount = max(
                        0,
                        survivor.exactMatchCount - counts["exact"],
                    )
                    survivor.semanticMatchCount = max(
                        0,
                        survivor.semanticMatchCount - counts["semantic"],
                    )
                    affected_applications.add(survivor.applicationId)

            session.execute(
                delete(SentenceRecord).where(SentenceRecord.globalId.in_(target_ids))
            )
            self._rebuild_application_summaries(
                session,
                sorted(affected_applications),
            )

        return {
            "sentencesDeleted": len(target_ids),
            "relationshipsDeleted": int(relationships_deleted),
        }

    def stats(self) -> dict:
        """Count the three PostgreSQL tables and worker states."""

        with self.sessions() as session:
            result = {
                "sentenceRecords": session.scalar(
                    select(func.count()).select_from(SentenceRecord)
                ),
                "sentenceRelationships": session.scalar(
                    select(func.count()).select_from(SentenceRelationship)
                ),
                "applicationSummaryRows": session.scalar(
                    select(func.count()).select_from(ApplicationSentenceSummary)
                ),
            }
            status_rows = session.execute(
                select(
                    SentenceRecord.matchStatus,
                    func.count(SentenceRecord.globalId).label("count"),
                ).group_by(SentenceRecord.matchStatus)
            ).mappings()
            result["jobs"] = {
                row["matchStatus"]: int(row["count"]) for row in status_rows
            }
        return result
