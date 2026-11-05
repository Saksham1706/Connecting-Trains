"""Run with:  python -m pytest -q   (or just: python test_trip_tools.py)"""

import datetime
import io

import pandas as pd

import trip_tools as tt

D = datetime.date(2026, 11, 12)


def cls(c, fare, status="AVAILABLE-0010"):
    return {"class": c, "status": status, "prediction": "", "chance": "", "fare": fare}


def route(*trains, ok=True):
    return {"ok": ok, "error": "" if ok else "boom", "trains": [
        {"train_number": n, "train_name": n, "classes": c} for n, c in trains]}


def conn_row(**kw):
    base = dict(
        Via_Station="NAGPUR", Via_Code="NGP", Train_1_No="111", Train_1_Name="ONE EXP",
        Leave_Start="08:00", Arrive_Mid="12:00", Leg1_Hrs=4.0,
        Train_2_No="222", Train_2_Name="TWO EXP", Leave_Mid="14:00", Arrive_End="20:00", Leg2_Hrs=6.0,
        Layover_Hrs=2.0, Total_Hrs=12.0, Comfort="Comfortable", Overnight_Layover="No",
        Train_1_Running_Days="Daily", Train_2_Running_Days="Daily", Train2_Day_Offset=0)
    base.update(kw)
    return base


# ---------------------------------------------------------------- fares
def test_parse_fare():
    assert tt.parse_fare("1150") == 1150
    assert tt.parse_fare("₹1,150") == 1150
    assert tt.parse_fare("1150.0") == 1150
    assert tt.parse_fare("") is None
    assert tt.parse_fare(None) is None
    assert tt.parse_fare("0") is None
    assert tt.parse_fare("N/A") is None


def test_filter_classes():
    rows = [cls("SL", "300"), cls("3A", "800"), cls("sl ", "300")]
    assert [r["class"] for r in tt.filter_classes(rows, "SL")] == ["SL", "sl "]
    assert len(tt.filter_classes(rows, "Any")) == 3
    assert tt.filter_classes(rows, "CC") == []
    assert tt.filter_classes(None, "SL") == []


def test_pair_specific_class_needs_both_trains():
    a = [cls("SL", "300"), cls("3A", "800")]
    b = [cls("3A", "900")]
    assert tt.pick_fare_pair(a, b, "SL") is None          # train 2 has no SL
    p = tt.pick_fare_pair(a, b, "3A")
    assert (p["fare1"], p["fare2"], p["total"]) == (800, 900, 1700)


def test_pair_any_picks_cheapest_common_class():
    a = [cls("SL", "300"), cls("3A", "800"), cls("1A", "3000")]
    b = [cls("3A", "900"), cls("1A", "3500"), cls("SL", "350")]
    p = tt.pick_fare_pair(a, b, "Any")
    assert (p["cls1"], p["cls2"], p["total"]) == ("SL", "SL", 650)


def test_pair_any_mixed_when_no_common_class():
    a = [cls("SL", "300")]
    b = [cls("CC", "700")]
    p = tt.pick_fare_pair(a, b, "Any")
    assert (p["cls1"], p["cls2"], p["total"]) == ("SL", "CC", 1000)


def test_pair_missing_fare_gives_none_total():
    p = tt.pick_fare_pair([cls("SL", "")], [cls("SL", "300")], "SL")
    assert p["total"] is None and p["fare2"] == 300
    assert tt.pick_fare_pair([], [], "Any") is None


def test_single_leg_fare():
    rows = [cls("SL", "300"), cls("3A", "800")]
    assert tt.single_leg_fare(rows, "Any")["cls"] == "SL"
    assert tt.single_leg_fare(rows, "3A")["fare"] == 800
    assert tt.single_leg_fare(rows, "CC") is None


# ---------------------------------------------------------------- annotate
def build():
    df = pd.DataFrame([
        conn_row(),                                                         # SL+3A both
        conn_row(Via_Code="BPL", Via_Station="BHOPAL", Train_1_No="333", Train_2_No="444"),   # no SL on 444
        conn_row(Via_Code="JBP", Via_Station="JABALPUR", Train_1_No="555", Train_2_No="666"),  # 666 missing live
        conn_row(Via_Code="ET", Via_Station="ITARSI", Train_1_No="777", Train_2_No="888"),     # not fetched
        conn_row(Via_Code="SC", Via_Station="SECUNDERABAD", Train_1_No="999", Train_2_No="101", Train2_Day_Offset=1),
    ])
    routes = {
        tt.route_key("AAA", "NGP", D, "GN"): route(("111", [cls("SL", "300"), cls("3A", "800")])),
        tt.route_key("NGP", "BBB", D, "GN"): route(("222", [cls("SL", "350"), cls("3A", "900")])),
        tt.route_key("AAA", "BPL", D, "GN"): route(("333", [cls("SL", "200"), cls("3A", "500")])),
        tt.route_key("BPL", "BBB", D, "GN"): route(("444", [cls("3A", "600")])),
        tt.route_key("AAA", "JBP", D, "GN"): route(("555", [cls("SL", "250")])),
        tt.route_key("JBP", "BBB", D, "GN"): route(("someone-else", [cls("SL", "1")])),
        tt.route_key("AAA", "SC", D, "GN"): route(("999", [cls("SL", "400")])),
        tt.route_key("SC", "BBB", D + datetime.timedelta(days=1), "GN"): route(("101", [cls("SL", "450")])),
    }
    return df, routes


