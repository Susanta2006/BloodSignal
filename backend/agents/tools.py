"""Narrow, deterministic tools. This is the ONLY surface agents can act through.

No tool takes SQL, writes inventory, contacts a bank or reserves stock. `search_stock` reaches the
database/source only via InventoryService (the single trusted writer).
"""
from __future__ import annotations

from app.agents.contracts import Budget
from app.inventory.models import (BankStockOut, InventoryResponse, QualityFlag as Q, StockOut,
                                  StockQuery)
from app.inventory.service import InventoryService

ALLOWED_TOOLS = frozenset({"search_stock", "refresh_stock_cache", "check_record_freshness",
                           "verify_source_provenance", "rank_candidate_banks", "prepare_contact_plan"})
_BAD = {Q.CONFLICTING_SOURCE_DATA.value, Q.FUTURE_SOURCE_TIMESTAMP.value}
_LIMITED = {Q.MISSING_SOURCE_TIMESTAMP.value, Q.MISSING_QUANTITY.value, Q.UNKNOWN_QUANTITY_UNIT.value,
            Q.NOT_IN_LATEST_REFRESH.value}


class ToolNotAllowed(Exception):
    pass


class ToolBox:
    def __init__(self, inventory: InventoryService, budget: Budget):
        self._inv, self._b = inventory, budget

    async def call(self, name: str, **kw):
        if name not in ALLOWED_TOOLS:
            raise ToolNotAllowed(name)
        self._b.charge(name)
        return await getattr(self, "_" + name)(**kw)

    # ---- data tools (go through the trusted service)
    async def _search_stock(self, *, query: StockQuery) -> InventoryResponse:
        return await self._inv.search(query)

    async def _refresh_stock_cache(self, *, query: StockQuery) -> InventoryResponse:
        # honours the per-scope minimum interval, DB lock and breaker inside the service
        return await self._inv.search(query, force_refresh=True)

    # ---- deterministic checks
    async def _check_record_freshness(self, *, stock: StockOut) -> str:
        return stock.source_freshness

    async def _verify_source_provenance(self, *, resp: InventoryResponse) -> list[str]:
        """One call checks the whole response (budget is per tool call, not per row)."""
        out: list[str] = []
        for it in resp.data:
            codes: set[str] = set()
            if not resp.source:
                codes.add("NO_SOURCE")
            if not it.bank.bank_id:
                codes.add("NO_BANK_ID")
            for st in it.stock:
                if not st.evidence_ref:
                    codes.add("NO_EVIDENCE_REF")
                if st.source_updated_at is None:
                    codes.add(Q.MISSING_SOURCE_TIMESTAMP.value)
            if codes:
                out.append(f"{it.bank.name}: {', '.join(sorted(codes))}")
        return out

    async def _rank_candidate_banks(self, *, items: list[BankStockOut], quality: dict[str, str],
                                    quantity: int | None, text_filter: bool,
                                    origin: tuple[float, float] | None = None):
        from app.services.ranking import haversine_distance_km, valid_coordinate_pair
        def key(it: BankStockOut):
            st = it.stock[0]
            q = {"GOOD": 0, "LIMITED": 1, "UNRELIABLE": 2}[quality[it.bank.bank_id]]
            enough = 1
            if (quantity and st.quantity is not None and st.quantity_unit == "units"
                    and st.reported_status == "available" and st.quantity >= quantity):
                enough = 0
            ts = -(st.source_updated_at.timestamp()) if st.source_updated_at else 0.0
            destination = ((it.bank.latitude, it.bank.longitude)
                           if valid_coordinate_pair(it.bank.latitude, it.bank.longitude) else None)
            distance = haversine_distance_km(origin, destination)
            # Evidence quality and requested-quantity sufficiency lead; freshness precedes proximity.
            # Unknown distances sort after known distances and are never guessed/geocoded.
            distance_key = distance if distance is not None else float("inf")
            return (q, enough, ts, distance_key, (it.bank.name or "").casefold(), it.bank.bank_id)
        ranked = sorted([i for i in items if i.stock], key=key)
        out = []
        for n, it in enumerate(ranked, 1):
            st = it.stock[0]
            quantity_label = ("quantity not reported" if st.quantity is None else
                              f"{st.quantity:g} {st.quantity_unit or '(unit not stated)'}")
            why = [f"Source reports {st.blood_group} {st.source_component_label or st.component}: "
                   f"{quantity_label}."]
            if quantity and st.quantity is not None and st.quantity_unit == "units":
                why.append(f"Reported quantity {'covers' if st.quantity >= quantity else 'is below'} the "
                           f"{quantity} unit(s) requested (as reported; not guaranteed).")
            why.append(f"Evidence quality: {quality[it.bank.bank_id]}.")
            if st.source_updated_at:
                freshness_label = "STALE" if st.source_freshness == "OLD" else st.source_freshness
                why.append(f"{freshness_label}: source stock timestamp {st.source_updated_at.isoformat()}.")
            else:
                why.append("Source stock timestamp is unknown; call to verify before travelling.")
            if it.bank.latitude is not None and it.bank.longitude is not None and origin is not None:
                distance = haversine_distance_km(origin, (it.bank.latitude, it.bank.longitude))
                if distance is not None:
                    why.append(f"Approximate straight-line distance: {distance:.2f} km; not road distance or ETA.")
            elif origin is None:
                why.append("Distance unavailable because no device coordinates were supplied.")
            else:
                why.append("Distance unavailable because this facility has no validated coordinates.")
            out.append((n, it, why))
        return out

    async def _prepare_contact_plan(self, *, ranked, origin: tuple[float, float] | None = None):
        from app.agents.contracts import ContactEntry
        from app.services.ranking import haversine_distance_km
        plan = []
        for n, it, why in ranked:
            b = it.bank
            if quality_is_unreliable(it):
                continue
            checklist = ["Confirm the exact blood group and component are in stock now.",
                         "Confirm the number of units and their unit/volume.",
                         "Ask what documents, cross-match and prescription the bank requires.",
                         "Ask whether units can be held and for how long (the bank decides)."]
            if not b.phone:
                checklist.insert(0, "No phone number is listed by the source; find the bank's official contact.")
            plan.append(ContactEntry(rank=n, bank_id=b.bank_id, name=b.name, phone=b.phone,
                                     phone_href=b.phone_href, email=b.email, address=b.address, why=why,
                                     checklist=checklist, evidence_refs=[s.evidence_ref for s in it.stock],
                                     source_updated_at=it.stock[0].source_updated_at,
                                     fetched_at=it.stock[0].fetched_at,
                                     freshness=it.stock[0].source_freshness,
                                     reported_blood_group=it.stock[0].blood_group,
                                     reported_component=it.stock[0].source_component_label or it.stock[0].component,
                                     reported_quantity=it.stock[0].quantity,
                                     reported_quantity_unit=it.stock[0].quantity_unit,
                                     distance_km=(haversine_distance_km(
                                         origin, (b.latitude, b.longitude)
                                         if b.latitude is not None and b.longitude is not None else None)),
                                     distance_type="straight_line" if origin is not None and b.latitude is not None and b.longitude is not None else None))
        return plan


def quality_is_unreliable(it: BankStockOut) -> bool:
    return any(set(s.flags) & _BAD for s in it.stock)


def classify(it: BankStockOut) -> str:
    """Deterministic evidence-quality classification (the Evidence agent's core)."""
    if any(set(s.flags) & _BAD for s in it.stock):
        return "UNRELIABLE"
    for s in it.stock:
        if (set(s.flags) & _LIMITED or s.source_freshness != "WITHIN_WINDOW" or s.quantity is None
                or not s.quantity_unit):
            return "LIMITED"
    return "GOOD"
