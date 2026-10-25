"""One controlled orchestrator: typed tasks, bounded retries, replanning, persisted state.

planner suggests -> verifier independently checks -> human approves -> outcome recorded.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from app.config import Settings
from app.safety import verify_eligible
from app.schemas import (BloodBank, Candidate, EvidenceRecord, EvidenceState, MatchState, Run,
                         RunStatus, Section, TaskStatus, new_id, utcnow)
from app.services import matching
from app.services.directory import BankDirectory, DirectoryUnavailable
from app.services.geography import GEO_POLICY_VERSION, nearby_districts
from app.services import ranking
from app.services.routes import RoutesClient
from app.services.stock_source import SourceError, SourceResult, SourceUnavailable, StockSourceAdapter
from app.services.store import MemoryStore

log = logging.getLogger("orchestrator")
MAX_ROUTE_CALLS = 10


class TaskFailed(Exception):
    def __init__(self, code: str, retryable: bool = False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


class Orchestrator:
    def __init__(self, *, settings: Settings, store: MemoryStore, directory: BankDirectory,
                 stock: StockSourceAdapter, routes: RoutesClient,
                 clock: Callable[[], datetime] = utcnow,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.s, self.store, self.directory, self.stock, self.routes = settings, store, directory, stock, routes
        self.clock, self.sleep = clock, sleep
        self._origins: dict[str, tuple[float, float]] = {}   # consented GPS origins; transient
        self._ranking_origins: dict[str, tuple[float, float]] = {}  # approximate demo centers only

    def register_origin(self, run_id: str, origin: tuple[float, float]) -> None:
        self._origins[run_id] = origin

    def register_ranking_origin(self, run_id: str, origin: tuple[float, float]) -> None:
        """Set a coarse demo-only origin for straight-line ranking; never route against it."""
        self._ranking_origins[run_id] = origin

    # ------------------------------------------------------------ helpers
    def _set_status(self, run: Run, status: RunStatus, reason: str | None = None) -> None:
        before = run.status.value
        run.status = status
        run.status_history.append({"status": status.value, "at": self.clock().isoformat(),
                                   "reason_code": reason})
        if status in (RunStatus.COMPLETED, RunStatus.NEEDS_REVIEW, RunStatus.FAILED):
            run.completed_at = self.clock()
        self.store.audit(run.run_id, "system", "orchestrator", "run.status_changed",
                         run.run_id, {"status": before}, {"status": status.value, "reason": reason})

    def _skip(self, run_id: str, codes: list[str], reason: str) -> None:
        for t in self.store.pending_tasks(run_id, codes):
            t.status, t.output_summary, t.completed_at = TaskStatus.SKIPPED, reason, self.clock()

    async def _task(self, run_id: str, code: str,
                    fn: Callable[[], Awaitable[tuple[Any, str]]], *, max_attempts: int = 1,
                    retry_on: tuple[type[Exception], ...] = ()) -> Any:
        t = self.store.task(run_id, code)
        for attempt in range(1, max_attempts + 1):
            t.attempt, t.status, t.started_at, t.completed_at = attempt, TaskStatus.RUNNING, self.clock(), None
            try:
                result, summary = await fn()
            except retry_on as e:
                t.error_code = getattr(e, "code", type(e).__name__)
                if attempt < max_attempts:
                    delay = self.s.retry_base_delay_s * (2 ** (attempt - 1)) * (0.5 + random.random())
                    await self.sleep(delay)
                    continue
                t.status, t.completed_at, t.safe_to_retry = TaskStatus.FAILED, self.clock(), True
                raise TaskFailed(t.error_code, True) from e
            except TaskFailed as e:
                t.status, t.error_code, t.completed_at = TaskStatus.FAILED, e.code, self.clock()
                t.safe_to_retry = e.retryable
                raise
            except SourceError as e:  # denied / not configured / malformed / rate-limited: never retried
                t.status, t.error_code, t.completed_at = TaskStatus.FAILED, e.code, self.clock()
                t.safe_to_retry = False
                raise TaskFailed(e.code, False) from e
            except Exception as e:  # noqa: BLE001 - never leak details; log class only
                log.error("task_error", extra={"task": code, "error_class": type(e).__name__})
                t.status, t.error_code, t.completed_at = TaskStatus.FAILED, "INTERNAL_ERROR", self.clock()
                raise TaskFailed("INTERNAL_ERROR") from e
            t.status, t.output_summary, t.completed_at, t.error_code = (
                TaskStatus.SUCCEEDED, summary, self.clock(), None)
            return result

    # ------------------------------------------------------------ entry
    async def execute(self, run_id: str) -> None:
        run = self.store.runs[run_id]
        try:
            await self._execute(run)
        except TaskFailed as e:
            run.error_code = e.code
            self._set_status(run, RunStatus.NEEDS_REVIEW if e.retryable else RunStatus.FAILED, e.code)
            self._skip(run_id, [t.task_code for t in self.store.tasks[run_id]][:10], f"Not run: {e.code}")
        except Exception as e:  # noqa: BLE001
            log.error("run_error", extra={"run_id": run_id, "error_class": type(e).__name__})
            run.error_code = "INTERNAL_ERROR"
            self._set_status(run, RunStatus.FAILED, "INTERNAL_ERROR")
        finally:
            self._origins.pop(run_id, None)
            self._ranking_origins.pop(run_id, None)

    async def _execute(self, run: Run) -> None:
        rid, req = run.run_id, self.store.request_for(run)
        self._set_status(run, RunStatus.PLANNING)

        async def t01():
            if req.max_radius_km > self.s.max_radius_km:
                raise TaskFailed("RADIUS_TOO_LARGE")
            return None, "Request schema and limits valid"
        await self._task(rid, "T01_VALIDATE", t01)

        origin = self._origins.get(rid)

        async def t02():
            if req.location_mode == "device" and (not req.location_consent or origin is None):
                return False, "Device location unavailable or not consented; manual location needed"
            return True, f"Using {req.location_mode} location"
        if not await self._task(rid, "T02_RESOLVE_LOCATION", t02):
            run.summary = {"result_state": "NEEDS_LOCATION",
                           "user_action": "Enter a district and state manually."}
            self._set_status(run, RunStatus.NEEDS_LOCATION, "LOCATION_NOT_PROVIDED")
            self._skip(rid, [c for c in [t.task_code for t in self.store.tasks[rid]][2:10]],
                       "Waiting for location")
            return

        allow_expansion = bool(req.include_nearby_districts and req.district
                               and self.stock.source_type == "live")

        async def t03():
            run.plan = {"districts": [req.district] if req.district else [],
                        "radius_km": req.max_radius_km,
                        "geo_policy": GEO_POLICY_VERSION,
                        "freshness_hours": self.s.freshness_hours,
                        "rule_version": matching.RULE_VERSION,
                        "search_level": "local_district"}
            return None, ("Local-first search with bounded adjacent-district fallback enabled for live stock"
                          if allow_expansion else
                          "Local-first search; automatic expansion is disabled for this request/source")
        await self._task(rid, "T03_PLAN_SEARCH", t03)

        inventory_ready, inventory_reason = self.stock.configured()
        provides = self.stock.provides_directory
        self._set_status(run, RunStatus.RUNNING)

        # T04 discover (directory candidates only; some sources supply their own bank identity)
        async def t04():
            if provides and inventory_ready:
                return [], "Bank identities are supplied by the stock source itself"
            banks = await self.directory.discover(district=req.district, state=req.state,
                                                  origin=origin, radius_km=req.max_radius_km)
            return banks, f"{len(banks)} directory candidates (not stock evidence)"
        try:
            banks: list[BloodBank] = await self._task(rid, "T04_DISCOVER_BANKS", t04,
                                                      max_attempts=2, retry_on=(DirectoryUnavailable,))
        except TaskFailed:
            if not inventory_ready:
                self._not_configured(run, inventory_reason)
                return
            if allow_expansion:
                banks = []
                run.plan["local_directory_lookup_failed"] = True
                run.plan["expansion_warnings"] = [
                    "Local directory lookup failed; bounded neighboring-district lookups will still be attempted."
                ]
            else:
                raise
        by_id: dict[str, BloodBank] = {}

        def index(bs):
            by_id.clear()
            for b in bs:
                by_id[b.bank_id] = b
                if b.provider_place_id:
                    by_id.setdefault(b.provider_place_id, b)
        index(banks)

        # Directory matches remain useful when the inventory provider is not configured.
        if not inventory_ready:
            self._not_configured(run, inventory_reason, banks=banks, by_id=by_id)
            return

        if not provides and not banks and not allow_expansion:
            self._skip(rid, [t.task_code for t in self.store.tasks[rid]][4:10], "No banks to check")
            self._finalise(run, req, banks, [], [], source=self.stock, source_failed=False, records_n=0)
            return

        # T05 fetch stock (bounded retries; timeout is NOT "no stock")
        source_failed, source_err, result = False, None, None

        async def t05():
            try:
                res = await asyncio.wait_for(
                    self.stock.fetch_stock([b.bank_id for b in banks], req.blood_group.value,
                                           req.component.value, req.district, state=req.state,
                                           quantity=req.quantity, origin=origin),
                    timeout=self.s.stock_source_timeout_s)
            except asyncio.TimeoutError as e:
                raise SourceUnavailable("timeout") from e
            return res, f"{len(res.records)} records from {res.source_display_name}"
        if not provides and not banks and allow_expansion:
            # Do not ask a live stock adapter to fetch an empty list of bank IDs. Mark the local
            # phase as completed-with-no-candidates, then let the bounded district fallback run.
            task = self.store.task(rid, "T05_FETCH_STOCK")
            task.status, task.started_at, task.completed_at = TaskStatus.SUCCEEDED, self.clock(), self.clock()
            task.output_summary = "No local directory candidates; continuing to bounded neighboring-district lookup"
            result = SourceResult(
                source_id=self.stock.source_id,
                source_display_name=self.stock.source_display_name,
                source_type=self.stock.source_type,
                source_label=self.stock.source_label,
                official_url=self.stock.official_url,
                records=[], banks=[], checked_bank_ids=[],
            )
        else:
            try:
                result = await self._task(rid, "T05_FETCH_STOCK", t05,
                                          max_attempts=self.s.stock_source_max_attempts,
                                          retry_on=(SourceUnavailable,))
            except TaskFailed as e:
                source_failed, source_err = True, e.code
        if source_failed is False and result is None:
            source_failed, source_err = True, "SOURCE_UNAVAILABLE"

        if not source_failed and provides:
            banks = list(result.banks)
            index(banks)
            if not banks and not allow_expansion:
                self._skip(rid, ["T06_NORMALIZE_EVIDENCE", "T07_MATCH_REQUIREMENTS", "T08_CHECK_FRESHNESS",
                                 "T09_CALCULATE_ROUTES", "T10_VERIFY_RESULTS"],
                           "Source returned no usable records")
                self._finalise(run, req, banks, [], [], source=self.stock, source_failed=False,
                               records_n=0, result=result)
                return

        if source_failed:
            self._skip(rid, ["T06_NORMALIZE_EVIDENCE", "T07_MATCH_REQUIREMENTS", "T08_CHECK_FRESHNESS",
                             "T09_CALCULATE_ROUTES", "T10_VERIFY_RESULTS"],
                       f"Skipped: stock source unavailable ({source_err})")
            run.error_code = source_err
            decisions = [matching.unavailable_decision(b.bank_id, source_err or "SOURCE_UNAVAILABLE")
                         for b in banks]
            self._finalise(run, req, banks, decisions, [], source=self.stock, source_failed=True,
                           records_n=0, routes_by_bank={}, by_id=by_id, error_code=source_err)
            return

        # Bounded multi-district fallback is part of the live search pipeline, not a UI-only
        # suggestion. It runs when no single local facility can meet the requested quantity.
        # The configured geography map is still marked unverified; each returned bank retains its
        # source-provided district/address and the UI must not infer road distance from this scope.
        areas_checked = [req.district] if req.district else []
        expansion_warnings = list(run.plan.get("expansion_warnings", []))
        search_level = "local_district"
        if allow_expansion and result is not None and req.quantity > 0:
            def usable_by_bank(records):
                now = self.clock()
                grouped: dict[str, list] = {}
                for record in records:
                    if (matching.norm_group(record.blood_group) != req.blood_group.value
                            or matching.norm_component(record.component) != req.component.value
                            or matching.norm_unit(record.quantity_unit) != req.quantity_unit.value
                            or record.quantity is None or record.quantity <= 0
                            or record.reported_status != "available"
                            or not record.source_id
                            or not (record.source_url or record.source_record_id)
                            or record.source_updated_at is None
                            or record.source_updated_at > now + matching.CLOCK_SKEW
                            or now - record.source_updated_at > timedelta(hours=self.s.freshness_hours)):
                        continue
                    grouped.setdefault(record.bank_source_id, []).append(record)
                usable: dict[str, float] = {}
                for bank_source_id, group in grouped.items():
                    latest_at = max(row.source_updated_at for row in group)
                    latest = [row for row in group if row.source_updated_at == latest_at]
                    signatures = {(row.reported_status, row.quantity, matching.norm_unit(row.quantity_unit))
                                  for row in latest}
                    # Conflicting latest reports from the same facility do not count toward
                    # fulfillment; matching will expose them as a conflict for review.
                    if len(signatures) == 1:
                        usable[bank_source_id] = float(latest[0].quantity)
                return usable

            local_usable = usable_by_bank(result.records)
            local_total = sum(local_usable.values())
            if not any(quantity >= req.quantity for quantity in local_usable.values()):
                already_bank_ids = {bank.bank_id for bank in banks}
                already_place_ids = {bank.provider_place_id for bank in banks if bank.provider_place_id}
                seen_record_keys = {
                    (row.bank_source_id, row.source_id, row.blood_group, row.component,
                     row.source_record_id or row.raw_record_hash, row.source_updated_at,
                     row.quantity, row.quantity_unit, row.reported_status)
                    for row in result.records
                }
                neighboring_records = []
                adjacent_banks = []
                contributed_usable = False
                for district_name in nearby_districts(req.district)[:3]:
                    if any(
                        quantity >= req.quantity
                        for quantity in usable_by_bank([*result.records, *neighboring_records]).values()
                    ):
                        break
                    areas_checked.append(district_name)
                    new_banks = []
                    try:
                        if provides:
                            extra = await asyncio.wait_for(
                                self.stock.fetch_stock([], req.blood_group.value, req.component.value,
                                                       district_name, state=req.state, quantity=req.quantity, origin=origin),
                                timeout=self.s.stock_source_timeout_s)
                            candidate_banks = list(extra.banks)
                        else:
                            candidate_banks = await self.directory.discover(
                                district=district_name, state=req.state, origin=origin,
                                radius_km=req.max_radius_km)
                            new_ids = {bank.bank_id for bank in candidate_banks
                                       if bank.bank_id not in already_bank_ids
                                       and (not bank.provider_place_id
                                            or bank.provider_place_id not in already_place_ids)}
                            candidate_banks = [bank for bank in candidate_banks if bank.bank_id in new_ids]
                            if not candidate_banks:
                                continue
                            extra = await asyncio.wait_for(
                                self.stock.fetch_stock([bank.bank_id for bank in candidate_banks],
                                                       req.blood_group.value, req.component.value,
                                                       district_name, state=req.state, quantity=req.quantity, origin=origin),
                                timeout=self.s.stock_source_timeout_s)
                    except (DirectoryUnavailable, SourceError, asyncio.TimeoutError) as exc:
                        expansion_warnings.append(
                            f"Could not check configured nearby district {district_name} ({type(exc).__name__}); continuing."
                        )
                        continue
                    except Exception as exc:  # noqa: BLE001 - one district must not abort others
                        log.warning("adjacent_search_failed district=%s error_class=%s",
                                    district_name, type(exc).__name__)
                        expansion_warnings.append(
                            f"Could not check configured nearby district {district_name}; continuing with other areas."
                        )
                        continue

                    if provides:
                        candidate_banks = list(extra.banks)
                    for bank in candidate_banks:
                        if bank.bank_id in already_bank_ids:
                            continue
                        if bank.provider_place_id and bank.provider_place_id in already_place_ids:
                            continue
                        already_bank_ids.add(bank.bank_id)
                        if bank.provider_place_id:
                            already_place_ids.add(bank.provider_place_id)
                        new_banks.append(bank)
                    accepted_ids = {bank.bank_id for bank in new_banks}
                    for row in extra.records:
                        key = (row.bank_source_id, row.source_id, row.blood_group, row.component,
                               row.source_record_id or row.raw_record_hash, row.source_updated_at,
                               row.quantity, row.quantity_unit, row.reported_status)
                        if row.bank_source_id not in accepted_ids or key in seen_record_keys:
                            continue
                        seen_record_keys.add(key)
                        neighboring_records.append(row)
                    adjacent_banks.extend(new_banks)
                    # `provides_directory` sources use identities from the stock response; other
                    # sources use the directory's validated identity rows. Never invent a district.
                    usable_after = usable_by_bank([*result.records, *neighboring_records])
                    if sum(usable_after.values()) > local_total:
                        contributed_usable = True
                    if any(quantity >= req.quantity for quantity in usable_after.values()):
                        break

                if neighboring_records and contributed_usable:
                    merged_records = [*result.records, *neighboring_records]
                    record_bank_ids = {row.bank_source_id for row in neighboring_records}
                    banks.extend(bank for bank in adjacent_banks if bank.bank_id in record_bank_ids)
                    # Re-index the merged facilities, preserving their original source attributes.
                    index(banks)
                    result = result.model_copy(update={
                        "records": merged_records,
                        "banks": list(banks) if provides else result.banks,
                        "checked_bank_ids": list(dict.fromkeys([
                            *result.checked_bank_ids, *[bank.bank_id for bank in adjacent_banks]
                        ])),
                    })
                    search_level = "expanded_nearest_districts"
                    expansion_warnings.append(
                        f"Expanded to configured nearby districts ({GEO_POLICY_VERSION}); this geographic map is unverified. "
                        "Confirm the source-reported district and current stock directly with the facility."
                    )
                elif neighboring_records:
                    # Do not inject unverified/non-contributing adjacent rows into ranked results.
                    expansion_warnings.append(
                        "Nearby districts were checked, but no current, exact-scope positive stock report contributed to fulfillment."
                    )
            run.plan["districts"] = areas_checked
            run.plan["search_level"] = search_level
            if expansion_warnings:
                run.plan["expansion_warnings"] = expansion_warnings

        # T06 normalise + persist evidence (immutable)
        async def t06():
            evs, unmapped = [], 0
            for r in result.records:
                bank = by_id.get(r.bank_source_id)
                if bank is None:
                    unmapped += 1
                    continue
                ev = EvidenceRecord(**r.model_dump(), run_id=rid, bank_id=bank.bank_id,
                                    source_display_name=result.source_display_name,
                                    source_type=result.source_type,  # type: ignore[arg-type]
                                    source_label=result.source_label, official_url=result.official_url)
                self.store.add_evidence(ev)
                self.store.audit(rid, "system", "orchestrator", "evidence.recorded", ev.evidence_id,
                                 None, {"hash": ev.raw_record_hash})
                evs.append(ev)
            return evs, f"{len(evs)} evidence records stored, {unmapped} unmapped"
        evidence: list[EvidenceRecord] = await self._task(rid, "T06_NORMALIZE_EVIDENCE", t06)
        recs_by_bank: dict[str, list[EvidenceRecord]] = {}
        for ev in evidence:
            recs_by_bank.setdefault(ev.bank_id, []).append(ev)

        async def t07():
            ms = {b: matching.match_bank(b, rs, req) for b, rs in recs_by_bank.items()}
            n = sum(1 for m in ms.values() if m.state == MatchState.MATCHED_REPORTED)
            return ms, f"{n} of {len(ms)} banks match group, component, quantity and unit"
        matches = await self._task(rid, "T07_MATCH_REQUIREMENTS", t07)

        async def t08():
            now = self.clock()
            ds = [matching.decide(m, now, self.s.freshness_hours) for m in matches.values()]
            fresh = sum(1 for d in ds if d.section == Section.A_ELIGIBLE)
            return ds, f"{fresh} fresh within {self.s.freshness_hours}h policy"
        decisions = await self._task(rid, "T08_CHECK_FRESHNESS", t08)

        async def t09():
            out: dict[str, Any] = {}
            targets = [d for d in decisions if d.section in (Section.A_ELIGIBLE, Section.B_UNCERTAIN)]
            if origin is None:
                return out, "No consented origin: routes unknown (never estimated)"
            targets = targets[:MAX_ROUTE_CALLS]

            async def one(d):
                b = by_id[d.bank_id]
                if b.latitude is None or b.longitude is None:
                    return d.bank_id, None
                return d.bank_id, await self.routes.driving(origin, (b.latitude, b.longitude))
            for bank_id, res in await asyncio.gather(*(one(d) for d in targets)):
                out[bank_id] = res
            ok = sum(1 for v in out.values() if v)
            return out, f"{ok}/{len(targets)} driving routes calculated"
        routes_by_bank = await self._task(rid, "T09_CALCULATE_ROUTES", t09)

        self._set_status(run, RunStatus.VERIFYING)

        async def t10():
            now, rejected = self.clock(), []
            for d in decisions:
                if d.section == Section.A_ELIGIBLE:
                    violations = verify_eligible(d, req, now, self.s.freshness_hours)
                    if violations:
                        d.section, d.match_state = Section.E_UNKNOWN, MatchState.UNKNOWN
                        d.reason_codes += ["VERIFIER_REJECTED"] + violations
                        rejected.append(d.bank_id)
            return rejected, f"{len(rejected)} candidates rejected by independent verifier"
        rejected = await self._task(rid, "T10_VERIFY_RESULTS", t10)

        self._finalise(run, req, banks, decisions, rejected, source=self.stock, source_failed=False,
                       records_n=len(evidence), routes_by_bank=routes_by_bank, by_id=by_id,
                       result=result)

    # ------------------------------------------------------------ results
    def _not_configured(self, run: Run, reason: str, *, banks=None, by_id=None) -> None:
        rid = run.run_id
        t = self.store.task(rid, "T05_FETCH_STOCK")
        t.status, t.error_code, t.output_summary, t.completed_at = (
            TaskStatus.FAILED, "NOT_CONFIGURED", reason, self.clock())
        self._skip(rid, ["T06_NORMALIZE_EVIDENCE", "T07_MATCH_REQUIREMENTS",
                         "T08_CHECK_FRESHNESS", "T09_CALCULATE_ROUTES", "T10_VERIFY_RESULTS"],
                   "Not run: live stock source is not configured")
        req = self.store.request_for(run)
        banks = banks or []
        self._finalise(run, req, banks,
                       [matching.unavailable_decision(b.bank_id, "NOT_CONFIGURED") for b in banks],
                       [], source=self.stock, source_failed=True, records_n=0,
                       by_id=by_id or {b.bank_id: b for b in banks}, error_code="NOT_CONFIGURED")
        run.error_code, run.result_state = "NOT_CONFIGURED", "LIVE_NOT_CONFIGURED"
        run.summary.update({
            "result_state": "LIVE_NOT_CONFIGURED", "source_state": "LIVE_NOT_CONFIGURED",
            "source_reason": reason,
            "user_action": ("Live blood-stock integration is not configured, so no stock data was "
                            "retrieved. This is not evidence of no stock. Check the official source "
                            "or call blood banks directly."),
            "source": {"id": self.stock.source_id, "name": self.stock.source_display_name,
                       "type": self.stock.source_type},
            "official_source_url": self.stock.official_url, "freshness_hours": self.s.freshness_hours,
            "rule_version": matching.RULE_VERSION, "suggested_districts": [],
            "areas_checked": [req.district] if req.district else []})

    def _finalise(self, run, req, banks, decisions, rejected, *, source, source_failed,
                  records_n, routes_by_bank=None, by_id=None, result=None, error_code=None) -> None:
        now = self.clock()
        routes_by_bank = routes_by_bank or {}
        by_id = by_id or {b.bank_id: b for b in banks}
        decisions = list(decisions)
        decision_ids = {d.bank_id for d in decisions}
        for bank in banks:
            if bank.bank_id not in decision_ids:
                decision = (matching.unavailable_decision(bank.bank_id, error_code or "SOURCE_UNAVAILABLE")
                            if source_failed else matching.no_inventory_decision(bank.bank_id))
                decisions.append(decision)
                decision_ids.add(bank.bank_id)
        cands: list[Candidate] = []
        for d in decisions:
            bank, sel = by_id[d.bank_id], d.selected
            rt = routes_by_bank.get(d.bank_id)
            codes = list(d.reason_codes)
            if d.section in (Section.A_ELIGIBLE, Section.B_UNCERTAIN) and rt is None:
                codes.append("ROUTE_UNKNOWN")
            cands.append(Candidate(
                run_id=run.run_id, bank=bank, section=d.section, match_state=d.match_state,
                evidence_state=d.evidence_state, reason_codes=codes,
                unresolved_fields=d.unresolved_fields, evidence_ids=d.evidence_ids,
                source_id=sel.source_id if sel else None,
                source_type=sel.source_type if sel else "unknown",
                source_label=sel.source_label if sel else None,
                source_display_name=sel.source_display_name if sel else None,
                official_source_url=sel.official_url if sel else None,
                source_blood_group_label=sel.source_blood_group_label if sel else None,
                source_component_label=sel.source_component_label if sel else None,
                blood_group_reported=sel.blood_group if sel else None,
                component_reported=sel.component if sel else None,
                quantity_reported=sel.quantity if sel else None,
                quantity_unit_reported=sel.quantity_unit if sel else None,
                source_updated_at=sel.source_updated_at if sel else None,
                source_updated_at_raw=sel.source_updated_at_raw if sel else None,
                retrieved_at=sel.retrieved_at if sel else None,
                route_meters=rt.meters if rt else None, route_seconds=rt.seconds if rt else None,
                route_mode=rt.mode if rt else None, route_retrieved_at=rt.retrieved_at if rt else None,
                verified_for_search=(d.section == Section.A_ELIGIBLE and sel is not None
                                     and sel.source_type == "live"),
                display_note=("Synthetic demo inventory matched the request. It is not verified "
                              "hospital stock." if sel is not None and sel.source_type == "demo"
                              else matching.display_note(d, req, now)),
                rule_version=matching.RULE_VERSION))

        origin = self._origins.get(run.run_id) or self._ranking_origins.get(run.run_id)
        ranking.add_distance_metadata(cands, origin=origin, location_mode=req.location_mode)
        ranking.rank_candidates(
            cands,
            now=now,
            requested_quantity=req.quantity,
            freshness_hours=self.s.freshness_hours,
            distance_scale_km=self.s.ranking_distance_scale_km,
            freshness_weight=self.s.ranking_freshness_weight,
            quantity_weight=self.s.ranking_quantity_weight,
            distance_weight=self.s.ranking_distance_weight,
        )
        cands.sort(key=lambda candidate: candidate.rank or 0)
        self.store.candidates[run.run_id] = cands

        has_a = any(c.section == Section.A_ELIGIBLE and c.source_type == "live" for c in cands)
        has_b = any(c.section == Section.B_UNCERTAIN and c.source_type == "live" for c in cands)
        has_insufficient = any(
            c.section == Section.C_INSUFFICIENT
            and c.source_type == "live"
            and c.quantity_reported is not None
            and c.quantity_reported > 0
            for c in cands
        )
        has_demo = any(
            c.source_type == "demo"
            and c.quantity_reported is not None
            and c.quantity_reported > 0
            and c.match_state in (MatchState.MATCHED_REPORTED, MatchState.INSUFFICIENT)
            for c in cands
        )
        if source_failed:
            result_state, status = "SOURCE_UNAVAILABLE", RunStatus.NEEDS_REVIEW
            action = ("The source denied access, so no stock data was retrieved. This is not evidence of "
                      "no stock. Check the official source or call blood banks directly."
                      if error_code == "ACCESS_NOT_PERMITTED" else
                      "We could not check the stock source. This is not evidence of no stock. "
                      "Try again later, check the official source, or call blood banks directly.")
        elif has_a:
            result_state, status = "ELIGIBLE_FOUND", RunStatus.AWAITING_APPROVAL
            action = "Review the evidence, then approve a call list. Call to reconfirm before travelling."
        elif has_b:
            result_state, status = "UNCERTAIN_ONLY", RunStatus.AWAITING_APPROVAL
            action = "Only stale, conflicting or untimed records were found. Call these banks to reconfirm."
        elif has_insufficient:
            result_state, status = "INSUFFICIENT_STOCK", RunStatus.COMPLETED
            action = ("Matching blood-group and component records were found, but no single facility has "
                      "enough reported stock to fulfill the full requested quantity. The quantities shown "
                      "are partial source reports; call facilities to verify current availability.")
        elif has_demo:
            result_state, status = "DEMO_RESULTS_ONLY", RunStatus.COMPLETED
            action = ("Synthetic demo inventory matched this request. It is not real stock evidence "
                      "and does not confirm availability; contact a real blood bank directly.")
        else:
            result_state, status = "NO_MATCH_FOUND", RunStatus.COMPLETED
            action = "No matching record found in the sources checked. Try nearby districts or call banks directly."

        if self.s.demo_mode:
            source_state = "DEMO_MODE"
        elif source_failed:
            source_state = "SOURCE_UNAVAILABLE"
        elif result_state == "NO_MATCH_FOUND":
            source_state = "NO_MATCHING_RECORDS"
        else:
            source_state = "LIVE_RESULTS"
        search_level = run.plan.get("search_level", "local_district")
        areas_checked = run.plan.get("districts", [req.district] if req.district else [])
        checked_keys = {str(area).strip().casefold() for area in areas_checked if area}
        suggested = ([] if has_a else [
            area for area in (nearby_districts(req.district) if req.include_nearby_districts else [])
            if area.strip().casefold() not in checked_keys
        ])
        if result_state == "NO_MATCH_FOUND":
            if suggested:
                action = (
                    "No matching stock record was found in the sources checked. Nearby districts "
                    "listed here are suggestions only; stock there has not been confirmed."
                )
            else:
                action = (
                    "No matching stock record was returned from the sources checked. This does not "
                    "prove blood is unavailable; call facilities or the treating hospital directly."
                )
        expansion_warnings = run.plan.get("expansion_warnings", [])
        if search_level == "expanded_nearest_districts":
            expansion_note = (f"Checked configured nearby districts ({GEO_POLICY_VERSION}); the geographic map is unverified. "
                              "Facility district and current inventory must be confirmed with the source.")
        elif suggested:
            expansion_note = ("Suggested areas only: they were not checked by this run; we make no claim that stock exists there.")
        else:
            expansion_note = None
        run.result_state = result_state
        run.summary = {
            "result_state": result_state, "source_state": source_state, "user_action": action,
            "source_error_code": error_code,
            "official_source_url": getattr(source, "official_url", None),
            "records_skipped_unsupported": getattr(result, "skipped_unsupported", 0),
            "rows_unusable": getattr(result, "invalid_rows", 0),
            "banks_discovered": len(banks), "records_received": records_n,
            "source": ({"id": source.source_id, "name": source.source_display_name,
                        "type": source.source_type} if source else None),
            "freshness_hours": self.s.freshness_hours, "rule_version": matching.RULE_VERSION,
            "verifier_rejected": rejected, "suggested_districts": suggested,
            "search_level": search_level, "expansion_note": expansion_note,
            "expansion_warnings": expansion_warnings,
            "areas_checked": areas_checked,
        }
        self._set_status(run, status, result_state)
