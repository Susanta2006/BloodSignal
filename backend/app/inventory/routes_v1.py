"""/api/v1 inventory endpoints. Thin: validation + HTTP mapping only; logic is in InventoryService."""
from __future__ import annotations

import hmac
from datetime import timedelta

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from app.inventory.models import (BankOut, BankStockOut, ErrorOut, InventoryResponse, Pagination,
                                  SourceStatus as S, StockQuery, norm_text, phone_href)
from app.inventory.repository import RepositoryError
from app.safety import DEMO_LABEL, DISCLAIMER
from app.schemas import AppError, BloodGroup, Component

router = APIRouter(prefix="/api/v1")


def _inv(request: Request):
    return request.app.state.container.inventory


def _render(resp: InventoryResponse) -> JSONResponse:
    # Stale data is HTTP 200 so the UI can render it; only "nothing usable" is 503.
    code = 503 if resp.source_status in (S.UNAVAILABLE, S.SOURCE_NOT_CONFIGURED, S.SOURCE_ACCESS_DENIED,
                                         S.SOURCE_PARSE_FAILED, S.SOURCE_RATE_LIMITED) else 200
    # The live e-RaktKosh endpoint advertises no-store; do not let browser/proxy caches retain stock.
    headers = {"Cache-Control": "no-store, no-cache, max-age=0, must-revalidate"}
    if code == 503 and resp.error and resp.error.retryable:
        headers["Retry-After"] = "60"
    return JSONResponse(status_code=code, content=resp.model_dump(mode="json"), headers=headers)


def _query(state, district, blood_group, component, centre_query, page, page_size) -> StockQuery:
    if not (state or district):
        raise AppError("LOCATION_REQUIRED", "Provide state and/or district.", status_code=422,
                       user_action="Choose a state or district.")
    return StockQuery(state=state, district=district, blood_group=blood_group, component=component,
                      centre_query=centre_query, page=page, page_size=page_size)


@router.get("/blood-stock/search")
async def search_stock(request: Request,
                       blood_group: BloodGroup, component: Component,
                       state: str | None = Query(None, max_length=80),
                       district: str | None = Query(None, max_length=80),
                       centre_query: str | None = Query(None, max_length=80),
                       page: int = Query(1, ge=1, le=1000), page_size: int = Query(20, ge=1, le=100)):
    q = _query(state, district, blood_group, component, centre_query, page, page_size)
    return _render(await _inv(request).search(q))


@router.post("/blood-stock/refresh")
async def refresh_stock(body: StockQuery, request: Request, authorization: str | None = Header(None)):
    """Operator-only. Disabled (404) unless REFRESH_ADMIN_TOKEN is set. Still bounded by the
    per-scope minimum interval, the DB lock, the breaker and the global POST rate limiter."""
    tok = request.app.state.container.settings.refresh_admin_token
    if not tok or not authorization or not hmac.compare_digest(authorization, f"Bearer {tok}"):
        raise AppError("NOT_FOUND", "Not found.", status_code=404)
    if not (body.state or body.district):
        raise AppError("LOCATION_REQUIRED", "Provide state and/or district.", status_code=422)
    return _render(await _inv(request).search(body, force_refresh=True))


def _bank_out(b) -> BankOut:
    return BankOut(bank_id=b.source_bank_id, name=b.name, address=b.address, phone=b.phone,
                   phone_href=phone_href(b.phone), email=b.email, category=b.category, type=b.type,
                   state=b.state, district=b.district,
                   flags=[] if b.phone else ["INCOMPLETE_CONTACT_DETAILS"], phones=b.phones,
                   emails=b.emails, faxes=b.faxes, contact_raw=b.contact_raw,
                   latitude=b.latitude, longitude=b.longitude)


