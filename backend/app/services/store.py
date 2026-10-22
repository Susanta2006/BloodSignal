"""Repository. MemoryStore is the reference implementation used by tests/demo.

The process-local MemoryStore keeps the same workflow methods (
RLS on every table, append-only audit_events, immutable source_records).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.schemas import (Approval, AuditEvent, Candidate, EvidenceRecord, Outcome, Run, Task,
                         StoredRequest, TASK_CODES, TaskStatus, utcnow)


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


class MemoryStore:
    def __init__(self) -> None:
        self.requests: dict[str, StoredRequest] = {}
        self.runs: dict[str, Run] = {}
        self.tasks: dict[str, list[Task]] = {}
        self.evidence: dict[str, EvidenceRecord] = {}
        self.run_evidence: dict[str, list[str]] = {}
        self.candidates: dict[str, list[Candidate]] = {}
        self.approvals: dict[str, Approval] = {}
        self.run_approvals: dict[str, list[str]] = {}
        self.outcomes: dict[str, list[Outcome]] = {}
        self.audit_events: list[AuditEvent] = []
        self.idem_runs: dict[str, tuple[str, str]] = {}          # HMAC(key) -> (payload hash, run id)
        self.idem_outcomes: dict[tuple[str, str], tuple[str, str]] = {}  # (run,key) -> (hash, outcome_id)

    # runs / tasks
    def add_run(self, run: Run, req: StoredRequest) -> None:
        self.runs[run.run_id] = run
        self.requests[req.request_id] = req
        self.tasks[run.run_id] = [Task(run_id=run.run_id, task_code=c) for c in TASK_CODES]

    def request_for(self, run: Run) -> StoredRequest:
        return self.requests[run.request_id]

    def task(self, run_id: str, code: str) -> Task:
        return next(t for t in self.tasks[run_id] if t.task_code == code)

    def pending_tasks(self, run_id: str, codes: list[str]) -> list[Task]:
        return [t for t in self.tasks[run_id] if t.task_code in codes and t.status == TaskStatus.PENDING]

    # evidence
    def add_evidence(self, ev: EvidenceRecord) -> None:
        self.evidence[ev.evidence_id] = ev
        self.run_evidence.setdefault(ev.run_id, []).append(ev.evidence_id)

    # approvals
    def add_approval(self, a: Approval) -> None:
        self.approvals[a.approval_id] = a
        self.run_approvals.setdefault(a.run_id, []).append(a.approval_id)

    def approvals_for(self, run_id: str) -> list[Approval]:
        return [self.approvals[i] for i in self.run_approvals.get(run_id, [])]

    def latest_approval(self, run_id: str) -> Approval | None:
        items = self.approvals_for(run_id)
        return max(items, key=lambda a: a.proposal_version) if items else None

    # audit (append-only: no update/delete methods exist)
    def audit(self, run_id: str | None, actor_type: str, actor_id: str, event_type: str,
              entity_id: str | None = None, before: Any = None, after: Any = None) -> None:
        self.audit_events.append(AuditEvent(
            run_id=run_id, actor_type=actor_type, actor_id=actor_id, event_type=event_type,
            entity_id=entity_id, before_hash=canonical_hash(before) if before is not None else None,
            after_hash=canonical_hash(after) if after is not None else None, created_at=utcnow()))
