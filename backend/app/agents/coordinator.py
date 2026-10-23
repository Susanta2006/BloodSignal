"""Coordinator: validated, bounded orchestration. It chooses steps; it cannot override inventory
validation, freshness policy or the final deterministic gate."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Callable

from app.agents.agents import (ContactPlanningAgent, EvidenceAgent, InventoryAgent,
                               LocationMatchingAgent)
from app.agents.contracts import (AgentSearchRequest, AgentSearchResponse, AgentStatus, Budget,
                                  BudgetExceeded, Candidate)
from app.agents.tools import ToolBox, ToolNotAllowed
from app.config import Settings
from app.inventory.models import SourceStatus as S
from app.inventory.service import InventoryService
from app.provider import ModelProvider
from app.safety import DEMO_LABEL
from app.schemas import new_id, utcnow

log = logging.getLogger("agents")


def verify_agent_output(out: AgentSearchResponse) -> list[str]:
    """Deterministic gate: every claim in the plan must trace to the inventory response."""
    v: list[str] = []
    inv = out.inventory
    if out.action_taken:
        v.append("AGENT_ACTION_EXECUTED_WITHOUT_APPROVAL")
    if not out.approval_required:
        v.append("HUMAN_APPROVAL_GATE_MISSING")
    if not out.contact_plan:
        return v
    if inv is None or inv.source_status in (S.UNAVAILABLE, S.SOURCE_NOT_CONFIGURED, S.SOURCE_ACCESS_DENIED,
                                            S.SOURCE_PARSE_FAILED, S.SOURCE_RATE_LIMITED):
        return ["PLAN_WITHOUT_USABLE_INVENTORY"]
    banks = {it.bank.bank_id: it for it in inv.data}
    refs = {s.evidence_ref for it in inv.data for s in it.stock}
    for e in out.contact_plan:
        if not e.approval_required or e.approval_status != "PENDING_HUMAN_REVIEW":
            v.append("CONTACT_PLAN_NOT_PENDING_HUMAN_REVIEW")
        it = banks.get(e.bank_id)
        if it is None:
            v.append("PLAN_BANK_NOT_IN_RESPONSE")
            continue
        if (e.phone, e.email, e.address, e.name) != (it.bank.phone, it.bank.email, it.bank.address, it.bank.name):
            v.append("PLAN_CONTACT_DETAILS_ALTERED")
        if not e.evidence_refs or not set(e.evidence_refs) <= refs:
            v.append("PLAN_EVIDENCE_NOT_IN_RESPONSE")
        if "reserv" in " ".join(e.why).casefold() and "not a reservation" not in e.caveat:
            v.append("PLAN_IMPLIES_RESERVATION")
    return v


class Coordinator:
    def __init__(self, *, settings: Settings, inventory: InventoryService, provider: ModelProvider,
                 clock: Callable[[], datetime] = utcnow):
        self.s, self.inv, self.provider, self._clock = settings, inventory, provider, clock

    async def run(self, req: AgentSearchRequest) -> AgentSearchResponse:
        t0, run_id = self._clock(), new_id()
        budget = Budget(self.s.agent_max_tool_calls, self.s.agent_max_workflow_seconds)
        state = {"steps": [], "llm": False}

        def done(status, **kw) -> AgentSearchResponse:
            return AgentSearchResponse(run_id=run_id, status=status, used_llm=state["llm"],
                                       tool_calls=list(budget.log), steps=state["steps"],
                                       started_at=t0, completed_at=self._clock(), **kw)
        try:
            return await asyncio.wait_for(self._run(req, budget, state, done),
                                          timeout=self.s.agent_max_workflow_seconds)
        except asyncio.TimeoutError:
            return done("TIMED_OUT", warnings=["The workflow exceeded its time limit; use the direct search."])
        except BudgetExceeded as e:
            return done("LIMIT_REACHED", warnings=[f"Stopped: {e.what} reached. Use the direct search."])
        except ToolNotAllowed as e:
            log.error("tool_not_allowed name=%s", e)
            return done("LIMIT_REACHED", warnings=["An unsupported tool was requested and refused."])

    async def _run(self, req, budget, state, done) -> AgentSearchResponse:
        tools = ToolBox(self.inv, budget)
        tools._agent_filters = req.filters
        loc, q, quantity, used = await LocationMatchingAgent(self.provider, budget).resolve(req)
        state["steps"].append(loc)
        state["llm"] = used
        if loc.status is AgentStatus.FAILED:
            return done("NEEDS_CLARIFICATION", clarification="Remove phone/ID-like numbers and try again.",
                        warnings=loc.errors)
        if q is None:
            return done("NEEDS_CLARIFICATION", clarification=loc.data.get("question"))
        inv_res, resp = await InventoryAgent(tools).run(q)
        state["steps"].append(inv_res)
        warnings = list(resp.warnings)

        # Expand scope only when explicitly requested quantity is not covered by positive
        # local reports. Each district query is charged through the bounded ToolBox.
        from app.services.geography import get_adjacent_districts
        from app.inventory.models import StockQuery
        requested_units = quantity if quantity is not None else req.filters.quantity
        def positive_units(items):
            return sum(
                float(stock.quantity or 0)
                for item in items for stock in item.stock
                if stock.quantity is not None and stock.quantity > 0
                and stock.reported_status == "available"
                and stock.blood_group == q.blood_group.value
                and stock.component == q.component.value
                and stock.quantity_unit == "units"
            )
        search_level = "local_district"
        local_units = positive_units(resp.data)
        if requested_units and local_units < requested_units and q.district:
            expanded_data = list(resp.data)
            adjacent = get_adjacent_districts(q.district)
            for district_name in adjacent:
                if local_units >= requested_units:
                    break
                expanded_query = StockQuery(
                    state=q.state, district=district_name, blood_group=q.blood_group,
                    component=q.component, centre_query=q.centre_query,
                    page=1, page_size=100,
                )
                try:
                    expanded = await tools.call("search_stock", query=expanded_query)
                except Exception as exc:
                    warnings.append(f"Nearby district search failed for {district_name}: {type(exc).__name__}.")
                    continue
                if expanded.source_status in (S.UNAVAILABLE, S.SOURCE_NOT_CONFIGURED,
                                                S.SOURCE_ACCESS_DENIED, S.SOURCE_PARSE_FAILED,
                                                S.SOURCE_RATE_LIMITED):
                    continue
                # A facility may have multiple rows; include only positive, relevant stock.
                eligible = []
                for item in expanded.data:
                    stocks = [stock for stock in item.stock
                              if stock.quantity is not None and stock.quantity > 0
                              and stock.reported_status == "available"
                              and stock.blood_group == q.blood_group.value
                              and stock.component == q.component.value
                              and stock.quantity_unit == "units"]
                    if stocks:
                        eligible.append(item.model_copy(update={"stock": stocks}))
                existing_ids = {item.bank.bank_id for item in expanded_data}
                expanded_data.extend(item for item in eligible if item.bank.bank_id not in existing_ids)
                local_units = positive_units(expanded_data)
            if len(expanded_data) > len(resp.data):
                # Keep the original response metadata; make the broader scope explicit.
                resp = resp.model_copy(update={
                    "data": expanded_data,
                    "pagination": resp.pagination.model_copy(update={"total": len(expanded_data)}),
                    "warnings": list(resp.warnings) + [
                        "Search expanded to adjacent districts; reported stock must be reconfirmed."
                    ],
                })
                search_level = "expanded_nearest_districts"
                warnings.append("search_level: expanded_nearest_districts")
        if resp.source_status in (S.UNAVAILABLE, S.SOURCE_NOT_CONFIGURED, S.SOURCE_ACCESS_DENIED,
                                  S.SOURCE_PARSE_FAILED, S.SOURCE_RATE_LIMITED):
            return done("UNAVAILABLE", inventory=resp, warnings=warnings +
                        ["Source unavailable. This is NOT evidence of zero stock; contact blood banks directly."])
        # Defence in depth: zero/negative stock is never surfaced as a candidate.
        resp = resp.model_copy(update={"data": [
            item.model_copy(update={"stock": [stock for stock in item.stock
                if stock.quantity is not None and stock.quantity > 0]})
            for item in resp.data if any(stock.quantity is not None and stock.quantity > 0
                                         for stock in item.stock)
        ]})
        ev_res, quality = await EvidenceAgent(tools).run(resp)
        state["steps"].append(ev_res)
        warnings += ev_res.warnings
        origin = ((req.filters.latitude, req.filters.longitude)
                  if req.filters.latitude is not None and req.filters.longitude is not None else None)
        cp_res, ranked, plan = await ContactPlanningAgent(tools).run(resp, quality, quantity,
                                                                    bool(q.centre_query), origin=origin)
        state["steps"].append(cp_res)
        from app.services.ranking import haversine_distance_km
        cands = []
        for n, it, why in ranked:
            st = it.stock[0]
            destination = ((it.bank.latitude, it.bank.longitude)
                           if it.bank.latitude is not None and it.bank.longitude is not None else None)
            distance = haversine_distance_km(origin, destination)
            cands.append(Candidate(
                rank=n, bank_id=it.bank.bank_id, name=it.bank.name,
                evidence_quality=quality[it.bank.bank_id],
                match_type=("EXPANDED_NEAREST_DISTRICTS" if search_level == "expanded_nearest_districts" else
                           ("EXACT_SCOPE_TEXT_FILTER" if q.centre_query else "EXACT_SCOPE")),
                reasons=why, flags=it.bank.flags,
                evidence_refs=[s.evidence_ref for s in it.stock],
                source_updated_at=st.source_updated_at, fetched_at=st.fetched_at,
                freshness=st.source_freshness, reported_blood_group=st.blood_group,
                reported_component=st.source_component_label or st.component,
                reported_quantity=st.quantity, reported_quantity_unit=st.quantity_unit,
                distance_km=distance, distance_type="straight_line" if distance is not None else None))
        out = done("COMPLETED", inventory=resp, candidates=cands, contact_plan=plan, warnings=warnings,
                   search_level=search_level,
                   evidence_refs=sorted({r for e in plan for r in e.evidence_refs}))
        bad = verify_agent_output(out)
        if bad:
            log.error("agent_output_rejected codes=%s", ",".join(bad))
            out = out.model_copy(update={"contact_plan": [], "candidates": [], "evidence_refs": [],
                                         "warnings": warnings + ["Contact plan withheld: failed validation."]})
        if resp.is_demo:
            out.warnings.append(DEMO_LABEL)
        return out
