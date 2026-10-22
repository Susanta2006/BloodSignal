"""HTTP endpoints. All matching/ranking logic lives in services/ and the orchestrator."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import base64

from fastapi import APIRouter, BackgroundTasks, Header, Request, Response
from pydantic import ValidationError

from app.safety import DEMO_LABEL, DISCLAIMER, check_note
from app.schemas import (AppError, Approval, ApprovalIn, Candidate, IntakeDraft, IntakeIn, Outcome,
                         OutcomeIn, ProposalCreate, ProposalItem, Run, RunCreate, RunStatus, Section,
                         StoredRequest, TaskStatus, new_id, utcnow)
from app.services.store import canonical_hash

router = APIRouter()

# Approximate district-center coordinates used only for deterministic demo distance ranking.
# They are not device GPS and must not be presented as the user's exact location.
_DEMO_ORIGINS: dict[str, tuple[float, float]] = {
    "imphal west": (24.8170, 93.9368),
    "imphal east": (24.7900, 94.0000),
    "thoubal": (24.6388, 94.0106),
}


def _secret(request: Request) -> bytes:
    configured = _c(request).settings.token_hash_secret.get_secret_value()
    # Development fallback only. Production deployments must configure TOKEN_HASH_SECRET.
    return (configured or "bloodsignal-local-development-only").encode("utf-8")


def _hash(value: str, secret: bytes) -> str:
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _run_token(run_id: str, idempotency_key: str, secret: bytes) -> str:
    raw = hmac.new(secret, f"run:{run_id}:{idempotency_key}".encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _key_hash(request: Request, value: str) -> str:
    return _hash(value, _secret(request))


def _c(request: Request):
    return request.app.state.container


def _banner(run: Run) -> dict:
    return {"disclaimer": DISCLAIMER, "demo_label": DEMO_LABEL if run.demo_mode else None}


def _authorize(request: Request, run_id: str, token: str | None) -> Run:
    if not token:
        raise AppError("RUN_TOKEN_REQUIRED", "Missing X-Run-Token header.", status_code=401)
    run = _c(request).store.runs.get(run_id)
    if run is None or not hmac.compare_digest(run.token_hash, _hash(token, _secret(request))):
        raise AppError("RUN_NOT_FOUND", "Run not found.", status_code=404)
    return run


def _idem(key: str | None) -> str:
    if not key or not (8 <= len(key) <= 128):
        raise AppError("IDEMPOTENCY_KEY_REQUIRED", "Send an Idempotency-Key header (8-128 chars).",
                       status_code=400)
    return key


@router.get("/api/idempotency-key")
async def issue_idempotency_key():
    """Issue a cryptographically random idempotency key from the trusted API runtime.

    This is an identifier, not an authorization token. Run access still requires the
    separate X-Run-Token returned by create_run.
    """
    return {"idempotency_key": secrets.token_urlsafe(24)}


# ------------------------------------------------------------ runs
@router.post("/api/runs", status_code=202)
async def create_run(body: RunCreate, request: Request, background: BackgroundTasks,
                     response: Response,
                     idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    c, key = _c(request), _idem(idempotency_key)
    key_hash = _key_hash(request, key)
    secret = _secret(request)
    payload_hash = canonical_hash(body.model_dump(mode="json"))

    prior = c.store.idem_runs.get(key_hash)
    if prior:
        if prior[0] != payload_hash:
            raise AppError("IDEMPOTENCY_KEY_REUSED", "This key was used with a different request.",
                           status_code=409)
        response.headers["Idempotent-Replay"] = "true"
        run = c.store.runs[prior[1]]
        return _created(run, _run_token(run.run_id, key, secret))

    now = utcnow()
    loc = body.location
    req = StoredRequest(  # coordinates intentionally dropped here
        request_id=new_id(), blood_group=body.blood_group, component=body.component,
        quantity=body.quantity, quantity_unit=body.quantity_unit, location_mode=loc.mode,
        district=loc.district, state=loc.state, country=loc.country,
        location_consent=loc.location_consent,
        include_nearby_districts=body.search_options.include_nearby_districts,
        max_radius_km=body.search_options.max_radius_km, idempotency_key=key_hash, created_at=now)
    run_id = new_id()
    token = _run_token(run_id, key, secret)
    run = Run(run_id=run_id, request_id=req.request_id, started_at=now,
              token_hash=_hash(token, secret),
              demo_mode=c.settings.demo_mode,
              status_history=[{"status": "ACCEPTED", "at": now.isoformat(), "reason_code": None}])
    c.store.add_run(run, req)
    c.store.idem_runs[key_hash] = (payload_hash, run.run_id)
    c.store.audit(run.run_id, "user", "anonymous", "run.created", run.run_id, None,
                  {"request_id": req.request_id})
    if loc.mode == "device" and loc.location_consent and loc.latitude is not None:
        c.orchestrator.register_origin(run.run_id, (loc.latitude, loc.longitude))
    elif run.demo_mode and loc.mode == "manual" and loc.district:
        # District-center origin makes demo ranking reproducible without collecting GPS.
        demo_origin = _DEMO_ORIGINS.get(loc.district.casefold().strip())
        if demo_origin:
            c.orchestrator.register_ranking_origin(run.run_id, demo_origin)
    background.add_task(c.orchestrator.execute, run.run_id)
    c.metrics["runs_created"] += 1
    return _created(run, token)


def _created(run: Run, token: str) -> dict:
    return {"run_id": run.run_id, "request_id": run.request_id, "status": run.status,
            "run_token": token, "poll_url": f"/api/runs/{run.run_id}", **_banner(run)}


@router.get("/api/runs/{run_id}")
async def get_run(run_id: str, request: Request, x_run_token: str | None = Header(None, alias="X-Run-Token")):
    run = _authorize(request, run_id, x_run_token)
    tasks = _c(request).store.tasks[run_id]
    return {"run_id": run.run_id, "status": run.status, "result_state": run.result_state,
            "plan_version": run.plan_version, "plan": run.plan, "started_at": run.started_at,
            "completed_at": run.completed_at, "error_code": run.error_code,
            "status_history": run.status_history, "summary": run.summary,
            "tasks": [t.model_dump(mode="json") for t in tasks], **_banner(run)}


@router.get("/api/runs/{run_id}/candidates")
async def get_candidates(run_id: str, request: Request,
                         x_run_token: str | None = Header(None, alias="X-Run-Token")):
    run = _authorize(request, run_id, x_run_token)
    return {"run_id": run_id, "run_status": run.status, "result_state": run.result_state,
            "candidates": _c(request).store.candidates.get(run_id, []), **_banner(run)}


@router.get("/api/evidence/{evidence_id}")
async def get_evidence(evidence_id: str, request: Request,
                       x_run_token: str | None = Header(None, alias="X-Run-Token")):
    ev = _c(request).store.evidence.get(evidence_id)
    if ev is None:
        raise AppError("EVIDENCE_NOT_FOUND", "Evidence not found.", status_code=404)
    run = _authorize(request, ev.run_id, x_run_token)
    return {**ev.model_dump(mode="json"),
            "timestamp_semantics": "source_updated_at is when the SOURCE says the record changed; "
                                   "retrieved_at is when we fetched it. They are different.",
            **_banner(run)}


# ------------------------------------------------------------ proposal / approval
def _items_for(cands: list[Candidate], ids: list[str] | None) -> list[ProposalItem]:
    pool = [c for c in cands if c.section in (Section.A_ELIGIBLE, Section.B_UNCERTAIN)]
    if ids is not None:
        known = {c.candidate_id: c for c in cands}
        bad = [i for i in ids if i not in known]
        if bad:
            raise AppError("UNKNOWN_CANDIDATE", "One or more candidate_ids are not part of this run.",
                           status_code=422)
        pool = [known[i] for i in dict.fromkeys(ids)]
    return [ProposalItem(
        candidate_id=c.candidate_id, bank_id=c.bank.bank_id, bank_name=c.bank.name,
        phone=c.bank.phone, section=c.section, evidence_state=c.evidence_state,
        source_updated_at=c.source_updated_at, source_updated_at_raw=c.source_updated_at_raw,
        display_note=c.display_note,
        address=c.bank.address, facility_source_type=c.bank.source_type,
        directory_source=c.bank.directory_source, retrieved_at=c.retrieved_at,
        source_name=c.source_display_name, source_type=c.source_type,
        source_url=c.official_source_url, blood_group=c.blood_group_reported,
        component=c.component_reported, quantity=c.quantity_reported,
        quantity_unit=c.quantity_unit_reported, distance_km=c.distance_km,
        distance_type=c.distance_type,
    ) for c in pool]


def _proposal_out(a: Approval, run: Run) -> dict:
    return {**a.model_dump(mode="json"),
            "notice": "Approving this list does not place calls or reserve blood. "
                      "You make the calls yourself.", **_banner(run)}


@router.post("/api/runs/{run_id}/proposal", status_code=201)
async def create_proposal(run_id: str, body: ProposalCreate, request: Request, response: Response,
                          x_run_token: str | None = Header(None, alias="X-Run-Token")):
    run, c = _authorize(request, run_id, x_run_token), _c(request)
    if run.status not in (RunStatus.AWAITING_APPROVAL, RunStatus.COMPLETED):
        raise AppError("RUN_NOT_READY", "Results are not ready for a call list.", status_code=409)
    items = _items_for(c.store.candidates.get(run_id, []), body.candidate_ids)
    if not items:
        raise AppError("NO_CANDIDATES", "No candidates to put on a call list.", status_code=409,
                       user_action="Try nearby districts or call blood banks directly.")
    items_hash = canonical_hash([i.model_dump(mode="json") for i in items])
    latest = c.store.latest_approval(run_id)
    if latest and latest.items_hash == items_hash and latest.status != "SUPERSEDED":
        response.status_code = 200
        return _proposal_out(latest, run)

    version = (latest.proposal_version + 1) if latest else 1
    for old in c.store.approvals_for(run_id):  # any change invalidates earlier approval
        if old.status != "SUPERSEDED":
            c.store.audit(run_id, "system", "api", "approval.superseded", old.approval_id,
                          {"status": old.status}, {"status": "SUPERSEDED"})
            old.status = "SUPERSEDED"
    a = Approval(run_id=run_id, proposal_version=version, items_hash=items_hash, items=items,
                 proposal_hash=canonical_hash({"run": run_id, "v": version, "items": items_hash}),
                 requested_at=utcnow())
    c.store.add_approval(a)
    c.store.audit(run_id, "user", "run-token-holder", "proposal.created", a.approval_id, None,
                  {"version": version, "hash": a.proposal_hash})
    t11 = c.store.task(run_id, "T11_PREPARE_ACTION")
    t11.status, t11.completed_at, t11.output_summary = (
        TaskStatus.SUCCEEDED, utcnow(), f"Proposal v{version} prepared; no external action taken")
    return _proposal_out(a, run)


@router.post("/api/approvals/{approval_id}")
async def approve(approval_id: str, body: ApprovalIn, request: Request,
                  x_run_token: str | None = Header(None, alias="X-Run-Token")):
    c = _c(request)
    a = c.store.approvals.get(approval_id)
    if a is None:
        raise AppError("APPROVAL_NOT_FOUND", "Approval not found.", status_code=404)
    run = _authorize(request, a.run_id, x_run_token)
    if a.status == "SUPERSEDED" or c.store.latest_approval(a.run_id).approval_id != a.approval_id:
        raise AppError("PROPOSAL_SUPERSEDED", "The call list changed; review and approve the new version.",
                       status_code=409)
    if body.proposal_version != a.proposal_version or not hmac.compare_digest(
            body.proposal_hash, a.proposal_hash):
        raise AppError("PROPOSAL_HASH_MISMATCH", "Approval does not match the displayed call list.",
                       status_code=409)
    if a.status == "PENDING":
        a.status, a.approved_at, a.approved_by = "APPROVED", utcnow(), "run-token-holder"
        c.store.audit(a.run_id, "user", "run-token-holder", "approval.approved", a.approval_id,
                      {"status": "PENDING"}, {"status": "APPROVED", "hash": a.proposal_hash})
        t12 = c.store.task(a.run_id, "T12_AWAIT_APPROVAL")
        t12.status, t12.completed_at, t12.output_summary = (
            TaskStatus.SUCCEEDED, utcnow(), f"Proposal v{a.proposal_version} approved by user")
    return _proposal_out(a, run)  # idempotent: repeat returns same approved record


# ------------------------------------------------------------ outcomes
@router.post("/api/runs/{run_id}/outcomes", status_code=201)
async def record_outcome(run_id: str, body: OutcomeIn, request: Request, response: Response,
                         x_run_token: str | None = Header(None, alias="X-Run-Token"),
                         idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    run, c, key = _authorize(request, run_id, x_run_token), _c(request), _idem(idempotency_key)
    note = check_note(body.note)
    payload_hash = canonical_hash({**body.model_dump(mode="json"), "note": note})
    key_hash = _key_hash(request, key)
    prior = c.store.idem_outcomes.get((run_id, key_hash))
    if prior:
        if prior[0] != payload_hash:
            raise AppError("IDEMPOTENCY_KEY_REUSED", "This key was used with a different outcome.",
                           status_code=409)
        response.status_code = 200
        return next(o for o in c.store.outcomes[run_id] if o.verification_id == prior[1])

    approval = c.store.latest_approval(run_id)
    if not approval or approval.status != "APPROVED":
        raise AppError("APPROVAL_REQUIRED", "Approve the call list before recording outcomes.",
                       status_code=409)
    cand = next((i for i in approval.items if i.candidate_id == body.candidate_id), None)
    if cand is None:
        raise AppError("CANDIDATE_NOT_ON_APPROVED_LIST", "That bank is not on the approved call list.",
                       status_code=422)
    now = utcnow()
    if body.occurred_at and body.occurred_at > now:
        raise AppError("OUTCOME_IN_FUTURE", "occurred_at cannot be in the future.", status_code=422)
    full = next(x for x in c.store.candidates[run_id] if x.candidate_id == body.candidate_id)
    o = Outcome(run_id=run_id, candidate_id=body.candidate_id, approval_id=approval.approval_id,
                observed_outcome=body.outcome, occurred_at=body.occurred_at or now, note=note,
                recorded_at=now, source_evidence_ids=full.evidence_ids)
    c.store.outcomes.setdefault(run_id, []).append(o)
    c.store.idem_outcomes[(run_id, key_hash)] = (payload_hash, o.verification_id)
    c.store.audit(run_id, "user", "run-token-holder", "outcome.recorded", o.verification_id, None,
                  {"outcome": o.observed_outcome.value, "candidate": o.candidate_id})
    t13 = c.store.task(run_id, "T13_RECORD_OUTCOME")
    t13.status, t13.completed_at, t13.output_summary = (
        TaskStatus.SUCCEEDED, now, "User-reported outcome recorded; not verified by the source")
    if run.status == RunStatus.AWAITING_APPROVAL:
        c.orchestrator._set_status(run, RunStatus.COMPLETED, "OUTCOME_RECORDED")
    return {**o.model_dump(mode="json"),
            "label": f"Reported by user at {o.occurred_at:%H:%M} UTC; not independently verified "
                     "by the portal."}


@router.get("/api/runs/{run_id}/audit")
async def get_audit(run_id: str, request: Request,
                    x_run_token: str | None = Header(None, alias="X-Run-Token")):
    _authorize(request, run_id, x_run_token)
    return {"events": [e for e in _c(request).store.audit_events if e.run_id == run_id]}


# ------------------------------------------------------------ optional LLM intake
@router.post("/api/intake/draft")
async def intake_draft(body: IntakeIn, request: Request):
    check_note(body.text)  # reject phone/ID-like digit runs before anything leaves the server
    raw = await _c(request).provider.extract_request(body.text)
    try:
        draft = IntakeDraft.model_validate(raw)  # strict schema; unknown keys rejected
    except ValidationError as e:
        raise AppError("LLM_OUTPUT_INVALID", "Could not read a valid draft; use the form.",
                       status_code=502, user_action="Fill in the form manually.") from e
    return {"draft": draft, "needs_confirmation": True,
            "notice": "Draft only. Confirm every field before searching."}


# ------------------------------------------------------------ health / metrics
@router.get("/health/live")
async def live():
    return {"status": "ok"}


@router.get("/api/source")
async def source_info(request: Request):
    """Which data source is active and whether it is usable. Safe for the UI to show before a search."""
    c = _c(request)
    stock, info = c.orchestrator.stock, await c.orchestrator.stock.status()
    state = {"DEMO": "DEMO_MODE", "NOT_CONFIGURED": "LIVE_NOT_CONFIGURED",
             "READY": "LIVE_CONFIGURED"}.get(info.state, "SOURCE_UNAVAILABLE")
    return {"source_state": state, "mode": c.settings.stock_source_mode, "reason": info.reason,
            "source_label": stock.source_label if state in ("LIVE_CONFIGURED", "DEMO_MODE", "SOURCE_UNAVAILABLE") else None,
            "source_display_name": stock.source_display_name,
            "official_source_url": stock.official_url,
            "demo_label": DEMO_LABEL if c.settings.demo_mode else None, "disclaimer": DISCLAIMER}


@router.get("/health/ready")
async def ready(request: Request, response: Response):
    """Application readiness. e-RaktKosh upstream access is verified by an actual user search,
    because its bulk endpoint requires a selected state/component and should not be probed across catalogs."""
    c = _c(request)
    info = await c.orchestrator.stock.status()
    ok = info.state in ("READY", "DEMO")
    inv_state, inv_reason = "OK", ""
    workflow_state = "EPHEMERAL"
    workflow_reason = "run state is held in process memory"
    security_state, security_reason = "OK", ""
    try:
        await c.inventory.repo.ping()
    except Exception as e:  # noqa: BLE001 - any storage failure => not ready; message is safe text
        inv_state, inv_reason = "UNAVAILABLE", getattr(e, "code", type(e).__name__)
    if c.settings.app_env.lower() == "production":
        workflow_state = "NOT_DURABLE"
        workflow_reason = "shared durable run storage is not configured"
    if c.settings.app_env.lower() == "production":
        if any(not origin.startswith("https://") for origin in c.settings.cors_origins):
            security_state, security_reason = "MISCONFIGURED", "production frontend origins must use HTTPS"
        elif len(c.settings.token_hash_secret.get_secret_value().encode("utf-8")) < 32:
            security_state, security_reason = "MISCONFIGURED", "TOKEN_HASH_SECRET must contain at least 32 bytes"
        elif not c.settings.rate_limit_redis_url:
            security_state, security_reason = "NOT_SHARED", "production requires Redis-backed rate limits"
        else:
            redis = request.app.state.rate_limit_redis
            try:
                if redis is None:
                    raise RuntimeError("rate limit storage unavailable")
                await redis.ping()
            except Exception:
                security_state, security_reason = "RATE_LIMIT_UNAVAILABLE", "Redis health check failed"
    ok = ok and inv_state == "OK" and security_state == "OK" and c.settings.app_env.lower() != "production"
    if not ok:
        response.status_code = 503
    return {"status": "ready" if ok else "not_ready", "mode": c.settings.stock_source_mode,
            "stock_source": {"state": info.state, "reason": info.reason},
            "inventory_storage": {"backend": "memory", "state": inv_state,
                                  "reason": inv_reason},
            "workflow_storage": {"backend": "memory", "state": workflow_state,
                                 "reason": workflow_reason},
            "security": {"state": security_state, "reason": security_reason},
            "demo_label": DEMO_LABEL if c.settings.demo_mode else None}


@router.get("/metrics")
async def metrics(request: Request, authorization: str | None = Header(None)):
    c = _c(request)
    tok = c.settings.metrics_token
    if not tok or not authorization or not hmac.compare_digest(authorization, f"Bearer {tok}"):
        raise AppError("NOT_FOUND", "Not found.", status_code=404)
    statuses: dict[str, int] = {}
    for r in c.store.runs.values():
        statuses[r.status.value] = statuses.get(r.status.value, 0) + 1
    return {**c.metrics, "runs_by_status": statuses}
