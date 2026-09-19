import time
from datetime import UTC, datetime

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError, TimeoutError
from sqlalchemy.orm import sessionmaker

from sentence_search.config import Config
from sentence_search.database_models import (
    ApplicationMatchSummary,
    Base,
    SentenceKeyMatch,
)

MATCH_WRITE_LOCK = 907_202_609  # Serializes bidirectional key-list updates.


def _now() -> datetime:
    """Return the current UTC time for saved rows."""

    return datetime.now(UTC)


def add_direct_key_matches(existing: dict, source_key: str, target_keys: list) -> dict:
    """Replace one key's direct matches and keep every reverse link in sync.

    Input: A used to match B, but now A directly matches D.
    Output: A has D, D has A, and B no longer has A.
    """

    updated = {key: set(values) for key, values in existing.items()}
    updated.setdefault(source_key, set())
    new_targets = {key for key in target_keys if key != source_key}
    old_targets = set(updated[source_key])

    for removed_key in old_targets - new_targets:
        if removed_key in updated:
            updated[removed_key].discard(source_key)
    updated[source_key] = new_targets
    for target_key in new_targets:
        updated.setdefault(target_key, set()).add(source_key)
    return {key: sorted(values) for key, values in updated.items()}


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
        """Create both tables and upgrade a project made by the old version."""

        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.execute(
                sql_text('DROP INDEX IF EXISTS "sentence_key_matches_job_idx"')
            )
            connection.execute(
                sql_text('DROP INDEX IF EXISTS "application_match_summary_job_idx"')
            )
            old_columns = {
                "sentence_key_matches": (
                    "matchStatus",
                    "matchAttempts",
                    "matchThreshold",
                    "modelVersion",
                    "matchAvailableAt",
                    "matchLockedAt",
                    "matchLastError",
                ),
                "application_match_summary": (
                    "summaryStatus",
                    "summaryAttempts",
                    "summaryAvailableAt",
                    "summaryLockedAt",
                    "summaryLastError",
                ),
            }
            preparer = connection.dialect.identifier_preparer
            for table_name, column_names in old_columns.items():
                quoted_table = preparer.quote(table_name)
                for column_name in column_names:
                    quoted_column = preparer.quote(column_name)
                    connection.execute(
                        sql_text(
                            f"ALTER TABLE {quoted_table} "
                            f"DROP COLUMN IF EXISTS {quoted_column} CASCADE"
                        )
                    )

    def reset_data(self):
        """Delete every saved relationship and application summary."""

        with self.sessions.begin() as session:
            session.execute(delete(ApplicationMatchSummary))
            session.execute(delete(SentenceKeyMatch))

    def register_sentence_keys(
        self,
        records: list,
    ) -> dict:
        """Create one simple relationship row per unique sentence key.

        Input: many occurrence records, including repeated sentence text.
        Output: the unique-key count and which keys were newly inserted.
        """

        if not records:
            return {"registered": 0, "newSentenceKeys": []}

        unique_keys = sorted({record["sentenceKey"] for record in records})
        current_time = _now()
        values = [
            {
                "sentenceKey": key,
                "matchingSentenceKeys": [],
                "createdAt": current_time,
                "updatedAt": current_time,
            }
            for key in unique_keys
        ]
        with self.sessions.begin() as session:
            statement = insert(SentenceKeyMatch).values(values)
            inserted_keys = list(
                session.execute(
                    statement.on_conflict_do_nothing().returning(
                        SentenceKeyMatch.sentenceKey
                    )
                ).scalars()
            )

        return {
            "registered": len(unique_keys),
            "newSentenceKeys": inserted_keys,
        }

    def save_key_matches(self, source_key: str, target_keys: list) -> dict:
        """Save direct key matches on both sides in one database transaction.

        Input: source D directly matches A and B.
        Output: D stores A/B, while A and B each store D.
        """

        direct_targets = set(target_keys)
        direct_targets.discard(source_key)

        with self.sessions.begin() as session:
            # Two API requests can discover the same pair at once. One lock
            # keeps both array sides from overwriting one another.
            session.execute(select(func.pg_advisory_xact_lock(MATCH_WRITE_LOCK)))
            source_row = session.execute(
                select(SentenceKeyMatch)
                .where(SentenceKeyMatch.sentenceKey == source_key)
                .with_for_update()
            ).scalar_one_or_none()
            if source_row is None:
                raise RuntimeError(f"Sentence-key row does not exist: {source_key}")

            old_targets = set(source_row.matchingSentenceKeys)
            requested_keys = {source_key, *old_targets, *direct_targets}
            rows = (
                session.execute(
                    select(SentenceKeyMatch)
                    .where(SentenceKeyMatch.sentenceKey.in_(requested_keys))
                    .order_by(SentenceKeyMatch.sentenceKey)
                    .with_for_update()
                )
                .scalars()
                .all()
            )
            rows_by_key = {row.sentenceKey: row for row in rows}

            # A stale catalog document may not have a PostgreSQL key row.
            saved_targets = direct_targets & set(rows_by_key)
            current_lists = {
                key: row.matchingSentenceKeys for key, row in rows_by_key.items()
            }
            new_lists = add_direct_key_matches(
                current_lists,
                source_key,
                sorted(saved_targets),
            )

            updated_count = 0
            for key, new_matches in new_lists.items():
                row = rows_by_key[key]
                if row.matchingSentenceKeys == new_matches:
                    continue
                row.matchingSentenceKeys = new_matches
                row.updatedAt = _now()
                updated_count += 1

        return {
            "directMatchesFound": len(saved_targets),
            "keyListsUpdated": updated_count,
            "affectedSentenceKeys": sorted({source_key, *old_targets, *saved_targets}),
        }

    def matching_keys(self, sentence_keys: list) -> dict:
        """Return the direct matching-key list for each requested key."""

        unique_keys = set(sentence_keys)
        if not unique_keys:
            return {}
        with self.sessions() as session:
            rows = session.execute(
                select(
                    SentenceKeyMatch.sentenceKey,
                    SentenceKeyMatch.matchingSentenceKeys,
                ).where(SentenceKeyMatch.sentenceKey.in_(unique_keys))
            ).mappings()
            found = {
                row["sentenceKey"]: {
                    "matchingSentenceKeys": list(row["matchingSentenceKeys"]),
                }
                for row in rows
            }
        for key in unique_keys:
            found.setdefault(
                key,
                {"matchingSentenceKeys": []},
            )
        return found

    def save_application_summary(
        self,
        application_id: str,
        analysis_group: str,
        section_match_counts: dict,
        total_matching: int,
    ):
        """Insert or replace the finished counts for one application.

        Input: A1, Asylee, {Affidavit: 10}, and total 10.
        Output: one summary row ready for the API to read.
        """

        statement = insert(ApplicationMatchSummary).values(
            applicationId=application_id,
            analysisGroup=analysis_group,
            sectionMatchCounts=section_match_counts,
            totalMatching=total_matching,
            updatedAt=_now(),
        )
        with self.sessions.begin() as session:
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        ApplicationMatchSummary.applicationId,
                        ApplicationMatchSummary.analysisGroup,
                    ],
                    set_={
                        "sectionMatchCounts": section_match_counts,
                        "totalMatching": total_matching,
                        "updatedAt": _now(),
                    },
                )
            )

    def application_summary(
        self,
        application_id: str,
        analysis_group: str,
    ) -> dict:
        """Return the saved application summary."""

        with self.sessions() as session:
            row = session.get(
                ApplicationMatchSummary,
                (application_id, analysis_group),
            )
            if row is None:
                return {
                    "totalMatching": 0,
                    "sectionMatches": [],
                    "updatedAt": None,
                }
            section_counts = dict(row.sectionMatchCounts)
            return {
                "totalMatching": int(row.totalMatching),
                "sectionMatches": [
                    {
                        "sectionName": section_name,
                        "matchingCount": int(count),
                    }
                    for section_name, count in sorted(section_counts.items())
                ],
                "updatedAt": row.updatedAt,
            }

    def delete_application_summaries(self, application_id: str) -> int:
        """Delete every saved analysis-group summary for one application."""

        with self.sessions.begin() as session:
            result = session.execute(
                delete(ApplicationMatchSummary).where(
                    ApplicationMatchSummary.applicationId == application_id
                )
            )
        return result.rowcount or 0

    def delete_keys(self, sentence_keys: list) -> dict:
        """Delete unused keys and remove them from every surviving key list."""

        target_keys = set(sentence_keys)
        if not target_keys:
            return {"keyRowsDeleted": 0, "keyListsUpdated": 0}

        with self.sessions.begin() as session:
            session.execute(select(func.pg_advisory_xact_lock(MATCH_WRITE_LOCK)))
            survivors = (
                session.execute(
                    select(SentenceKeyMatch)
                    .where(
                        SentenceKeyMatch.matchingSentenceKeys.overlap(list(target_keys))
                    )
                    .with_for_update()
                )
                .scalars()
                .all()
            )
            updated_count = 0
            for survivor in survivors:
                if survivor.sentenceKey in target_keys:
                    continue
                new_matches = [
                    key
                    for key in survivor.matchingSentenceKeys
                    if key not in target_keys
                ]
                if new_matches == survivor.matchingSentenceKeys:
                    continue
                survivor.matchingSentenceKeys = new_matches
                survivor.updatedAt = _now()
                updated_count += 1

            result = session.execute(
                delete(SentenceKeyMatch).where(
                    SentenceKeyMatch.sentenceKey.in_(target_keys)
                )
            )

        return {
            "keyRowsDeleted": result.rowcount or 0,
            "keyListsUpdated": updated_count,
        }

    def stats(self) -> dict:
        """Count both PostgreSQL tables."""

        with self.sessions() as session:
            return {
                "sentenceKeyMatches": session.scalar(
                    select(func.count()).select_from(SentenceKeyMatch)
                ),
                "applicationMatchSummaries": session.scalar(
                    select(func.count()).select_from(ApplicationMatchSummary)
                ),
            }
