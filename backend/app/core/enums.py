from enum import StrEnum


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    REVIEW = "review"
    DONE = "done"
    CANCELED = "canceled"


class SessionRole(StrEnum):
    MANAGER = "manager"
    WORKER = "worker"
    REVIEWER = "reviewer"


class SessionStatus(StrEnum):
    PENDING = "pending"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    EXITED = "exited"
    FAILED = "failed"
    STOPPED = "stopped"


class SessionTransport(StrEnum):
    LOCAL_PROCESS = "local_process"


class WorkspaceStatus(StrEnum):
    PLANNED = "planned"
    PROVISIONING = "provisioning"
    READY = "ready"
    DIRTY = "dirty"
    ARCHIVED = "archived"
    FAILED = "failed"


class EventLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class EventCategory(StrEnum):
    PROJECT = "project"
    TASK = "task"
    SESSION = "session"
    EVENT = "event"
    REVIEW = "review"
    ARTIFACT = "artifact"
    WORKSPACE = "workspace"
    SYSTEM = "system"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    CHANGES_REQUESTED = "changes_requested"
    REVIEWER_APPROVED = "reviewer_approved"
    HUMAN_APPROVED = "human_approved"
    REJECTED = "rejected"
    MERGE_READY = "merge_ready"


class ArtifactKind(StrEnum):
    DIFF = "diff"
    PATCH = "patch"
    TEST_REPORT = "test_report"
    LINT_REPORT = "lint_report"
    SESSION_LOG = "session_log"
    TRANSCRIPT = "transcript"
    BUNDLE = "bundle"
    NOTE = "note"