@router.get("/blood-banks")
async def list_banks(request: Request, state: str | None = Query(None, max_length=80),
                     district: str | None = Query(None, max_length=80),
                     query: str | None = Query(None, max_length=80),
                     category: str | None = Query(None, max_length=80),
                     page: int = Query(1, ge=1, le=1000), page_size: int = Query(20, ge=1, le=100)):
    """Directory of banks PERSISTED from earlier successful syncs. Does not call the source.
    state/district/category match only source-provided fields (none in the current contract)."""
    c = request.app.state.container
    inv = c.inventory
    now = inv._clock()
    warnings = []
    if c.settings.demo_mode:
        from app.services.demo_data import demo_banks_for
        banks = [type("B", (), dict(source_bank_id=b.bank_id, name=b.name, address=b.address, phone=b.phone,
                                    email=None, category=None, type=None, state=None, district=b.district))
                 for b in demo_banks_for(district)]
        status, is_demo, sync_at = S.DEMO, True, None
        warnings.append(DEMO_LABEL)
    else:
        try:
            banks = await inv.repo.list_banks(inv.adapter.source_id)
            sync = await inv.repo.get_sync(inv.adapter.source_id)
        except RepositoryError:
            err = ErrorOut(code="DATABASE_UNAVAILABLE", message="Directory storage is unavailable.", retryable=True)
            return _render(inv._empty(StockQuery(blood_group=BloodGroup.O_NEG, component=Component.WHOLE_BLOOD,
                                                 page=page, page_size=page_size), S.UNAVAILABLE, err, now))
        is_demo, sync_at = False, sync.last_success_at if sync else None
        if sync_at is None:
            status = S.UNAVAILABLE
        else:
            age = (now - sync_at).total_seconds()
            status = (S.CACHED if age < c.settings.stock_cache_ttl_seconds else
                      S.STALE if age <= c.settings.max_stale_age_seconds else S.UNAVAILABLE)
        if status is S.UNAVAILABLE:
            banks = []
    for label, val, attr in (("state", state, "state"), ("district", district, "district"),
                             ("category", category, "category")):
        if val:
            banks = [b for b in banks if norm_text(getattr(b, attr)) == norm_text(val)]
            warnings.append(f"'{label}' filter matches only source-provided {label} values.") \
                if not is_demo and not any(getattr(b, attr) for b in banks) else None
    if query:
        n = norm_text(query)
        banks = [b for b in banks if n in (norm_text(b.name) or "")]
    banks = sorted(banks, key=lambda b: ((norm_text(b.name) or ""), b.source_bank_id))
    total, start = len(banks), (page - 1) * page_size
    out = InventoryResponse(
        data=[BankStockOut(bank=_bank_out(b), stock=[]) for b in banks[start:start + page_size]],
        source=inv.adapter.source_id, source_label=inv.adapter.source_label, source_status=status,
        cache_status="BYPASS" if is_demo else "HIT", fetched_at=sync_at, expires_at=None,
        is_stale=status is S.STALE, is_demo=is_demo,
        stale_age_seconds=int((now - sync_at).total_seconds()) if status is S.STALE and sync_at else None,
        warnings=[w for w in warnings if w], pagination=Pagination(page=page, page_size=page_size, total=total),
        disclaimer=DISCLAIMER, demo_label=DEMO_LABEL if is_demo else None,
        error=None if status is not S.UNAVAILABLE else ErrorOut(
            code="NO_USABLE_DIRECTORY", message="No sufficiently recent directory data is stored.", retryable=True))
    return _render(out)


@router.get("/blood-banks/{bank_id}")
async def bank_detail(bank_id: str, request: Request):
    """One persisted bank + every persisted stock row for it, each with its own timestamps."""
    c = request.app.state.container
    inv = c.inventory
    if c.settings.demo_mode:
        raise AppError("NOT_AVAILABLE_IN_DEMO", "Bank detail is available for persisted live data only.",
                       status_code=404)
    try:
        banks = [b for b in await inv.repo.list_banks(inv.adapter.source_id) if b.source_bank_id == bank_id]
        rows = await inv.repo.bank_stock(inv.adapter.source_id, bank_id) if banks else []
    except RepositoryError as e:
        raise AppError("DATABASE_UNAVAILABLE", "Storage is unavailable.", status_code=503,
                       retryable=True) from e
    if not banks:
        raise AppError("NOT_FOUND", "Bank not found.", status_code=404)
    now = inv._clock()
    newest = max((r.fetched_at for r in rows), default=None)
    from app.inventory.models import CacheMeta
    meta = CacheMeta(cache_key="bank", query_parameters={}, last_successful_refresh_at=newest,
                     expires_at=newest + timedelta(seconds=c.settings.stock_cache_ttl_seconds),
                     source_status="LIVE", record_count=len(rows)) if newest else None
    age = (now - newest).total_seconds() if newest else None
    if newest is None:
        status = S.UNAVAILABLE
    elif age < c.settings.stock_cache_ttl_seconds:
        status = S.CACHED
    elif age <= c.settings.max_stale_age_seconds:
        status = S.STALE
    else:
        status, rows = S.UNAVAILABLE, []
    q = StockQuery(blood_group=BloodGroup.O_NEG, component=Component.WHOLE_BLOOD, page_size=100)
    if status is S.UNAVAILABLE:
        return _render(inv._empty(q, status, ErrorOut(code="NO_USABLE_STOCK", message="No sufficiently recent "
                                                      "stock is stored for this bank.", retryable=True), now))
    resp = inv._compose(q, banks, rows, status, "HIT" if status is S.CACHED else "STALE_FALLBACK", meta, now)
    return _render(resp)


# ------------------------------------------------------------------ agentic search
from app.agents.contracts import AgentSearchRequest, AgentSearchResponse  # noqa: E402


@router.post("/agents/search", response_model=AgentSearchResponse)
async def agent_search(body: AgentSearchRequest, request: Request, response: Response):
    """Bounded multi-step search; returns a proposal only and never executes external actions."""
    response.headers["Cache-Control"] = "no-store, no-cache, max-age=0, must-revalidate"
    result = await request.app.state.container.agents.run(body)
    # The API is a planner only. Explicitly keep all proposed contact entries behind human review.
    result.approval_required = True
    result.action_taken = False
    return result
