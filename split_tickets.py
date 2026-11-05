"""
Split-ticket suggestions.

If A->C is waitlisted (or sold out) on a train, the same train often has confirmed seats on
A->B and B->C for some stop B in between. Two separate tickets, one train, one journey.
"""

from __future__ import annotations

import datetime
from typing import Callable, Optional

import trip_tools as tt
from live_availability import fetch_many_routes, pick_train, status_tone

DEFAULT_MAX_STOPS = 8


def needs_split(row: dict) -> bool:
    """A class row worth splitting: waitlisted or sold out (not departed/charted)."""
    if tt.is_closed_row(row):
        return False
    return status_tone(row.get("status", "")) in ("wait", "bad")


def is_bookable(row: dict) -> bool:
    """A class row with a confirmed seat/berth right now."""
    return (not tt.is_closed_row(row)) and status_tone(row.get("status", "")) == "good"


def split_candidates(classes) -> list:
    """Class rows (from a seat check) for which a split might help."""
    return [r for r in (classes or []) if needs_split(r)]


def _fare_total(f1, f2):
    return (f1 + f2) if (f1 is not None and f2 is not None) else None


def find_split_options(
    train_no: str,
    from_code: str,
    to_code: str,
    journey_date: datetime.date,
    quota: str = "GN",
    cls: str = "Any",
    full_classes: Optional[list] = None,
    max_stops: int = DEFAULT_MAX_STOPS,
    stops: Optional[list] = None,
    fetch: Callable = fetch_many_routes,
) -> dict:
    """
    Try A->B + B->C on one train for the stops B between A and C.

    Returns {ok, error, reason, targets, options, checked, failed}:
      targets  class rows of A->C that are waitlisted / sold out (what we try to fix)
      options  [{via, via_name, cls, date1, date2, status1, status2, fare1, fare2, total,
                 full_status, full_fare}], cheapest first
    Only stops with confirmed seats on BOTH halves in the same class are suggested. The most
    connected stations are tried first, capped at `max_stops` (2 live lookups each).
    """
    a, c = str(from_code).strip().upper(), str(to_code).strip().upper()
    train_no = str(train_no).strip()
    out = {"ok": True, "error": "", "reason": "", "targets": [], "options": [], "checked": 0, "failed": 0}

    if stops is None:
        from engine import train_stops_between  # lazy: keeps this module importable without the CSVs

        stops = train_stops_between(train_no, a, c)
    if not stops:
        out["reason"] = "This train has no stop between these stations, so it can't be split."
        return out

    if full_classes is None:
        full_key = (a, c, journey_date.isoformat(), quota)
        full = pick_train(fetch([full_key]).get(full_key) or {}, train_no)
        if not full.get("ok"):
            return {**out, "ok": False, "error": full.get("error") or "Could not get seats for the full journey."}
        full_classes = full.get("classes") or []

    targets = split_candidates(tt.filter_classes(full_classes, cls))
    out["targets"] = targets
    if not targets:
        out["reason"] = "No waitlisted or sold-out class on the full journey, so no split is needed."
        return out

    ranked = sorted(range(len(stops)), key=lambda i: (-int(stops[i].get("trains", 0)), i))
    picked = sorted(ranked[: max(1, int(max_stops))])
    chosen = [stops[i] for i in picked]

    keys, plan = [], []
    for s in chosen:
        d1 = journey_date
        d2 = journey_date + datetime.timedelta(days=int(s.get("day_offset", 0) or 0))
        k1 = (a, s["code"], d1.isoformat(), quota)
        k2 = (s["code"], c, d2.isoformat(), quota)
        keys += [k1, k2]
        plan.append((s, d1, d2, k1, k2))
    results = fetch(keys) or {}
    out["checked"] = len(chosen)

    full_by_cls = tt._by_class(full_classes)
    wanted = {tt._norm_cls(r.get("class")) for r in targets}
    for s, d1, d2, k1, k2 in plan:
        r1 = pick_train(results.get(k1) or {}, train_no)
        r2 = pick_train(results.get(k2) or {}, train_no)
        if not (r1.get("ok") and r1.get("found") and r2.get("ok") and r2.get("found")):
            out["failed"] += 1
            continue
        t1, t2 = tt._by_class(r1["classes"]), tt._by_class(r2["classes"])
        best = None
        for name in sorted(wanted):
            if name not in t1 or name not in t2:
                continue
            (f1, row1), (f2, row2) = t1[name], t2[name]
            if not (is_bookable(row1) and is_bookable(row2)):
                continue
            total = _fare_total(f1, f2)
            full_fare, full_row = full_by_cls.get(name, (None, {}))
            cand = {
                "via": s["code"],
                "via_name": s.get("name") or s["code"],
                "cls": name,
                "date1": d1.isoformat(),
                "date2": d2.isoformat(),
                "status1": str(row1.get("status") or "").strip(),
                "status2": str(row2.get("status") or "").strip(),
                "fare1": f1,
                "fare2": f2,
                "total": total,
                "full_status": str(full_row.get("status") or "").strip(),
                "full_fare": full_fare,
            }
            if best is None or (total is not None and (best["total"] is None or total < best["total"])):
                best = cand
        if best:
            out["options"].append(best)

    out["options"].sort(key=lambda o: (o["total"] is None, o["total"] or 0))
    if not out["options"]:
        out["reason"] = (
            f"Tried {out['checked']} stop(s) - none has confirmed seats on both halves"
            + (f" ({out['failed']} lookup(s) failed)." if out["failed"] else ".")
        )
    return out


def split_text(result: dict, train_no: str, from_code: str, to_code: str) -> str:
    """Plain-text summary of the options, for the copy box."""
    lines = [f"Split-ticket options - train {train_no}, {from_code} -> {to_code}"]
    for o in result.get("options") or []:
        total = f" = {tt._money(o['total'])}" if o.get("total") else ""
        lines.append(
            f"  {o['cls']}: {from_code}->{o['via']} ({o['status1']}, {o['date1']}) + "
            f"{o['via']}->{to_code} ({o['status2']}, {o['date2']}){total}"
        )
    lines.append("Two separate tickets on the same train. " + tt._FOOTER)
    return "\n".join(lines)