def test_annotate_states_and_totals():
    df, routes = build()
    out = tt.annotate_connections(df, routes, "AAA", "BBB", D, "GN", "Any")
    assert list(out["Fare_Data"]) == ["ok", "ok", "no_data", "pending", "ok"]
    assert out.loc[0, "Total_Fare"] == 650 and out.loc[0, "Fare_Class"] == "SL"
    assert out.loc[1, "Total_Fare"] == 1100 and out.loc[1, "Fare_Class"] == "3A"   # only common class
    assert pd.isna(out.loc[2, "Total_Fare"]) and pd.isna(out.loc[3, "Total_Fare"])
    assert out.loc[4, "Total_Fare"] == 850          # used the day+1 lookup for train 2


def test_annotate_class_filter_marks_no_class():
    df, routes = build()
    out = tt.annotate_connections(df, routes, "AAA", "BBB", D, "GN", "SL")
    assert list(out["Fare_Data"]) == ["ok", "no_class", "no_data", "pending", "ok"]
    assert out.loc[0, "Total_Fare"] == 650


def test_closed_trains_are_never_ranked_or_priced():
    # Found with live data: the "cheapest" route was a train that had already left.
    df, routes = build()
    routes[tt.route_key("AAA", "NGP", D, "GN")] = route(
        ("111", [cls("SL", "1", "Train Departed (No more booking)"), cls("3A", "1", "Train Departed")]))
    out = tt.annotate_connections(df, routes, "AAA", "BBB", D, "GN", "Any")
    assert out.loc[0, "Fare_Data"] == "closed" and pd.isna(out.loc[0, "Total_Fare"])
    view, hidden = tt.filter_and_sort(out, sort="fare")
    assert "NGP" not in set(view["Via_Code"]) and hidden["closed"] == 1
    assert view.iloc[0]["Via_Code"] != "NGP"
    d = pd.DataFrame([{"Train_No": "111", "Train_Name": "A", "Board_Date": D.isoformat(), "Departure": "08:00",
                       "Arrival": "20:00", "Duration_Hrs": 12.0}])
    r = {tt.route_key("A", "B", D, "GN"): route(("111", [cls("SL", "300", "Train Departed")]))}
    assert tt.annotate_directs(d, r, "A", "B", "GN", "Any").loc[0, "Fare_Data"] == "closed"
    assert tt.is_closed([cls("SL", "1", "TRAIN DEPARTED")]) and not tt.is_closed([cls("SL", "1", "WL 5")])


def live_row(c, fare, status, prediction):
    return {"class": c, "status": status, "prediction": prediction, "chance": "", "fare": fare}


def closed_row(c, fare):
    # exactly what ConfirmTkt returns for a train that has left
    return live_row(c, fare, "Train Departed", "No more booking")


def test_only_departed_or_charted_is_closed():
    assert tt.is_closed_row(closed_row("3A", "700"))
    assert tt.is_closed_row(cls("SL", "275", "Charting Done"))
    assert not tt.is_closed_row(cls("SL", "275", "WL 12"))
    assert tt.is_closed([closed_row("3A", "700"), closed_row("SL", "275")])
    assert not tt.is_closed([closed_row("3A", "700"), cls("SL", "275", "WL 3")])   # one open class -> train open
    assert not tt.is_closed([])


def test_regression_sold_out_rows_are_not_hidden():
    # Real ConfirmTkt combos seen live: prediction 'No more booking' ALSO accompanies Regret /
    # Not Available. Treating that text as "closed" hid 70 of 78 routes. They must stay visible.
    regret = live_row("3A", "1650", "Regret", "No more booking")
    na = live_row("SL", "275", "Not Available", "No More Booking")
    assert not tt.is_closed_row(regret) and not tt.is_closed_row(na)
    assert not tt.is_closed([regret, na])
    df, routes = build()
    routes[tt.route_key("AAA", "NGP", D, "GN")] = route(("111", [regret]))
    out = tt.annotate_connections(df, routes, "AAA", "BBB", D, "GN", "Any")
    assert out.loc[0, "Fare_Data"] == "ok" and out.loc[0, "Seat_Train_1"] == "Regret"


