"""Specialist agents. Each returns an AgentResult. Deterministic first; the LLM is only an optional,
schema-validated fallback for reading blood group / component from free text (never quantities
from the source, never locations, never stock)."""
from __future__ import annotations

import re

from pydantic import ValidationError

from app.agents.contracts import AgentResult, AgentSearchRequest, AgentStatus, Budget
from app.agents.tools import ToolBox, classify
from app.inventory.models import InventoryResponse, QualityFlag as Q, StockQuery, norm_text
from app.provider import ModelProvider, NullProvider
from app.safety import check_note
from app.schemas import AppError, BloodGroup, Component, IntakeDraft, utcnow

_GROUP = re.compile(r"(?<![A-Za-z])(AB|A|B|O)\s?(\+|-|positive|negative|pos|neg)(?:ve)?(?![A-Za-z])", re.I)
_SIGN = {"+": "+", "positive": "+", "pos": "+", "-": "-", "negative": "-", "neg": "-"}
_COMP = [("platelets", re.compile(r"platelet", re.I)), ("plasma", re.compile(r"plasma|ffp", re.I)),
         ("whole_blood", re.compile(r"whole\s+blood", re.I)),
         ("prbc", re.compile(r"prbc|packed\s+red|red\s+(blood\s+)?cell", re.I))]


def _res(agent, started, status, *, data=None, refs=None, warnings=None, errors=None) -> AgentResult:
    return AgentResult(agent=agent, status=status, data=data or {}, evidence_refs=refs or [],
                       warnings=warnings or [], errors=errors or [], started_at=started, completed_at=utcnow())


def parse_text(text: str) -> tuple[set[str], set[str]]:
    groups = {m.group(1).upper() + _SIGN[m.group(2).lower()] for m in _GROUP.finditer(text)}
    comps = {name for name, rx in _COMP if rx.search(text)}
    return groups, comps


class LocationMatchingAgent:
    """Resolves the request into validated filters and (later) ranks candidates by explicit rules."""
    name = "location_matching"

    def __init__(self, provider: ModelProvider, budget: Budget):
        self._p, self._b = provider, budget

    async def resolve(self, req: AgentSearchRequest) -> tuple[AgentResult, StockQuery | None, int | None, bool]:
        t0, f, used_llm, warnings = utcnow(), req.filters.model_copy(), False, []
        group, comp = f.blood_group, f.component
        if req.text:
            try:
                check_note(req.text)      # digit runs (phone/ID-like) never leave the server
            except AppError:
                return (_res(self.name, t0, AgentStatus.FAILED, errors=["TEXT_CONTAINS_IDENTIFIERS"]),
                        None, None, False)
            groups, comps = parse_text(req.text)
            if len(groups) > 1 or len(comps) > 1:
                return (_res(self.name, t0, AgentStatus.NEEDS_CLARIFICATION, data={
                    "question": "Your message mentions more than one blood group or component. Which one?"}),
                        None, None, False)
            group = group or (BloodGroup(next(iter(groups))) if groups else None)
            comp = comp or (Component(next(iter(comps))) if comps else None)
            if (group is None or comp is None) and not isinstance(self._p, NullProvider):
                try:
                    self._b.charge("llm_extract")
                    draft = IntakeDraft.model_validate(await self._p.extract_request(req.text))
                    used_llm = True
                    group, comp = group or draft.blood_group, comp or draft.component
                except (AppError, ValidationError):
                    warnings.append("Free-text helper unavailable or returned invalid output; ignored.")
        missing = [n for n, v in (("blood_group", group), ("component", comp)) if v is None]
        if not (f.state or f.district):
            missing.append("state_or_district")
        if missing:
            return (_res(self.name, t0, AgentStatus.NEEDS_CLARIFICATION, warnings=warnings, data={
                "missing": missing, "question": "Please provide: " + ", ".join(missing).replace("_", " ") + "."}),
                    None, None, used_llm)
        q = StockQuery(state=f.state, district=f.district, blood_group=group, component=comp,
                       centre_query=f.centre_query, page=f.page, page_size=f.page_size)
        return (_res(self.name, t0, AgentStatus.OK, warnings=warnings, data={"query": q.model_dump(mode="json")}),
                q, f.quantity, used_llm)


class InventoryAgent:
    name = "inventory_retrieval"

    def __init__(self, tools: ToolBox):
        self._t = tools

    async def run(self, q: StockQuery) -> tuple[AgentResult, InventoryResponse | None]:
        t0 = utcnow()
        resp: InventoryResponse = await self._t.call("search_stock", query=q)
        refs = [s.evidence_ref for it in resp.data for s in it.stock]
        status = AgentStatus.OK if resp.source_status.value in ("LIVE", "CACHED", "DEMO") else (
            AgentStatus.PARTIAL if resp.source_status.value == "STALE" else AgentStatus.FAILED)
        return (_res(self.name, t0, status, refs=refs, warnings=list(resp.warnings),
                     errors=[resp.error.code] if resp.error else [],
                     data={"source_status": resp.source_status.value, "banks": len(resp.data)}), resp)


class EvidenceAgent:
    """Machine-verifiable checks only; no model opinion counts as validation."""
    name = "evidence_verification"

    def __init__(self, tools: ToolBox):
        self._t = tools

    async def run(self, resp: InventoryResponse) -> tuple[AgentResult, dict[str, str]]:
        t0, quality, warns = utcnow(), {}, []
        warns += await self._t.call("verify_source_provenance", resp=resp)
        seen_phone: dict[str, str] = {}
        seen_name: dict[tuple, str] = {}
        dup_ids: set[str] = set()
        for it in resp.data:
            b = it.bank
            quality[b.bank_id] = classify(it)
            keys = [("p", "".join(c for c in (b.phone or "") if c.isdigit()) or None),
                    ("n", norm_text(b.name), norm_text(b.address))]
            for k in keys:
                if k[1] is None:
                    continue
                book = seen_phone if k[0] == "p" else seen_name
                kk = k[1] if k[0] == "p" else k[1:]
                if kk in book and book[kk] != b.bank_id:
                    dup_ids |= {book[kk], b.bank_id}
                book.setdefault(kk, b.bank_id)
        for it in resp.data:
            if it.bank.bank_id in dup_ids:      # flag only; NEVER merge on name/phone similarity
                if Q.DUPLICATE_CANDIDATE.value not in it.bank.flags:
                    it.bank.flags.append(Q.DUPLICATE_CANDIDATE.value)
                warns.append(f"{it.bank.name}: possible duplicate of another listed bank (not merged).")
        return _res(self.name, t0, AgentStatus.OK, warnings=warns, data={"quality": quality},
                    refs=[s.evidence_ref for it in resp.data for s in it.stock]), quality


class ContactPlanningAgent:
    name = "contact_planning"

    def __init__(self, tools: ToolBox):
        self._t = tools

    async def run(self, resp: InventoryResponse, quality: dict[str, str], quantity: int | None,
                  text_filter: bool, origin: tuple[float, float] | None = None):
        t0 = utcnow()
        ranked = await self._t.call("rank_candidate_banks", items=resp.data, quality=quality,
                                    quantity=quantity, text_filter=text_filter, origin=origin)
        plan = await self._t.call("prepare_contact_plan", ranked=ranked, origin=origin)
        return (_res(self.name, t0, AgentStatus.OK, data={"entries": len(plan)},
                     refs=[r for e in plan for r in e.evidence_refs]), ranked, plan)
