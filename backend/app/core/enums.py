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
    STARTING = "starting"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
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
    NEEDS_CHANGES = "needs_changes"
    APPROVED = "approved"
    REJECTED = "rejected"


class ArtifactKind(StrEnum):
    DIFF = "diff"
    PATCH = "patch"
    TEST_REPORT = "test_report"
    LINT_REPORT = "lint_report"
    SESSION_LOG = "session_log"
    TRANSCRIPT = "transcript"
    BUNDLE = "bundle"
    NOTE = "note"


class TranscriptStream(StrEnum):
    STDIN = "stdin"
    STDOUT = "stdout"
    STDERR = "stderr"
    SYSTEM = "system"


class StructuredEventType(StrEnum):
    START = "start"
    PROGRESS = "progress"
    QUESTION = "question"
    BLOCKED = "blocked"
    TESTS_RUN = "tests_run"
    COMPLETE = "complete"
    ERROR = "error"
    HEARTBEAT = "heartbeat"


class StructuredEventStatus(StrEnum):
    VALID = "valid"
    MALFORMED = "malformed"


class TestCommandStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    WARN = "warn"


class ProjectCheckpointKind(StrEnum):
    QUESTION = "question"
    BLOCKED = "blocked"
    COMPLETION = "completion"
    ERROR = "error"


class ProjectCheckpointStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ProjectCheckpointResolution(StrEnum):
    ANSWERED = "answered"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    DISMISSED = "dismissed"


class ProjectCheckpointAction(StrEnum):
    ANSWER = "answer"
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    DISMISS = "dismiss"