def test_closed_class_cannot_win_cheapest_but_open_ones_can():
    df, routes = build()
    # train 111: SL is closed (cheap, 100) but 3A is open (800) -> must quote 3A, not the closed SL
    routes[tt.route_key("AAA", "NGP", D, "GN")] = route(("111", [closed_row("SL", "100"), cls("3A", "800")]))
    out = tt.annotate_connections(df, routes, "AAA", "BBB", D, "GN", "Any")
    assert out.loc[0, "Fare_Data"] == "ok" and out.loc[0, "Fare_Class"] == "3A" and out.loc[0, "Total_Fare"] == 1700
    # asking for the closed class specifically -> not offered
    out = tt.annotate_connections(df, routes, "AAA", "BBB", D, "GN", "SL")
    assert out.loc[0, "Fare_Data"] == "no_class"


def test_api_error_is_not_confused_with_no_seats():
    df, routes = build()
    routes[tt.route_key("AAA", "NGP", D, "GN")] = route(ok=False)
    out = tt.annotate_connections(df, routes, "AAA", "BBB", D, "GN", "SL")
    assert out.loc[0, "Fare_Data"] == "error"


def test_no_date_stays_pending():
    df, routes = build()
    out = tt.annotate_connections(df, routes, "AAA", "BBB", None, "GN", "Any")
    assert set(out["Fare_Data"]) == {"pending"}


def test_filter_and_sort_class_keeps_unverified():
    df, routes = build()
    out = tt.annotate_connections(df, routes, "AAA", "BBB", D, "GN", "SL")
    view, hidden = tt.filter_and_sort(out, cls="SL", sort="fare")
    assert hidden["class"] == 1                     # BHOPAL (no SL on train 2)
    assert hidden["unverified"] == 2                # JABALPUR (no live data) + ITARSI (pending)
    assert list(view["Via_Code"])[:2] == ["NGP", "SC"]   # cheapest first, unpriced last
    assert view["Total_Fare"].iloc[-1] is pd.NA or pd.isna(view["Total_Fare"].iloc[-1])


def test_filter_and_sort_budget():
    df, routes = build()
    out = tt.annotate_connections(df, routes, "AAA", "BBB", D, "GN", "Any")
    view, hidden = tt.filter_and_sort(out, max_fare=700, sort="fare")
    assert set(view.loc[view["Fare_Data"] == "ok", "Via_Code"]) == {"NGP"}
    assert hidden["budget"] == 3        # BPL 1100, SC 850, JBP no_data (no fare) -> hidden
    assert "ET" in set(view["Via_Code"])  # pending rows are never hidden by budget


def test_sort_modes():
    df, _ = build()
    df["Total_Hrs"] = [5.0, 3.0, 9.0, 1.0, 7.0]
    df["Layover_Hrs"] = [5.0, 3.0, 9.0, 1.0, 7.0][::-1]
    df["Fare_Data"] = "pending"
    assert list(tt.filter_and_sort(df, sort="fastest")[0]["Total_Hrs"]) == [1.0, 3.0, 5.0, 7.0, 9.0]
    assert list(tt.filter_and_sort(df, sort="wait")[0]["Layover_Hrs"]) == sorted(df["Layover_Hrs"])
    assert list(tt.filter_and_sort(df, sort="search")[0]["Total_Hrs"]) == [5.0, 3.0, 9.0, 1.0, 7.0]


def test_directs():
    df = pd.DataFrame([
        {"Train_No": "111", "Train_Name": "A", "Board_Date": D.isoformat(), "Departure": "08:00",
         "Arrival": "20:00", "Duration_Hrs": 12.0},
        {"Train_No": "222", "Train_Name": "B", "Board_Date": D.isoformat(), "Departure": "09:00",
         "Arrival": "21:00", "Duration_Hrs": 12.0},
        {"Train_No": "333", "Train_Name": "C", "Board_Date": "", "Departure": "10:00",
         "Arrival": "22:00", "Duration_Hrs": 12.0},
    ])
    routes = {tt.route_key("A", "B", D, "GN"): route(("111", [cls("SL", "300"), cls("CC", "700")]),
                                                      ("222", [cls("3A", "900")]))}
    out = tt.annotate_directs(df, routes, "A", "B", "GN", "Any")
    assert list(out["Fare_Data"]) == ["ok", "ok", "pending"]
    assert out.loc[0, "Fare"] == 300 and out.loc[0, "Fare_Class"] == "SL"
    out = tt.annotate_directs(df, routes, "A", "B", "GN", "SL")
    assert list(out["Fare_Data"]) == ["ok", "no_class", "pending"]
    view, hidden = tt.filter_and_sort(out, cls="SL", sort="fare", fare_col="Fare")
    assert list(view["Train_No"]) == ["111", "333"] and hidden["class"] == 1


