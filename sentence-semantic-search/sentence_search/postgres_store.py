import bisect
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError, TimeoutError
from sqlalchemy.orm import sessionmaker

from sentence_search.config import Config
from sentence_search.database_models import (
    ApplicationMatchSummary,
    Base,
    SentenceMatch,
    model_values,
)
from sentence_search.matching_rules import is_matchable_sentence
from sentence_search.search_utils import decode_id_token, encode_id_token

MATCH_WRITE_LOCK = 907_202_609  # Serializes connected-group array updates.


def _now() -> datetime:
    """Return the current UTC time for worker locks and retries."""

    return datetime.now(UTC)


def complete_match_lists(global_ids: set) -> dict:
    """Build the symmetric match array for every ID in one connected group.

    Input: global_ids={"1", "10", "40"}
    Output: ID 1 has ["10", "40"], and every other ID gets the same group.
    """

    ordered_ids = sorted(global_ids)
    return {
        global_id: [item for item in ordered_ids if item != global_id]
        for global_id in ordered_ids
    }


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
        """Create the two simple PostgreSQL tables and their indexes."""

        Base.metadata.create_all(self.engine)

    def reset_data(self):
        """Delete every saved match list and application summary."""

        with self.sessions.begin() as session:
            session.execute(delete(ApplicationMatchSummary))
            session.execute(delete(SentenceMatch))

    def register_sentences(self, records: list, threshold: int) -> dict:
        """Create one match-list row for each new global ID."""

        if not records:
            return {"registered": 0, "jobsQueued": 0}

        unique_records = {}
        for record in records:
            unique_records[record["globalId"]] = record
        records = list(unique_records.values())

        with self.sessions.begin() as session:
            existing_ids = self._validate_sentence_identity(session, records)
            match_insert = insert(SentenceMatch).values(
                [self._match_values(record, threshold) for record in records]
            )
            session.execute(
                match_insert.on_conflict_do_update(
                    index_elements=[SentenceMatch.globalId],
                    set_={"updatedAt": match_insert.excluded.updatedAt},
                )
            )
            summary_rows = {
                (record["applicationId"], record["analysisGroup"]) for record in records
            }
            if summary_rows:
                summary_insert = insert(ApplicationMatchSummary).values(
                    [
                        {
                            "applicationId": application_id,
                            "analysisGroup": analysis_group,
                            "sectionMatchCounts": {},
                            "totalMatching": 0,
                            "updatedAt": _now(),
                        }
                        for application_id, analysis_group in sorted(summary_rows)
                    ]
                )
                session.execute(summary_insert.on_conflict_do_nothing())

        jobs_queued = sum(
            record["globalId"] not in existing_ids and is_matchable_sentence(record)
            for record in records
        )
        return {"registered": len(records), "jobsQueued": jobs_queued}

    @staticmethod
    def _match_values(record: dict, threshold: int) -> dict:
        """Build one new match-list row from a validated sentence."""

        status = "pending" if is_matchable_sentence(record) else "skipped"
        return {
            "globalId": record["globalId"],
            "tspId": record["tspId"],
            "applicationId": record["applicationId"],
            "sectionName": record["sectionName"],
            "analysisGroup": record["analysisGroup"],
            "matchingGlobalIds": [],
            "matchStatus": status,
            "matchAttempts": 0,
            "matchThreshold": threshold,
            "matchAvailableAt": _now(),
            "createdAt": record["createdAt"],
            "updatedAt": record["updatedAt"],
        }

    def validate_sentence_identity(self, records: list):
        """Reject metadata changes before OpenSearch is updated."""

        if not records:
            return
        with self.sessions() as session:
            self._validate_sentence_identity(session, records)

    @staticmethod
    def _validate_sentence_identity(session, records: list) -> set:
        """Keep one global ID tied to stable matching metadata."""

        incoming = {}
        for record in records:
            global_id = record["globalId"]
            identity = {
                "applicationId": record["applicationId"],
                "tspId": record["tspId"],
                "sectionName": record["sectionName"],
                "analysisGroup": record["analysisGroup"],
                "skipped": not is_matchable_sentence(record),
            }
            previous = incoming.get(global_id)
            if previous and previous != identity:
                raise ValueError(
                    f"The same globalId has different metadata in one batch: {global_id}"
                )
            incoming[global_id] = identity

        rows = session.execute(
            select(
                SentenceMatch.globalId,
                SentenceMatch.applicationId,
                SentenceMatch.tspId,
                SentenceMatch.sectionName,
                SentenceMatch.analysisGroup,
                SentenceMatch.matchStatus,
            ).where(SentenceMatch.globalId.in_(incoming))
        ).mappings()
        existing_ids = set()
        for row in rows:
            global_id = row["globalId"]
            existing_ids.add(global_id)
            expected = incoming[global_id]
            actual = {
                "applicationId": row["applicationId"],
                "tspId": row["tspId"],
                "sectionName": row["sectionName"],
                "analysisGroup": row["analysisGroup"],
                "skipped": row["matchStatus"] == "skipped",
            }
            if actual != expected:
                raise ValueError(
                    f"globalId cannot change matching metadata without reset: {global_id}"
                )
        return existing_ids

    def claim_job(self) -> dict | None:
        """Lock and return one pending sentence for the worker."""

        with self.sessions.begin() as session:
            global_id = session.execute(
                select(SentenceMatch.globalId)
                .where(
                    SentenceMatch.matchStatus == "pending",
                    SentenceMatch.matchAvailableAt <= _now(),
                )
                .order_by(SentenceMatch.matchAvailableAt, SentenceMatch.globalId)
                .with_for_update(skip_locked=True)
                .limit(1)
            ).scalar_one_or_none()
            if global_id is None:
                return None

            row = (
                session.execute(
                    update(SentenceMatch)
                    .where(SentenceMatch.globalId == global_id)
                    .values(
                        matchStatus="running",
                        matchAttempts=SentenceMatch.matchAttempts + 1,
                        matchLockedAt=_now(),
                        matchLastError=None,
                    )
                    .returning(*SentenceMatch.__table__.columns)
                )
                .mappings()
                .one()
            )
            return dict(row)

    def requeue_stale_jobs(self):
        """Return abandoned running rows to the pending queue."""

        stale_time = _now() - timedelta(minutes=10)
        with self.sessions.begin() as session:
            session.execute(
                update(SentenceMatch)
                .where(
                    SentenceMatch.matchStatus == "running",
                    SentenceMatch.matchLockedAt < stale_time,
                )
                .values(
                    matchStatus="pending",
                    matchAvailableAt=_now(),
                    matchLockedAt=None,
                    matchLastError="Worker stopped before completing the job",
                )
            )

    def complete_job(self, global_id: str):
        """Mark one sentence's background work as successful."""

        with self.sessions.begin() as session:
            session.execute(
                update(SentenceMatch)
                .where(SentenceMatch.globalId == global_id)
                .values(
                    matchStatus="completed",
                    matchAttempts=0,
                    matchLockedAt=None,
                    matchLastError=None,
                    updatedAt=_now(),
                )
            )

    def retry_failed_jobs(self) -> int:
        """Put stopped rows back in the worker queue."""

        with self.sessions.begin() as session:
            result = session.execute(
                update(SentenceMatch)
                .where(SentenceMatch.matchStatus == "failed")
                .values(
                    matchStatus="pending",
                    matchAttempts=0,
                    matchAvailableAt=_now(),
                    matchLockedAt=None,
                    matchLastError=None,
                )
            )
        return result.rowcount or 0

    def fail_job(self, job: dict, error: Exception):
        """Retry one sentence or stop it after the configured limit."""

        final_failure = job["matchAttempts"] >= self.config.worker_max_attempts
        status = "failed" if final_failure else "pending"
        available_at = _now()
        if not final_failure:
            available_at += timedelta(seconds=self.config.worker_retry_seconds)
        with self.sessions.begin() as session:
            session.execute(
                update(SentenceMatch)
                .where(SentenceMatch.globalId == job["globalId"])
                .values(
                    matchStatus=status,
                    matchAvailableAt=available_at,
                    matchLockedAt=None,
                    matchLastError=str(error)[:4_000],
                )
            )

    def save_match_group(self, source_global_id: str, target_global_ids: list) -> dict:
        """Merge direct vector hits into one symmetric connected match group."""

        direct_targets = set(target_global_ids)
        direct_targets.discard(source_global_id)
        if not direct_targets:
            return {
                "directMatchesFound": 0,
                "matchGroupSize": 1,
                "matchListsUpdated": 0,
            }

        with self.sessions.begin() as session:
            # Updating a connected group touches many rows. One transaction lock
            # prevents two workers from partially overwriting the same arrays.
            session.execute(select(func.pg_advisory_xact_lock(MATCH_WRITE_LOCK)))
            member_ids = {source_global_id, *direct_targets}
            rows_by_id = {}

            while True:
                rows = (
                    session.execute(
                        select(SentenceMatch)
                        .where(
                            SentenceMatch.globalId.in_(member_ids),
                            SentenceMatch.matchStatus != "skipped",
                        )
                        .order_by(SentenceMatch.globalId)
                        .with_for_update()
                    )
                    .scalars()
                    .all()
                )
                rows_by_id = {row.globalId: row for row in rows}
                expanded_ids = set(rows_by_id)
                for row in rows:
                    expanded_ids.update(row.matchingGlobalIds)
                if expanded_ids == member_ids:
                    break
                member_ids = expanded_ids

            if source_global_id not in rows_by_id:
                raise RuntimeError(
                    f"Match row does not exist for source: {source_global_id}"
                )

            cluster_ids = set(rows_by_id)
            match_lists = complete_match_lists(cluster_ids)
            summary_deltas = defaultdict(int)
            updated_rows = 0
            for row in rows_by_id.values():
                new_matches = match_lists[row.globalId]
                old_count = len(row.matchingGlobalIds)
                if new_matches == row.matchingGlobalIds:
                    continue
                row.matchingGlobalIds = new_matches
                row.updatedAt = _now()
                updated_rows += 1
                summary_deltas[
                    (row.applicationId, row.analysisGroup, row.sectionName)
                ] += len(new_matches) - old_count

            self._apply_summary_deltas(session, summary_deltas)

        return {
            "directMatchesFound": len(direct_targets & cluster_ids),
            "matchGroupSize": len(cluster_ids),
            "matchListsUpdated": updated_rows,
        }

    @staticmethod
    def _apply_summary_deltas(session, deltas: dict):
        """Add match-list size changes to application and section totals."""

        for key, delta in deltas.items():
            if not delta:
                continue
            application_id, analysis_group, section_name = key
            summary = session.get(
                ApplicationMatchSummary,
                (application_id, analysis_group),
                with_for_update=True,
            )
            if summary is None:
                summary = ApplicationMatchSummary(
                    applicationId=application_id,
                    analysisGroup=analysis_group,
                    sectionMatchCounts={},
                    totalMatching=0,
                    updatedAt=_now(),
                )
                session.add(summary)
                session.flush()

            section_counts = dict(summary.sectionMatchCounts)
            section_counts[section_name] = max(
                0,
                int(section_counts.get(section_name, 0)) + delta,
            )
            summary.sectionMatchCounts = section_counts
            summary.totalMatching = max(0, summary.totalMatching + delta)
            summary.updatedAt = _now()

    def application_summary(
        self,
        application_id: str,
        analysis_group: str,
    ) -> dict | None:
        """Return one ready-to-display application summary row."""

        with self.sessions() as session:
            summary = session.get(
                ApplicationMatchSummary,
                (application_id, analysis_group),
            )
            if summary is None:
                return None
            values = model_values(summary)

        values["sectionMatches"] = [
            {"sectionName": section_name, "matchingCount": int(count)}
            for section_name, count in sorted(values.pop("sectionMatchCounts").items())
        ]
        return values

    def application_sentences(
        self,
        application_id: str,
        analysis_group: str,
        page_size: int,
        next_token: str | None,
    ) -> dict:
        """List sentence IDs and their prepared match-list counts."""

        after_global_id = decode_id_token(next_token) if next_token else ""
        matching_count = func.cardinality(SentenceMatch.matchingGlobalIds).label(
            "matchingCount"
        )
        with self.sessions() as session:
            rows = (
                session.execute(
                    select(
                        SentenceMatch.globalId,
                        SentenceMatch.tspId,
                        SentenceMatch.applicationId,
                        SentenceMatch.sectionName,
                        SentenceMatch.analysisGroup,
                        matching_count,
                        SentenceMatch.matchStatus,
                    )
                    .where(
                        SentenceMatch.applicationId == application_id,
                        SentenceMatch.analysisGroup == analysis_group,
                        SentenceMatch.matchStatus != "skipped",
                        SentenceMatch.globalId > after_global_id,
                    )
                    .order_by(SentenceMatch.globalId)
                    .limit(page_size + 1)
                )
                .mappings()
                .all()
            )

        page = [dict(row) for row in rows[:page_size]]
        next_page_token = None
        if len(rows) > page_size and page:
            next_page_token = encode_id_token(page[-1]["globalId"])
        return {
            "applicationId": application_id,
            "analysisGroup": analysis_group,
            "pageSize": page_size,
            "sentences": page,
            "nextToken": next_page_token,
        }

    def sentence_matches(
        self,
        global_id: str,
        page_size: int,
        next_token: str | None,
    ) -> dict | None:
        """Return one sentence's stored match-list page."""

        after_global_id = decode_id_token(next_token) if next_token else ""
        with self.sessions() as session:
            source = session.get(SentenceMatch, global_id)
            if source is None:
                return None
            all_matches = source.matchingGlobalIds
            start = bisect.bisect_right(all_matches, after_global_id)
            page_ids = all_matches[start : start + page_size]
            has_more = start + page_size < len(all_matches)
            target_rows = session.execute(
                select(
                    SentenceMatch.globalId,
                    SentenceMatch.tspId,
                    SentenceMatch.applicationId,
                    SentenceMatch.sectionName,
                    SentenceMatch.analysisGroup,
                ).where(SentenceMatch.globalId.in_(page_ids))
            ).mappings()
            target_by_id = {row["globalId"]: dict(row) for row in target_rows}
            matches = [target_by_id[item] for item in page_ids if item in target_by_id]
            source_values = model_values(source)

        next_page_token = None
        if has_more and page_ids:
            next_page_token = encode_id_token(page_ids[-1])
        return {
            "globalId": source_values["globalId"],
            "tspId": source_values["tspId"],
            "applicationId": source_values["applicationId"],
            "sectionName": source_values["sectionName"],
            "analysisGroup": source_values["analysisGroup"],
            "matchingCount": len(all_matches),
            "pageSize": page_size,
            "matches": matches,
            "nextToken": next_page_token,
        }

    def delete_sentences(
        self,
        application_id: str | None = None,
        tsp_id: str | None = None,
    ) -> dict:
        """Delete scoped rows and remove their IDs from every surviving list."""

        conditions = []
        if application_id is not None:
            conditions.append(SentenceMatch.applicationId == application_id)
        if tsp_id is not None:
            conditions.append(SentenceMatch.tspId == tsp_id)
        if not conditions:
            raise ValueError("applicationId or tspId is required for deletion")

        with self.sessions.begin() as session:
            session.execute(select(func.pg_advisory_xact_lock(MATCH_WRITE_LOCK)))
            target_rows = (
                session.execute(
                    select(SentenceMatch).where(*conditions).with_for_update()
                )
                .scalars()
                .all()
            )
            target_ids = {row.globalId for row in target_rows}
            if not target_ids:
                return {"sentencesDeleted": 0, "matchListsUpdated": 0}

            affected_applications = {row.applicationId for row in target_rows}
            survivors = (
                session.execute(
                    select(SentenceMatch)
                    .where(SentenceMatch.matchingGlobalIds.overlap(list(target_ids)))
                    .with_for_update()
                )
                .scalars()
                .all()
            )
            survivor_updates = 0
            for survivor in survivors:
                if survivor.globalId in target_ids:
                    continue
                new_matches = [
                    item
                    for item in survivor.matchingGlobalIds
                    if item not in target_ids
                ]
                if new_matches != survivor.matchingGlobalIds:
                    survivor.matchingGlobalIds = new_matches
                    survivor.updatedAt = _now()
                    survivor_updates += 1
                    affected_applications.add(survivor.applicationId)

            session.execute(
                delete(SentenceMatch).where(SentenceMatch.globalId.in_(target_ids))
            )
            self._rebuild_summaries(session, affected_applications)

        return {
            "sentencesDeleted": len(target_ids),
            "matchListsUpdated": survivor_updates,
        }

    @staticmethod
    def _rebuild_summaries(session, application_ids: set):
        """Recalculate simple section totals after a deletion."""

        if not application_ids:
            return
        session.execute(
            delete(ApplicationMatchSummary).where(
                ApplicationMatchSummary.applicationId.in_(application_ids)
            )
        )
        rows = session.execute(
            select(
                SentenceMatch.applicationId,
                SentenceMatch.analysisGroup,
                SentenceMatch.sectionName,
                func.sum(func.cardinality(SentenceMatch.matchingGlobalIds)).label(
                    "matchingCount"
                ),
            )
            .where(SentenceMatch.applicationId.in_(application_ids))
            .group_by(
                SentenceMatch.applicationId,
                SentenceMatch.analysisGroup,
                SentenceMatch.sectionName,
            )
        ).mappings()
        summaries = {}
        for row in rows:
            key = (row["applicationId"], row["analysisGroup"])
            summary = summaries.setdefault(
                key,
                {"sectionMatchCounts": {}, "totalMatching": 0},
            )
            count = int(row["matchingCount"] or 0)
            summary["sectionMatchCounts"][row["sectionName"]] = count
            summary["totalMatching"] += count

        if summaries:
            session.execute(
                insert(ApplicationMatchSummary).values(
                    [
                        {
                            "applicationId": key[0],
                            "analysisGroup": key[1],
                            "sectionMatchCounts": value["sectionMatchCounts"],
                            "totalMatching": value["totalMatching"],
                            "updatedAt": _now(),
                        }
                        for key, value in summaries.items()
                    ]
                )
            )

    def stats(self) -> dict:
        """Count the two PostgreSQL tables and worker states."""

        with self.sessions() as session:
            status_rows = session.execute(
                select(
                    SentenceMatch.matchStatus,
                    func.count(SentenceMatch.globalId).label("count"),
                ).group_by(SentenceMatch.matchStatus)
            ).mappings()
            return {
                "sentenceMatches": session.scalar(
                    select(func.count()).select_from(SentenceMatch)
                ),
                "applicationSummaries": session.scalar(
                    select(func.count()).select_from(ApplicationMatchSummary)
                ),
                "jobs": {row["matchStatus"]: int(row["count"]) for row in status_rows},
            }
