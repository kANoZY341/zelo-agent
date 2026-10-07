"""Add immutable links from accepted learned revisions to successful merge lineage."""

import sqlalchemy as sa
from alembic import op

revision = "0035_skill_learning_promotion_links"
down_revision = "0034_skill_learning_task"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_learning_promotion_links",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.String(length=36),
            sa.ForeignKey("skill_learning_proposals.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("reviewer_id", sa.String(length=32), nullable=False),
        sa.Column("review_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("revision_sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_path", sa.String(length=4096), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_bytes", sa.Integer(), nullable=False),
        sa.Column("repository", sa.String(length=256), nullable=False),
        sa.Column(
            "promotion_task_id",
            sa.String(length=36),
            sa.ForeignKey("tasks.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("promotion_action_version", sa.Integer(), nullable=False),
        sa.Column("promotion_action_hash", sa.String(length=64), nullable=False),
        sa.Column("promotion_review_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("pr_head_sha", sa.String(length=40), nullable=False),
        sa.Column(
            "merge_task_id",
            sa.String(length=36),
            sa.ForeignKey("tasks.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("merge_action_version", sa.Integer(), nullable=False),
        sa.Column("merge_action_hash", sa.String(length=64), nullable=False),
        sa.Column("merge_commit_sha", sa.String(length=40), nullable=False),
        sa.Column("link_fingerprint", sa.String(length=64), nullable=False, unique=True),
        sa.Column("link_json", sa.Text(), nullable=False),
        sa.Column("runtime_authority", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("reviewer_id = 'ahmad'", name="ck_skill_learning_promotion_reviewer"),
        sa.CheckConstraint("runtime_authority = 0", name="ck_skill_learning_promotion_runtime"),
        sa.CheckConstraint(
            "length(review_fingerprint) = 64", name="ck_skill_learning_promotion_review"
        ),
        sa.CheckConstraint(
            "length(revision_sha256) = 64", name="ck_skill_learning_promotion_revision"
        ),
        sa.CheckConstraint(
            "length(artifact_sha256) = 64", name="ck_skill_learning_promotion_artifact"
        ),
        sa.CheckConstraint(
            "length(promotion_action_hash) = 64", name="ck_skill_learning_promotion_action"
        ),
        sa.CheckConstraint("length(merge_action_hash) = 64", name="ck_skill_learning_merge_action"),
        sa.CheckConstraint(
            "length(link_fingerprint) = 64", name="ck_skill_learning_link_fingerprint"
        ),
        sa.CheckConstraint(
            "length(promotion_review_fingerprint) = 64",
            name="ck_skill_learning_promotion_review_fingerprint",
        ),
        sa.CheckConstraint("length(pr_head_sha) = 40", name="ck_skill_learning_promotion_pr_head"),
        sa.CheckConstraint(
            "length(merge_commit_sha) = 40", name="ck_skill_learning_promotion_merge_commit"
        ),
        sa.CheckConstraint(
            "artifact_bytes BETWEEN 1 AND 65536",
            name="ck_skill_learning_promotion_artifact_bytes",
        ),
        sa.CheckConstraint("pr_number >= 1", name="ck_skill_learning_promotion_pr_number"),
        sa.CheckConstraint(
            "promotion_action_version >= 1",
            name="ck_skill_learning_promotion_action_version",
        ),
        sa.CheckConstraint(
            "merge_action_version >= 1",
            name="ck_skill_learning_merge_action_version",
        ),
        sa.CheckConstraint(
            "length(link_json) <= 32768", name="ck_skill_learning_promotion_link_size"
        ),
    )
    op.create_index(
        "ix_skill_learning_promotion_links_proposal_id",
        "skill_learning_promotion_links",
        ["proposal_id"],
    )
    op.create_index(
        "ix_skill_learning_promotion_links_promotion_task_id",
        "skill_learning_promotion_links",
        ["promotion_task_id"],
    )
    op.create_index(
        "ix_skill_learning_promotion_links_merge_task_id",
        "skill_learning_promotion_links",
        ["merge_task_id"],
    )
    op.execute(
        """CREATE TRIGGER skill_learning_promotion_link_insert_guard
        BEFORE INSERT ON skill_learning_promotion_links
        WHEN NOT EXISTS (
                SELECT 1 FROM skill_learning_reviews
                WHERE proposal_id = NEW.proposal_id
                  AND reviewer_id = 'ahmad' AND decision = 'accepted'
            )
          OR NOT EXISTS (
                SELECT 1 FROM skill_learning_events
                WHERE proposal_id = NEW.proposal_id AND sequence = 3
                  AND event = 'accepted' AND reviewer_id = 'ahmad'
            )
          OR NOT EXISTS (
                SELECT 1 FROM tasks
                WHERE id = NEW.promotion_task_id
                  AND kind = 'promotion_run' AND status = 'succeeded'
            )
          OR NOT EXISTS (
                SELECT 1 FROM tasks
                WHERE id = NEW.merge_task_id
                  AND kind = 'merge_run' AND status = 'succeeded'
            )
          OR NEW.review_fingerprint IS NOT (
                SELECT review_fingerprint FROM skill_learning_reviews
                WHERE proposal_id = NEW.proposal_id
                  AND reviewer_id = 'ahmad' AND decision = 'accepted'
            )
          OR NEW.revision_sha256 IS NOT (
                SELECT json_extract(proposal_json, '$.proposed_revision.content_sha256')
                FROM skill_learning_proposals WHERE id = NEW.proposal_id
            )
          OR json_extract(NEW.link_json, '$.proposal_id') IS NOT NEW.proposal_id
          OR json_extract(NEW.link_json, '$.proposal_fingerprint') IS NOT (
                SELECT proposal_fingerprint FROM skill_learning_proposals
                WHERE id = NEW.proposal_id
            )
          OR json_extract(NEW.link_json, '$.reviewer_id') IS NOT NEW.reviewer_id
          OR json_extract(NEW.link_json, '$.review_fingerprint') IS NOT NEW.review_fingerprint
          OR json_extract(NEW.link_json, '$.revision_sha256') IS NOT NEW.revision_sha256
          OR json_extract(NEW.link_json, '$.artifact_path') IS NOT NEW.artifact_path
          OR json_extract(NEW.link_json, '$.artifact_sha256') IS NOT NEW.artifact_sha256
          OR json_extract(NEW.link_json, '$.artifact_bytes') IS NOT NEW.artifact_bytes
          OR json_extract(NEW.link_json, '$.repository') IS NOT NEW.repository
          OR json_extract(NEW.link_json, '$.promotion_task_id') IS NOT NEW.promotion_task_id
          OR json_extract(NEW.link_json, '$.promotion_action_version')
                IS NOT NEW.promotion_action_version
          OR json_extract(NEW.link_json, '$.promotion_action_hash') IS NOT NEW.promotion_action_hash
          OR json_extract(NEW.link_json, '$.promotion_review_fingerprint')
                IS NOT NEW.promotion_review_fingerprint
          OR json_extract(NEW.link_json, '$.pr_number') IS NOT NEW.pr_number
          OR json_extract(NEW.link_json, '$.pr_head_sha') IS NOT NEW.pr_head_sha
          OR json_extract(NEW.link_json, '$.merge_task_id') IS NOT NEW.merge_task_id
          OR json_extract(NEW.link_json, '$.merge_action_version') IS NOT NEW.merge_action_version
          OR json_extract(NEW.link_json, '$.merge_action_hash') IS NOT NEW.merge_action_hash
          OR json_extract(NEW.link_json, '$.merge_commit_sha') IS NOT NEW.merge_commit_sha
          OR json_extract(NEW.link_json, '$.link_fingerprint') IS NOT NEW.link_fingerprint
          OR NEW.promotion_action_version IS NOT (
                SELECT action_version FROM tasks WHERE id = NEW.promotion_task_id
            )
          OR NEW.promotion_action_hash IS NOT (
                SELECT action_hash FROM tasks WHERE id = NEW.promotion_task_id
            )
          OR NEW.merge_action_version IS NOT (
                SELECT action_version FROM tasks WHERE id = NEW.merge_task_id
            )
          OR NEW.merge_action_hash IS NOT (
                SELECT action_hash FROM tasks WHERE id = NEW.merge_task_id
            )
          OR NEW.repository IS NOT json_extract(
                (SELECT promotion_spec FROM tasks WHERE id = NEW.promotion_task_id),
                '$.repository'
            )
          OR NEW.repository IS NOT json_extract(
                (SELECT result FROM tasks WHERE id = NEW.merge_task_id),
                '$.repository'
            )
          OR NEW.pr_number IS NOT json_extract(
                (SELECT result FROM tasks WHERE id = NEW.promotion_task_id),
                '$.publication.pr_number'
            )
          OR NEW.pr_head_sha IS NOT json_extract(
                (SELECT result FROM tasks WHERE id = NEW.promotion_task_id),
                '$.publication.pr_head_sha'
            )
          OR NEW.pr_number IS NOT json_extract(
                (SELECT result FROM tasks WHERE id = NEW.merge_task_id),
                '$.pr_number'
            )
          OR NEW.pr_head_sha IS NOT json_extract(
                (SELECT result FROM tasks WHERE id = NEW.merge_task_id),
                '$.head_sha'
            )
          OR json_array_length(
                json_extract(
                    (SELECT promotion_spec FROM tasks WHERE id = NEW.promotion_task_id),
                    '$.review.candidate_manifest'
                )
            ) IS NOT 1
          OR NEW.artifact_path IS NOT json_extract(
                (SELECT promotion_spec FROM tasks WHERE id = NEW.promotion_task_id),
                '$.review.candidate_manifest[0].path'
            )
          OR NEW.artifact_sha256 IS NOT json_extract(
                (SELECT promotion_spec FROM tasks WHERE id = NEW.promotion_task_id),
                '$.review.candidate_manifest[0].sha256'
            )
          OR NEW.artifact_bytes IS NOT json_extract(
                (SELECT promotion_spec FROM tasks WHERE id = NEW.promotion_task_id),
                '$.review.candidate_manifest[0].size'
            )
          OR json_extract(
                (SELECT promotion_spec FROM tasks WHERE id = NEW.promotion_task_id),
                '$.review.candidate_manifest[0].operation'
            ) IS NOT 'add'
          OR json_extract(
                (SELECT promotion_spec FROM tasks WHERE id = NEW.promotion_task_id),
                '$.review.candidate_manifest[0].object_type'
            ) IS NOT 'file'
          OR json_extract(
                (SELECT promotion_spec FROM tasks WHERE id = NEW.promotion_task_id),
                '$.review.candidate_manifest[0].mode'
            ) IS NOT '100644'
          OR json_extract(
                (SELECT merge_spec FROM tasks WHERE id = NEW.merge_task_id),
                '$.promotion_task_id'
            ) IS NOT NEW.promotion_task_id
          OR json_extract(
                (SELECT result FROM tasks WHERE id = NEW.merge_task_id),
                '$.merge_commit_sha'
            ) IS NOT NEW.merge_commit_sha
          OR json_extract(
                (SELECT promotion_spec FROM tasks WHERE id = NEW.promotion_task_id),
                '$.review.review_fingerprint'
            ) IS NOT NEW.promotion_review_fingerprint
          OR COALESCE(json_extract(NEW.link_json, '$.runtime_authority'), 1) IS NOT 0
        BEGIN
            SELECT RAISE(ABORT, 'Skill-learning promotion link is not authorized');
        END"""
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER skill_learning_promotion_links_no_{operation.lower()} "
            f"BEFORE {operation} ON skill_learning_promotion_links "
            "BEGIN SELECT RAISE(ABORT, 'Skill-learning promotion links are append-only'); END"
        )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
