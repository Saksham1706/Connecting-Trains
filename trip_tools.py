"""
Pure helpers for fares, travel-class filtering, CSV export and plain-text route summaries.

No Streamlit imports here, so everything can be unit-tested on its own.

Live data comes from `live_availability.fetch_route_availability`; a "route" below is the dict
that function returns ({ok, error, trains: [{train_number, classes: [...]}, ...]}).
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Iterable, Optional

import pandas as pd

from live_availability import pick_train

# Classes offered in the UI filter (the API also returns 3E, EC, 2S ... which stay visible under "Any").
CLASS_CHOICES = ["Any", "SL", "3A", "2A", "1A", "CC"]

SORT_CONNECTION = {
    "Best match": "search",
    "Cheapest total fare": "fare",
    "Fastest": "fastest",
    "Shortest wait": "wait",
}
SORT_DIRECT = {
    "As searched": "search",
    "Cheapest fare": "fare",
}

FARE_COLUMNS = [
    "Fare_Class",
    "Fare_Train_1",
    "Fare_Train_2",
    "Total_Fare",
    "Seat_Train_1",
    "Seat_Train_2",
]


# =====================================================================================
# FARES + CLASSES
# =====================================================================================
def parse_fare(value: Any) -> Optional[int]:
    """'1,150' / '₹1150' / '1150.0' -> 1150.  Empty, zero or junk -> None."""
    if value is None:
        return None
    s = re.sub(r"[^\d.]", "", str(value))
    if not s:
        return None
    try:
        n = float(s)
    except ValueError:
        return None
    return int(round(n)) if n > 0 else None


def _norm_cls(cls: Any) -> str:
    return str(cls or "").strip().upper()


def is_closed_row(row: dict) -> bool:
    """
    One class row that can't be booked any more: status 'Train Departed' or 'Charting Done'.

    Deliberately NOT based on the prediction text 'No More Booking': ConfirmTkt also sends it with
    status 'Regret' / 'Not Available' (sold out) - for future dates too. Those rows keep their real
    fare and status so the user can see them; they are not "gone".
    """
    status = str(row.get("status") or "").upper()
    return "DEPART" in status or "CHARTING" in status


def open_classes(classes: Optional[Iterable[dict]]) -> list:
    """Class rows that can still be booked."""
    return [r for r in (classes or []) if not is_closed_row(r)]


def is_closed(classes: Optional[Iterable[dict]]) -> bool:
    """True if the train has class rows but none of them can be booked (left, charted or sold out)."""
    classes = list(classes or [])
    return bool(classes) and not open_classes(classes)


def _by_class(classes: Optional[Iterable[dict]]) -> dict:
    """{'3A': (fare|None, row), ...}  First row wins if a class repeats."""
    out: dict = {}
    for row in classes or []:
        key = _norm_cls(row.get("class"))
        if key and key not in out:
            out[key] = (parse_fare(row.get("fare")), row)
    return out


def filter_classes(classes: Optional[Iterable[dict]], cls: str = "Any") -> list:
    """Keep only the rows for one class ('Any' keeps everything)."""
    classes = list(classes or [])
    want = _norm_cls(cls)
    if not want or want == "ANY":
        return classes
    return [r for r in classes if _norm_cls(r.get("class")) == want]


def single_leg_fare(classes: Optional[Iterable[dict]], cls: str = "Any") -> Optional[dict]:
    """
    Fare/status for ONE train.
    cls given  -> that class (None if the train doesn't offer it).
    cls 'Any'  -> the cheapest class that has a fare (None if no fares at all).
    """
    table = _by_class(classes)
    want = _norm_cls(cls)
    if want and want != "ANY":
        if want not in table:
            return None
        fare, row = table[want]
        return {"cls": want, "fare": fare, "status": str(row.get("status") or "").strip()}
    priced = {k: v for k, v in table.items() if v[0] is not None}
    if not priced:
        return None
    best = min(priced, key=lambda k: (priced[k][0], k))
    fare, row = priced[best]
    return {"cls": best, "fare": fare, "status": str(row.get("status") or "").strip()}


def pick_fare_pair(classes1, classes2, cls: str = "Any") -> Optional[dict]:
    """
    Choose which class to quote for a 2-train journey.
      * a class is selected -> that class on BOTH trains (None if either lacks it)
      * 'Any' -> cheapest class both trains offer; else cheapest of each train (mixed classes)
    Returns {cls1, cls2, fare1, fare2, total, status1, status2}; total is None if a fare is missing.
    """
    t1, t2 = _by_class(classes1), _by_class(classes2)
    want = _norm_cls(cls)

    def pack(c1, c2):
        f1, r1 = t1[c1]
        f2, r2 = t2[c2]
        return {
            "cls1": c1,
            "cls2": c2,
            "fare1": f1,
            "fare2": f2,
            "total": (f1 + f2) if (f1 is not None and f2 is not None) else None,
            "status1": str(r1.get("status") or "").strip(),
            "status2": str(r2.get("status") or "").strip(),
        }

    if want and want != "ANY":
        if want in t1 and want in t2:
            return pack(want, want)
        return None

    p1 = {k: v for k, v in t1.items() if v[0] is not None}
    p2 = {k: v for k, v in t2.items() if v[0] is not None}
    common = [k for k in p1 if k in p2]
    if common:
        best = min(common, key=lambda k: (p1[k][0] + p2[k][0], k))
        return pack(best, best)
    if p1 and p2:
        c1 = min(p1, key=lambda k: (p1[k][0], k))
        c2 = min(p2, key=lambda k: (p2[k][0], k))
        return pack(c1, c2)
    return None


# =====================================================================================
# ANNOTATE RESULT TABLES WITH LIVE DATA
# =====================================================================================
def _empty_fare_frame(n: int) -> dict:
    return {
        "Fare_Class": [""] * n,
        "Fare_Train_1": [None] * n,
        "Fare_Train_2": [None] * n,
        "Total_Fare": [None] * n,
        "Seat_Train_1": [""] * n,
        "Seat_Train_2": [""] * n,
        "Fare_Data": ["pending"] * n,
    }


def _route_key(from_code: str, to_code: str, d: datetime.date, quota: str) -> tuple:
    return (str(from_code).upper(), str(to_code).upper(), d.isoformat(), quota or "GN")


def route_key(from_code: str, to_code: str, d: datetime.date, quota: str = "GN") -> tuple:
    return _route_key(from_code, to_code, d, quota)


def row_codes(row: dict, start_code: str, end_code: str) -> tuple:
    """Stations this row really uses; nearby-station searches store them per row."""
    s = str(row.get("Start_Code") or "").strip() or start_code
    e = str(row.get("End_Code") or "").strip() or end_code
    return s, e


def connection_route_keys(
    df: pd.DataFrame, start_code: str, end_code: str, journey_date: datetime.date, quota: str
) -> list:
    """Every live lookup needed to price/classify these connections (de-duplicated, in order)."""
    keys = []
    for row in df.to_dict("records"):
        s, e = row_codes(row, start_code, end_code)
        off = int(row.get("Train2_Day_Offset", 0) or 0)
        keys.append(route_key(s, row["Via_Code"], journey_date, quota))
        keys.append(
            route_key(row["Via_Code"], e, journey_date + datetime.timedelta(days=off), quota)
        )
    return list(dict.fromkeys(keys))


def direct_route_keys(df: pd.DataFrame, start_code: str, end_code: str, quota: str) -> list:
    keys = []
    for row in df.to_dict("records"):
        iso = str(row.get("Board_Date") or "").strip()
        if iso:
            s, e = row_codes(row, start_code, end_code)
            keys.append(route_key(s, e, datetime.date.fromisoformat(iso), quota))
    return list(dict.fromkeys(keys))


def annotate_connections(
    df: pd.DataFrame,
    routes: dict,
    start_code: str,
    end_code: str,
    journey_date: Optional[datetime.date],
    quota: str = "GN",
    cls: str = "Any",
) -> pd.DataFrame:
    """
    Add fare/class columns to the connections table from already-fetched live routes.

    Fare_Data tells the UI what happened to each row:
      pending   - live data not fetched yet
      ok        - both trains found, fare/class resolved
      no_class  - both trains found but one of them doesn't offer the chosen class
      closed    - a train has left or is charted (booking closed) - never offered
      error     - the live lookup itself failed
      no_data   - a train wasn't in the live results
      no_fare   - trains found but the API returned no price
    """
    out = df.copy()
    cols = _empty_fare_frame(len(out))
    if journey_date is not None and len(out):
        for i, row in enumerate(out.to_dict("records")):
            off = int(row.get("Train2_Day_Offset", 0) or 0)
            s, e = row_codes(row, start_code, end_code)
            r1 = routes.get(route_key(s, row["Via_Code"], journey_date, quota))
            r2 = routes.get(
                route_key(row["Via_Code"], e, journey_date + datetime.timedelta(days=off), quota)
            )
            if r1 is None or r2 is None:
                continue  # stays "pending"
            if not (r1.get("ok") and r2.get("ok")):
                cols["Fare_Data"][i] = "error"  # API failed - say so, don't pretend it's "no seats"
                continue
            p1 = pick_train(r1, str(row["Train_1_No"]))
            p2 = pick_train(r2, str(row["Train_2_No"]))
            if not (p1["found"] and p2["found"]):
                cols["Fare_Data"][i] = "no_data"
                continue
            if is_closed(p1["classes"]) or is_closed(p2["classes"]):
                cols["Fare_Data"][i] = "closed"  # can't be booked, so never rank it as "cheapest"
                continue
            # Only bookable classes may be quoted: a closed class must not win "cheapest".
            pair = pick_fare_pair(open_classes(p1["classes"]), open_classes(p2["classes"]), cls)
            if pair is None:
                wanted = _norm_cls(cls) not in ("", "ANY")
                cols["Fare_Data"][i] = "no_class" if wanted else "no_fare"
                continue
            cols["Fare_Data"][i] = "ok" if pair["total"] is not None else "no_fare"
            cols["Fare_Class"][i] = (
                pair["cls1"] if pair["cls1"] == pair["cls2"] else f"{pair['cls1']} + {pair['cls2']}"
            )
            cols["Fare_Train_1"][i] = pair["fare1"]
            cols["Fare_Train_2"][i] = pair["fare2"]
            cols["Total_Fare"][i] = pair["total"]
            cols["Seat_Train_1"][i] = pair["status1"]
            cols["Seat_Train_2"][i] = pair["status2"]
    for name, values in cols.items():
        if name in ("Fare_Train_1", "Fare_Train_2", "Total_Fare"):
            out[name] = pd.array(values, dtype="Int64")
        else:
            out[name] = values
    return out


def annotate_directs(
    df: pd.DataFrame,
    routes: dict,
    start_code: str,
    end_code: str,
    quota: str = "GN",
    cls: str = "Any",
) -> pd.DataFrame:
    """Same idea as annotate_connections, for single-train journeys."""
    out = df.copy()
    n = len(out)
    fare_cls, fare, seat, state = [""] * n, [None] * n, [""] * n, ["pending"] * n
    for i, row in enumerate(out.to_dict("records")):
        iso = str(row.get("Board_Date") or "").strip()
        if not iso:
            continue
        s, e = row_codes(row, start_code, end_code)
        route = routes.get(route_key(s, e, datetime.date.fromisoformat(iso), quota))
        if route is None:
            continue
        if not route.get("ok"):
            state[i] = "error"
            continue
        p = pick_train(route, str(row["Train_No"]))
        if not p["found"]:
            state[i] = "no_data"
            continue
        if is_closed(p["classes"]):
            state[i] = "closed"
            continue
        info = single_leg_fare(open_classes(p["classes"]), cls)
        if info is None:
            wanted = _norm_cls(cls) not in ("", "ANY")
            state[i] = "no_class" if wanted else "no_fare"
            continue
        fare_cls[i], fare[i], seat[i] = info["cls"], info["fare"], info["status"]
        state[i] = "ok" if info["fare"] is not None else "no_fare"
    out["Fare_Class"] = fare_cls
    out["Fare"] = pd.array(fare, dtype="Int64")
    out["Seat_Status"] = seat
    out["Fare_Data"] = state
    return out


def view_summary(df: pd.DataFrame) -> dict:
    """How many rows have live data? Used for captions like '78 of 78 priced'."""
    if "Fare_Data" not in df.columns or df.empty:
        return {"total": len(df), "loaded": 0, "pending": len(df)}
    pending = int((df["Fare_Data"] == "pending").sum())
    return {"total": len(df), "loaded": len(df) - pending, "pending": pending}


def filter_and_sort(
    df: pd.DataFrame,
    cls: str = "Any",
    max_fare: int = 0,
    sort: str = "search",
    fare_col: str = "Total_Fare",
) -> tuple[pd.DataFrame, dict]:
    """
    Apply the class filter, budget filter and sort.  Returns (view, hidden_counts).

    The class filter hides only routes CONFIRMED not to offer that class (state 'no_class').
    Rows that are still 'pending', hit an API 'error', or weren't in the live list ('no_data')
    are kept - we can't say they lack the class - and `hidden['unverified']` counts them.
    """
    hidden = {"class": 0, "budget": 0, "unverified": 0, "closed": 0}
    out = df
    if "Fare_Data" in out.columns:
        gone = out["Fare_Data"] == "closed"  # booking closed - never offer these
        hidden["closed"] = int(gone.sum())
        out = out[~gone]
        want = _norm_cls(cls) not in ("", "ANY")
        if want:
            bad = out["Fare_Data"] == "no_class"
            hidden["class"] = int(bad.sum())
            out = out[~bad]
            hidden["unverified"] = int(out["Fare_Data"].isin(["pending", "error", "no_data"]).sum())
        if max_fare and max_fare > 0:
            loaded = out["Fare_Data"] != "pending"
            over = loaded & (out[fare_col].isna() | (out[fare_col] > max_fare))
            hidden["budget"] = int(over.sum())
            out = out[~over]

    if sort == "fare" and fare_col in out.columns:
        out = out.sort_values(fare_col, na_position="last", kind="stable")
    elif sort == "fastest" and "Total_Hrs" in out.columns:
        out = out.sort_values("Total_Hrs", kind="stable")
    elif sort == "wait" and "Layover_Hrs" in out.columns:
        out = out.sort_values("Layover_Hrs", kind="stable")
    elif sort == "duration" and "Duration_Hrs" in out.columns:
        out = out.sort_values("Duration_Hrs", kind="stable")
    return out.reset_index(drop=True), hidden


# =====================================================================================
# CSV EXPORT
# =====================================================================================
def csv_bytes(df: pd.DataFrame, extra: Optional[dict] = None) -> bytes:
    """
    UTF-8 with BOM so Excel shows ₹ and accents correctly.  Internal columns are dropped.
    `extra` adds constant columns at the front, e.g. {"Travel_Date": "2026-11-12"}.
    """
    out = df.drop(columns=["Fare_Data"], errors="ignore").copy()
    for col in FARE_COLUMNS + ["Fare", "Seat_Status"]:
        # Drop live-data columns that were never filled, instead of exporting empty columns.
        if col in out.columns:
            empty = out[col].isna().all() or (out[col].astype(str).str.strip() == "").all()
            if empty:
                out = out.drop(columns=[col])
    for i, (k, v) in enumerate((extra or {}).items()):
        out.insert(i, k, v)
    return out.to_csv(index=False).encode("utf-8-sig")


def csv_filename(prefix: str, start_code: str, end_code: str, d: Optional[datetime.date] = None) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", f"{start_code}-{end_code}")
    stamp = d.isoformat() if d else "anydate"
    return f"{prefix}_{safe}_{stamp}.csv"


# =====================================================================================
# "COPY AS TEXT"
# =====================================================================================
def _dur(hours: Any) -> str:
    try:
        total = int(round(float(hours) * 60))
    except (TypeError, ValueError):
        return ""
    h, m = divmod(abs(total), 60)
    return f"{h}h {m}m" if h and m else (f"{h}h" if h else f"{m}m")


def _money(n: Any) -> str:
    if n is None or (not isinstance(n, str) and pd.isna(n)):
        return ""
    try:
        return f"₹{int(n):,}"
    except (TypeError, ValueError):
        return ""


def _class_lines(classes, cls: str) -> list:
    lines = []
    for r in filter_classes(classes, cls):
        text = f"    {r.get('class') or '?'}: {r.get('status') or '—'}"
        if r.get("prediction"):
            text += f" ({r['prediction']})"
        fare = parse_fare(r.get("fare"))
        if fare:
            text += f" · {_money(fare)}"
        lines.append(text)
    return lines


_FOOTER = "Live seat/fare data from ConfirmTkt - confirm on IRCTC before booking."


def _row_labels(row: dict, start_label: str, end_label: str) -> tuple:
    """Label with the row's own stations when it came from a nearby-station search."""
    s, e = str(row.get("Start_Code") or "").strip(), str(row.get("End_Code") or "").strip()
    if s and e:
        return f"{_station_name(s)} ({s})", f"{_station_name(e)} ({e})"
    return start_label, end_label


def _station_name(code: str) -> str:
    from engine import CODE_TO_NAME  # lazy: keeps trip_tools importable without loading the CSVs

    return CODE_TO_NAME.get(code, code)


def connection_text(
    row: dict,
    start_label: str,
    end_label: str,
    travel_date: Optional[datetime.date] = None,
    quota: str = "GN",
    legs: Optional[dict] = None,
    cls: str = "Any",
) -> str:
    """Plain-text summary of one 2-train route, ready to paste into WhatsApp / notes."""
    off = int(row.get("Train2_Day_Offset", 0) or 0)
    d1 = travel_date
    d2 = travel_date + datetime.timedelta(days=off) if travel_date else None
    start_label, end_label = _row_labels(row, start_label, end_label)

    head = f"{start_label} → {end_label}"
    if travel_date:
        head += f"  |  {travel_date.strftime('%a %d %b %Y')}"
    lines = [
        head,
        f"Change at {row['Via_Station']} ({row['Via_Code']}) · wait {_dur(row['Layover_Hrs'])}"
        f" · total {_dur(row['Total_Hrs'])}",
        "",
        f"Train 1: {row['Train_1_No']} {row['Train_1_Name']}",
        f"  {row['Leave_Start']} {start_label} → {row['Arrive_Mid']} {row['Via_Station']}"
        f" ({_dur(row['Leg1_Hrs'])})"
        + (f" · {d1.strftime('%a %d %b')}" if d1 else "")
        + f" · runs {row['Train_1_Running_Days']}",
    ]
    if legs and (legs.get("leg1") or {}).get("classes"):
        lines += _class_lines(legs["leg1"]["classes"], cls)
    lines += [
        f"Train 2: {row['Train_2_No']} {row['Train_2_Name']}",
        f"  {row['Leave_Mid']} {row['Via_Station']} → {row['Arrive_End']} {end_label}"
        f" ({_dur(row['Leg2_Hrs'])})"
        + (f" · {d2.strftime('%a %d %b')}" if d2 else "")
        + f" · runs {row['Train_2_Running_Days']}",
    ]
    if legs and (legs.get("leg2") or {}).get("classes"):
        lines += _class_lines(legs["leg2"]["classes"], cls)

    total = row.get("Total_Fare")
    if total is not None and not pd.isna(total):
        label = row.get("Fare_Class") or "cheapest"
        lines += [
            "",
            f"Total fare ({label}, {quota}): {_money(total)}"
            f"  ({_money(row.get('Fare_Train_1'))} + {_money(row.get('Fare_Train_2'))})",
        ]
    lines += ["", _FOOTER]
    return "\n".join(lines)


def direct_text(
    row: dict,
    start_label: str,
    end_label: str,
    quota: str = "GN",
    seat: Optional[dict] = None,
    cls: str = "Any",
) -> str:
    """Plain-text summary of one direct train."""
    start_label, end_label = _row_labels(row, start_label, end_label)
    head = f"{start_label} → {end_label}"
    if row.get("Board_On"):
        head += f"  |  {row['Board_On']}"
    lines = [
        head,
        f"Direct: {row['Train_No']} {row['Train_Name']}",
        f"  {row['Departure']} → {row['Arrival']} ({_dur(row['Duration_Hrs'])})"
        f" · {int(row.get('Stops_Between', 0) or 0)} stops between"
        f" · runs {row.get('Running_Days', '')}",
    ]
    if seat and seat.get("classes"):
        lines += _class_lines(seat["classes"], cls)
    fare = row.get("Fare")
    if fare is not None and not pd.isna(fare) and not (seat and seat.get("classes")):
        lines.append(f"    {row.get('Fare_Class') or ''}: {_money(fare)} ({quota})")
    lines += ["", _FOOTER]
    return "\n".join(lines)


def routes_overview_text(
    rows: list, start_label: str, end_label: str, travel_date: Optional[datetime.date], limit: int = 5
) -> str:
    """Short numbered list of the top routes (one line each) - the 'share my options' text."""
    head = f"{start_label} → {end_label}"
    if travel_date:
        head += f"  |  {travel_date.strftime('%a %d %b %Y')}"
    lines = [head, ""]
    for n, row in enumerate(rows[:limit], 1):
        s, e = row_codes(row, "", "")
        ends = f"[{s}→{e}] " if s and e else ""
        line = (
            f"{n}. {ends}via {row['Via_Station']}: {row['Train_1_No']} ({row['Leave_Start']}→{row['Arrive_Mid']})"
            f" + {row['Train_2_No']} ({row['Leave_Mid']}→{row['Arrive_End']})"
            f" · {_dur(row['Total_Hrs'])} · wait {_dur(row['Layover_Hrs'])}"
        )
        total = row.get("Total_Fare")
        if total is not None and not pd.isna(total):
            line += f" · {_money(total)} ({row.get('Fare_Class') or ''})"
        lines.append(line)
    lines += ["", _FOOTER]
    return "\n".join(lines)