# ---------------------------------------------------------------- csv
def test_csv_roundtrip_and_cleanup():
    df, routes = build()
    out = tt.annotate_connections(df, routes, "AAA", "BBB", D, "GN", "Any")
    raw = tt.csv_bytes(out, {"Travel_Date": D.isoformat()})
    assert raw.startswith(b"\xef\xbb\xbf")                    # BOM for Excel
    back = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig")
    assert "Fare_Data" not in back.columns
    assert back.columns[0] == "Travel_Date" and len(back) == 5
    assert back.loc[0, "Total_Fare"] == 650
    # fare columns never filled -> dropped rather than exported empty
    plain = tt.csv_bytes(tt.annotate_connections(df, {}, "AAA", "BBB", None, "GN", "Any"))
    assert "Total_Fare" not in pd.read_csv(io.BytesIO(plain), encoding="utf-8-sig").columns


def test_csv_special_characters_are_quoted():
    df = pd.DataFrame([{"Train_Name": 'A, "B" EXP', "x": 1}])
    back = pd.read_csv(io.BytesIO(tt.csv_bytes(df)), encoding="utf-8-sig")
    assert back.loc[0, "Train_Name"] == 'A, "B" EXP'


def test_csv_filename():
    assert tt.csv_filename("connections", "PUNE", "NDLS", D) == "connections_PUNE-NDLS_2026-11-12.csv"
    assert tt.csv_filename("x", "A/B", "C D", None) == "x_AB-CD_anydate.csv"


# ---------------------------------------------------------------- text
def test_connection_text_full():
    df, routes = build()
    out = tt.annotate_connections(df, routes, "AAA", "BBB", D, "GN", "Any")
    row = out.iloc[0].to_dict()
    legs = {"leg1": {"classes": [cls("SL", "300"), cls("3A", "800")]},
            "leg2": {"classes": [cls("SL", "350")]}}
    txt = tt.connection_text(row, "PUNE JN (PUNE)", "NEW DELHI (NDLS)", D, "GN", legs, "SL")
    assert "PUNE JN (PUNE) → NEW DELHI (NDLS)  |  Thu 12 Nov 2026" in txt
    assert "Change at NAGPUR (NGP)" in txt and "Train 1: 111 ONE EXP" in txt
    assert "SL: AVAILABLE-0010 · ₹300" in txt and "3A:" not in txt      # class filter applied
    assert "Total fare (SL, GN): ₹650  (₹300 + ₹350)" in txt
    assert "confirm on IRCTC" in txt


def test_connection_text_day_offset_and_no_live_data():
    row = conn_row(Train2_Day_Offset=1, Total_Fare=pd.NA)
    txt = tt.connection_text(row, "A", "B", D, "GN")
    assert "Fri 13 Nov" in txt and "Thu 12 Nov" in txt
    assert "Total fare" not in txt and "nan" not in txt.lower() and "<NA>" not in txt
    assert "Thu 12 Nov" not in tt.connection_text(row, "A", "B", None, "GN")


def test_direct_and_overview_text():
    row = {"Train_No": "111", "Train_Name": "A EXP", "Departure": "08:00", "Arrival": "20:00",
           "Duration_Hrs": 12.0, "Stops_Between": 3, "Running_Days": "Daily", "Board_On": "Thu 12 Nov 2026",
           "Fare": 300, "Fare_Class": "SL"}
    txt = tt.direct_text(row, "A", "B", "GN")
    assert "Direct: 111 A EXP" in txt and "SL: ₹300 (GN)" in txt and "12h" in txt
    rows = [conn_row(Total_Fare=650, Fare_Class="SL"), conn_row(Total_Fare=pd.NA)]
    ov = tt.routes_overview_text(rows, "A", "B", D, limit=5)
    assert "1. via NAGPUR" in ov and "₹650 (SL)" in ov and "2. via NAGPUR" in ov
    assert ov.count("₹") == 1


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as e:  # noqa: BLE001
                fails += 1
                import traceback; print("FAIL", name, "->", repr(e)); traceback.print_exc()
    sys.exit(1 if fails else 0)
