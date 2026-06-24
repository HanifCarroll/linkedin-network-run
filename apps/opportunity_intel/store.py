"""SQLite state for recommend-only opportunity discovery."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from apps.opportunity_intel.contracts import (
    CommentEvidence,
    QueryPack,
    RankedComment,
    RejectReason,
    ReviewLabel,
    SourceDefinition,
    SourceRegistry,
)
from apps.opportunity_intel.post_discovery import PostCandidate
from apps.opportunity_intel.ranking import rank_comment
from packages.linkedin_common.paths import DEFAULT_STATE_ROOT
from packages.linkedin_storage.sqlite import connect_sqlite

APP_DIR = "opportunity-intel"
DATABASE_NAME = "opportunity.sqlite"


@dataclass(frozen=True)
class StoredReviewLabel:
    label: ReviewLabel
    reject_reason: RejectReason | None
    notes: str
    updated_at: str


class OpportunityStore:
    """Durable SQLite store for opportunity sources, extraction, rankings, and review."""

    def __init__(self, state_dir: str | Path | None = None) -> None:
        self.dir = Path(state_dir) if state_dir is not None else DEFAULT_STATE_ROOT / APP_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    @property
    def database_path(self) -> Path:
        return self.dir / DATABASE_NAME

    @property
    def artifact_dir(self) -> Path:
        return self.dir / "artifacts"

    def ensure_schema(self) -> None:
        with self._open_db() as db:
            db.executescript(SCHEMA_SQL)

    def sync_source_registry(self, registry: SourceRegistry) -> None:
        now = _now_iso()
        with self._open_db() as db:
            with db:
                for source in registry.sources:
                    db.execute(
                        """
                        INSERT INTO sources(
                          source_id, source_kind, title, description, enabled, priority,
                          query_ids_json, urls_json, search_queries_json, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(source_id) DO UPDATE SET
                          source_kind = excluded.source_kind,
                          title = excluded.title,
                          description = excluded.description,
                          enabled = excluded.enabled,
                          priority = excluded.priority,
                          query_ids_json = excluded.query_ids_json,
                          urls_json = excluded.urls_json,
                          search_queries_json = excluded.search_queries_json,
                          updated_at = excluded.updated_at
                        """,
                        _source_params(source, now),
                    )

    def sync_post_candidates(self, candidates: Sequence[PostCandidate]) -> None:
        now = _now_iso()
        with self._open_db() as db:
            with db:
                for candidate in candidates:
                    post_id = post_candidate_key(candidate)
                    db.execute(
                        """
                        INSERT INTO posts(
                          post_id, post_url, source_id, source_kind, query_id, source_url,
                          search_query, priority, reason, extraction_status, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(post_id) DO UPDATE SET
                          post_url = excluded.post_url,
                          source_id = excluded.source_id,
                          source_kind = excluded.source_kind,
                          query_id = excluded.query_id,
                          source_url = excluded.source_url,
                          search_query = excluded.search_query,
                          priority = excluded.priority,
                          reason = excluded.reason,
                          updated_at = excluded.updated_at
                        """,
                        (
                            post_id,
                            candidate.post_url,
                            candidate.source_id,
                            candidate.source_kind,
                            candidate.query_id,
                            candidate.source_url,
                            candidate.search_query,
                            candidate.priority,
                            candidate.reason,
                            "queued" if candidate.post_url else "source",
                            now,
                            now,
                        ),
                    )

    def start_extraction_run(
        self,
        *,
        post_url: str,
        source_id: str,
        query_id: str,
        source_kind: str,
        source_url: str,
        search_query: str,
        browser_profile: str,
        safety_limits: object,
        status: str = "running",
    ) -> str:
        run_id = "run_" + uuid.uuid4().hex
        now = _now_iso()
        with self._open_db() as db:
            with db:
                db.execute(
                    """
                    INSERT INTO extraction_runs(
                      run_id, post_url, source_id, query_id, source_kind, source_url,
                      search_query, status, started_at, browser_profile, safety_limits_json,
                      retry_recommendation
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        post_url,
                        source_id,
                        query_id,
                        source_kind,
                        source_url,
                        search_query,
                        status,
                        now,
                        browser_profile,
                        _json_dumps(safety_limits),
                        "",
                    ),
                )
                if post_url:
                    db.execute(
                        """
                        UPDATE posts
                        SET extraction_status = ?, latest_extraction_run_id = ?, updated_at = ?
                        WHERE post_url = ?
                        """,
                        (status, run_id, now, post_url),
                    )
                    self._record_transition(
                        db,
                        entity_type="post",
                        entity_id=post_url,
                        from_status="",
                        to_status=status,
                        reason="extraction run started",
                        metadata={"run_id": run_id},
                        created_at=now,
                    )
        return run_id

    def finish_extraction_run(
        self,
        run_id: str,
        *,
        status: str,
        comments_found: int,
        failures: int,
        warning_count: int,
        retry_recommendation: str,
    ) -> None:
        now = _now_iso()
        with self._open_db() as db:
            with db:
                row = db.execute(
                    "SELECT post_url, status FROM extraction_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"unknown extraction run: {run_id}")
                old_status = str(row["status"])
                db.execute(
                    """
                    UPDATE extraction_runs
                    SET status = ?, finished_at = ?, comments_found = ?, failures = ?,
                        warning_count = ?, retry_recommendation = ?
                    WHERE run_id = ?
                    """,
                    (
                        status,
                        now,
                        comments_found,
                        failures,
                        warning_count,
                        retry_recommendation,
                        run_id,
                    ),
                )
                post_url = str(row["post_url"] or "")
                if post_url:
                    db.execute(
                        """
                        UPDATE posts
                        SET extraction_status = ?, updated_at = ?
                        WHERE post_url = ?
                        """,
                        (status, now, post_url),
                    )
                    self._record_transition(
                        db,
                        entity_type="post",
                        entity_id=post_url,
                        from_status=old_status,
                        to_status=status,
                        reason="extraction run finished",
                        metadata={"run_id": run_id, "comments_found": comments_found},
                        created_at=now,
                    )

    def record_artifact(
        self,
        *,
        run_id: str,
        kind: str,
        path: Path,
        status: str = "ok",
        retryable_error: str = "",
        metadata: object | None = None,
    ) -> str:
        artifact_id = "artifact_" + uuid.uuid4().hex
        with self._open_db() as db:
            with db:
                db.execute(
                    """
                    INSERT INTO extraction_artifacts(
                      artifact_id, run_id, app, kind, path, status, retryable_error,
                      created_at, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        run_id,
                        "opportunity",
                        kind,
                        str(path),
                        status,
                        retryable_error,
                        _now_iso(),
                        _json_dumps(metadata or {}),
                    ),
                )
                if kind in {"html", "raw_comments"}:
                    db.execute(
                        """
                        UPDATE posts
                        SET artifact_path = ?, updated_at = ?
                        WHERE post_url = (
                          SELECT post_url FROM extraction_runs WHERE run_id = ?
                        )
                        """,
                        (str(path), _now_iso(), run_id),
                    )
        return artifact_id

    def record_error(
        self,
        *,
        run_id: str,
        post_url: str,
        error_type: str,
        message: str,
        retryable: bool,
    ) -> None:
        with self._open_db() as db:
            with db:
                db.execute(
                    """
                    INSERT INTO extraction_errors(
                      error_id, run_id, post_url, error_type, message, retryable, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "error_" + uuid.uuid4().hex,
                        run_id,
                        post_url,
                        error_type,
                        message,
                        int(retryable),
                        _now_iso(),
                    ),
                )

    def persist_comments(
        self,
        *,
        run_id: str,
        comments: Sequence[CommentEvidence],
        query_pack: QueryPack,
    ) -> tuple[RankedComment, ...]:
        now = _now_iso()
        ranked_comments = tuple(
            rank_comment(comment, query_pack.require_query(comment.query_id))
            for comment in comments
        )
        with self._open_db() as db:
            with db:
                for ranked in ranked_comments:
                    comment = ranked.comment
                    post_id = stable_post_key(comment.post_url)
                    person_id = stable_person_key(comment.commenter_profile_url)
                    comment_key = stable_comment_key(comment)
                    db.execute(
                        """
                        INSERT INTO posts(
                          post_id, post_url, source_id, source_kind, query_id, source_url,
                          search_query, post_author_name, post_text, extraction_status,
                          latest_extraction_run_id, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(post_id) DO UPDATE SET
                          source_id = excluded.source_id,
                          source_kind = excluded.source_kind,
                          query_id = excluded.query_id,
                          source_url = excluded.source_url,
                          search_query = excluded.search_query,
                          post_author_name = excluded.post_author_name,
                          post_text = excluded.post_text,
                          extraction_status = excluded.extraction_status,
                          latest_extraction_run_id = excluded.latest_extraction_run_id,
                          updated_at = excluded.updated_at
                        """,
                        (
                            post_id,
                            comment.post_url,
                            comment.source_id,
                            comment.source_kind,
                            comment.query_id,
                            comment.source_url,
                            comment.search_query,
                            comment.post_author_name,
                            comment.post_text,
                            "extracted",
                            run_id,
                            now,
                            now,
                        ),
                    )
                    db.execute(
                        """
                        INSERT INTO people(
                          person_id, profile_url, name, headline, company, relationship, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(person_id) DO UPDATE SET
                          profile_url = excluded.profile_url,
                          name = excluded.name,
                          headline = excluded.headline,
                          company = excluded.company,
                          relationship = excluded.relationship,
                          updated_at = excluded.updated_at
                        """,
                        (
                            person_id,
                            comment.commenter_profile_url,
                            comment.commenter_name,
                            comment.commenter_headline,
                            comment.commenter_company,
                            comment.relationship,
                            now,
                        ),
                    )
                    db.execute(
                        """
                        INSERT INTO comments(
                          comment_key, run_id, post_id, person_id, query_id, source_id,
                          source_kind, source_url, search_query, post_url, post_author_name,
                          post_text, source_comment_id, comment_url, commenter_name,
                          commenter_profile_url, commenter_headline, commenter_company,
                          relationship, comment_text, commented_at, warnings_json,
                          created_at, updated_at
                        )
                        VALUES (
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        ON CONFLICT(comment_key) DO UPDATE SET
                          run_id = excluded.run_id,
                          post_id = excluded.post_id,
                          person_id = excluded.person_id,
                          query_id = excluded.query_id,
                          source_id = excluded.source_id,
                          source_kind = excluded.source_kind,
                          source_url = excluded.source_url,
                          search_query = excluded.search_query,
                          post_url = excluded.post_url,
                          post_author_name = excluded.post_author_name,
                          post_text = excluded.post_text,
                          source_comment_id = excluded.source_comment_id,
                          comment_url = excluded.comment_url,
                          commenter_name = excluded.commenter_name,
                          commenter_profile_url = excluded.commenter_profile_url,
                          commenter_headline = excluded.commenter_headline,
                          commenter_company = excluded.commenter_company,
                          relationship = excluded.relationship,
                          comment_text = excluded.comment_text,
                          commented_at = excluded.commented_at,
                          warnings_json = excluded.warnings_json,
                          updated_at = excluded.updated_at
                        """,
                        (
                            comment_key,
                            run_id,
                            post_id,
                            person_id,
                            comment.query_id,
                            comment.source_id,
                            comment.source_kind,
                            comment.source_url,
                            comment.search_query,
                            comment.post_url,
                            comment.post_author_name,
                            comment.post_text,
                            comment.comment_id,
                            comment.comment_url,
                            comment.commenter_name,
                            comment.commenter_profile_url,
                            comment.commenter_headline,
                            comment.commenter_company,
                            comment.relationship,
                            comment.comment_text,
                            comment.commented_at,
                            _json_dumps(comment.warnings),
                            now,
                            now,
                        ),
                    )
                    db.execute(
                        """
                        INSERT INTO rankings(
                          comment_key, rank_level, rank_points, problem_fit, buying_signal,
                          buyer_fit, actionability, immediacy, direct_buyer,
                          need_categories_json, positive_signals_json, fit_reasons_json,
                          reject_reasons_json, evidence_quote, ranked_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(comment_key) DO UPDATE SET
                          rank_level = excluded.rank_level,
                          rank_points = excluded.rank_points,
                          problem_fit = excluded.problem_fit,
                          buying_signal = excluded.buying_signal,
                          buyer_fit = excluded.buyer_fit,
                          actionability = excluded.actionability,
                          immediacy = excluded.immediacy,
                          direct_buyer = excluded.direct_buyer,
                          need_categories_json = excluded.need_categories_json,
                          positive_signals_json = excluded.positive_signals_json,
                          fit_reasons_json = excluded.fit_reasons_json,
                          reject_reasons_json = excluded.reject_reasons_json,
                          evidence_quote = excluded.evidence_quote,
                          ranked_at = excluded.ranked_at
                        """,
                        (
                            comment_key,
                            ranked.rank_level.value,
                            ranked.rank_points,
                            ranked.problem_fit,
                            ranked.buying_signal,
                            ranked.buyer_fit,
                            ranked.actionability,
                            ranked.immediacy,
                            int(ranked.direct_buyer),
                            _json_dumps(ranked.need_categories),
                            _json_dumps(ranked.positive_signals),
                            _json_dumps(ranked.fit_reasons),
                            _json_dumps(ranked.reject_reasons),
                            ranked.evidence_quote,
                            now,
                        ),
                    )
        return ranked_comments

    def export_comments(self) -> tuple[CommentEvidence, ...]:
        rows = self.fetch_all(
            """
            SELECT query_id, source_id, source_kind, source_url, search_query, post_url,
                   post_author_name, post_text, source_comment_id, comment_url,
                   commenter_name, commenter_profile_url, commenter_headline,
                   commenter_company, relationship, comment_text, commented_at,
                   warnings_json
            FROM comments
            ORDER BY created_at ASC, comment_key ASC
            """
        )
        comments: list[CommentEvidence] = []
        for row in rows:
            comments.append(
                CommentEvidence(
                    query_id=str(row["query_id"] or ""),
                    source_id=str(row["source_id"] or ""),
                    source_kind=str(row["source_kind"] or ""),
                    source_url=str(row["source_url"] or ""),
                    search_query=str(row["search_query"] or ""),
                    post_url=str(row["post_url"] or ""),
                    post_author_name=str(row["post_author_name"] or ""),
                    post_text=str(row["post_text"] or ""),
                    comment_id=str(row["source_comment_id"] or ""),
                    comment_url=str(row["comment_url"] or ""),
                    commenter_name=str(row["commenter_name"] or ""),
                    commenter_profile_url=str(row["commenter_profile_url"] or ""),
                    commenter_headline=str(row["commenter_headline"] or ""),
                    commenter_company=str(row["commenter_company"] or ""),
                    relationship=str(row["relationship"] or ""),
                    comment_text=str(row["comment_text"] or ""),
                    commented_at=str(row["commented_at"] or ""),
                    warnings=_warnings_from_json(str(row["warnings_json"] or "[]")),
                )
            )
        return tuple(comments)

    def set_review_label(
        self,
        *,
        comment_key: str,
        label: ReviewLabel,
        reject_reason: RejectReason | None = None,
        notes: str = "",
    ) -> None:
        now = _now_iso()
        with self._open_db() as db:
            with db:
                row = db.execute(
                    "SELECT comment_key FROM comments WHERE comment_key = ?",
                    (comment_key,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"unknown comment: {comment_key}")
                previous = db.execute(
                    "SELECT label FROM review_labels WHERE comment_key = ?",
                    (comment_key,),
                ).fetchone()
                old_label = str(previous["label"]) if previous is not None else ""
                db.execute(
                    """
                    INSERT INTO review_labels(
                      comment_key, label, reject_reason, notes, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(comment_key) DO UPDATE SET
                      label = excluded.label,
                      reject_reason = excluded.reject_reason,
                      notes = excluded.notes,
                      updated_at = excluded.updated_at
                    """,
                    (
                        comment_key,
                        label.value,
                        reject_reason.value if reject_reason is not None else "",
                        notes,
                        now,
                    ),
                )
                self._record_transition(
                    db,
                    entity_type="comment",
                    entity_id=comment_key,
                    from_status=old_label,
                    to_status=label.value,
                    reason="review label updated",
                    metadata={
                        "reject_reason": reject_reason.value if reject_reason is not None else "",
                        "notes": notes,
                    },
                    created_at=now,
                )

    def comment_exists(self, comment_key: str) -> bool:
        with self._open_db() as db:
            row = db.execute(
                "SELECT 1 FROM comments WHERE comment_key = ? LIMIT 1",
                (comment_key,),
            ).fetchone()
        return row is not None

    def fetch_all(self, sql: str, parameters: Sequence[object] = ()) -> list[sqlite3.Row]:
        with self._open_db() as db:
            return list(db.execute(sql, tuple(parameters)).fetchall())

    def _open_db(self) -> sqlite3.Connection:
        return connect_sqlite(self.database_path)

    def _record_transition(
        self,
        db: sqlite3.Connection,
        *,
        entity_type: str,
        entity_id: str,
        from_status: str,
        to_status: str,
        reason: str,
        metadata: object,
        created_at: str,
    ) -> None:
        db.execute(
            """
            INSERT INTO status_transitions(
              event_id, entity_type, entity_id, from_status, to_status, reason,
              created_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "event_" + uuid.uuid4().hex,
                entity_type,
                entity_id,
                from_status,
                to_status,
                reason,
                created_at,
                _json_dumps(metadata),
            ),
        )


def stable_comment_key(comment: CommentEvidence) -> str:
    if comment.comment_id:
        seed = f"{comment.post_url}|{comment.comment_id}"
    else:
        seed = (
            f"{comment.post_url}|{comment.commenter_profile_url}|"
            f"{comment.comment_text}|{comment.commented_at}"
        )
    return "comment_" + _hash(seed)


def stable_post_key(post_url: str) -> str:
    return "post_" + _hash(post_url)


def stable_person_key(profile_url: str) -> str:
    return "person_" + _hash(profile_url)


def post_candidate_key(candidate: PostCandidate) -> str:
    seed = "|".join(
        (
            candidate.source_id,
            candidate.source_kind,
            candidate.query_id,
            candidate.post_url,
            candidate.source_url,
            candidate.search_query,
            candidate.reason,
        )
    )
    return "post_" + _hash(seed)


def _source_params(source: SourceDefinition, now: str) -> tuple[object, ...]:
    return (
        source.source_id,
        source.source_kind.value,
        source.title,
        source.description,
        int(source.enabled),
        source.priority,
        _json_dumps(source.query_ids),
        _json_dumps(source.urls),
        _json_dumps(source.search_queries),
        now,
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _json_dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def _warnings_from_json(value: str) -> tuple[str, ...]:
    decoded = json.loads(value)
    if not isinstance(decoded, list) or not all(isinstance(warning, str) for warning in decoded):
        raise ValueError("warnings_json must be a JSON string list")
    return tuple(decoded)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY,
  source_kind TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  enabled INTEGER NOT NULL,
  priority INTEGER NOT NULL,
  query_ids_json TEXT NOT NULL,
  urls_json TEXT NOT NULL,
  search_queries_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
  post_id TEXT PRIMARY KEY,
  post_url TEXT NOT NULL DEFAULT '',
  source_id TEXT NOT NULL DEFAULT '',
  source_kind TEXT NOT NULL DEFAULT '',
  query_id TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  search_query TEXT NOT NULL DEFAULT '',
  priority INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  post_author_name TEXT NOT NULL DEFAULT '',
  post_text TEXT NOT NULL DEFAULT '',
  extraction_status TEXT NOT NULL DEFAULT 'queued',
  artifact_path TEXT NOT NULL DEFAULT '',
  latest_extraction_run_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_posts_source ON posts(source_id);
CREATE INDEX IF NOT EXISTS idx_posts_url ON posts(post_url);

CREATE TABLE IF NOT EXISTS people (
  person_id TEXT PRIMARY KEY,
  profile_url TEXT NOT NULL,
  name TEXT NOT NULL,
  headline TEXT NOT NULL,
  company TEXT NOT NULL,
  relationship TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_people_profile ON people(profile_url);

CREATE TABLE IF NOT EXISTS extraction_runs (
  run_id TEXT PRIMARY KEY,
  post_url TEXT NOT NULL DEFAULT '',
  source_id TEXT NOT NULL DEFAULT '',
  query_id TEXT NOT NULL DEFAULT '',
  source_kind TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  search_query TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL DEFAULT '',
  comments_found INTEGER NOT NULL DEFAULT 0,
  failures INTEGER NOT NULL DEFAULT 0,
  warning_count INTEGER NOT NULL DEFAULT 0,
  browser_profile TEXT NOT NULL DEFAULT '',
  safety_limits_json TEXT NOT NULL DEFAULT '{}',
  retry_recommendation TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_extraction_runs_started ON extraction_runs(started_at);

CREATE TABLE IF NOT EXISTS extraction_artifacts (
  artifact_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  app TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  status TEXT NOT NULL,
  retryable_error TEXT NOT NULL,
  created_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_extraction_artifacts_run ON extraction_artifacts(run_id);

CREATE TABLE IF NOT EXISTS extraction_errors (
  error_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  post_url TEXT NOT NULL DEFAULT '',
  error_type TEXT NOT NULL,
  message TEXT NOT NULL,
  retryable INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_extraction_errors_run ON extraction_errors(run_id);

CREATE TABLE IF NOT EXISTS comments (
  comment_key TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  post_id TEXT NOT NULL,
  person_id TEXT NOT NULL,
  query_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  source_url TEXT NOT NULL,
  search_query TEXT NOT NULL,
  post_url TEXT NOT NULL,
  post_author_name TEXT NOT NULL,
  post_text TEXT NOT NULL,
  source_comment_id TEXT NOT NULL,
  comment_url TEXT NOT NULL,
  commenter_name TEXT NOT NULL,
  commenter_profile_url TEXT NOT NULL,
  commenter_headline TEXT NOT NULL,
  commenter_company TEXT NOT NULL,
  relationship TEXT NOT NULL,
  comment_text TEXT NOT NULL,
  commented_at TEXT NOT NULL,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comments_source ON comments(source_id);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
CREATE INDEX IF NOT EXISTS idx_comments_person ON comments(person_id);

CREATE TABLE IF NOT EXISTS rankings (
  comment_key TEXT PRIMARY KEY,
  rank_level TEXT NOT NULL,
  rank_points INTEGER NOT NULL,
  problem_fit INTEGER NOT NULL,
  buying_signal INTEGER NOT NULL,
  buyer_fit INTEGER NOT NULL,
  actionability INTEGER NOT NULL,
  immediacy INTEGER NOT NULL,
  direct_buyer INTEGER NOT NULL,
  need_categories_json TEXT NOT NULL,
  positive_signals_json TEXT NOT NULL,
  fit_reasons_json TEXT NOT NULL,
  reject_reasons_json TEXT NOT NULL,
  evidence_quote TEXT NOT NULL,
  ranked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rankings_level ON rankings(rank_level);
CREATE INDEX IF NOT EXISTS idx_rankings_points ON rankings(rank_points);

CREATE TABLE IF NOT EXISTS review_labels (
  comment_key TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  reject_reason TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS status_transitions (
  event_id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  from_status TEXT NOT NULL,
  to_status TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_status_transitions_entity
ON status_transitions(entity_type, entity_id);
"""
