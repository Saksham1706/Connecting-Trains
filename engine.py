import os
import re
import datetime
from collections import Counter, defaultdict

import pandas as pd

# Always load the CSVs that sit next to this file, no matter where the app is started from.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _data_path(filename: str) -> str:
    return os.path.join(_BASE_DIR, filename)


# =====================================================================================
# DATA FILES
# =====================================================================================
# stations.csv                  -> station_code, station_name
# train_schedule_scappped.csv   -> train_number, train_name, day, station_name,
#                                   station_code (often BLANK), arrival, departure
# running_days_scrapped.csv     -> train_number, train_name, source, destination,
#                                   running_days  (7-digit string, Mon..Sun,
#                                   '1' = runs that day. Leading zeros get eaten if
#                                   the column is read as a number, e.g. "0000010"
#                                   becomes "10" -> we zfill(7) it back.)
# =====================================================================================

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]  # index 0 = Monday

# Booking tags sometimes get scraped into the station name (not real stations).
_BOOKING_TAG_RE = re.compile(r"\s*\((?:PQ|RL)\)\s*", re.IGNORECASE)


def clean_station_name(name: str) -> str:
    """
    Strip scraped booking tags like (PQ)/(RL) and tidy whitespace.
    'Jodhpur Jn (PQ)' -> 'Jodhpur Jn'
    """
    if name is None:
        return ""
    s = str(name).strip()
    # Remove tags repeatedly in case of odd spacing
    while True:
        cleaned = _BOOKING_TAG_RE.sub(" ", s)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned == s:
            break
        s = cleaned
    return s


def normalize_name(name: str) -> str:
    """Uppercase + collapse whitespace so station names from different files can be matched."""
    if name is None:
        return ""
    return re.sub(r"\s+", " ", clean_station_name(name).upper())


def _loose_key(name: str) -> str:
    """'Barddhaman Jn' / 'BARDDHAMAN' -> 'BARDDHAMAN' (suffixes + punctuation removed)."""
    n = re.sub(r"\b(JN|JUNCTION|CANTT|CANT|CNTT)\b\.?", " ", normalize_name(name))
    return re.sub(r"[^A-Z0-9]", "", n)


def _load_stations():
    """Build a NAME -> CODE lookup from stations.csv."""
    stations_df = pd.read_csv(_data_path("stations.csv"), dtype=str)
    stations_df.columns = [c.strip() for c in stations_df.columns]
    stations_df["station_code"] = stations_df["station_code"].fillna("").str.strip().str.upper()
    stations_df["station_name"] = stations_df["station_name"].fillna("").str.strip().map(clean_station_name)
    stations_df["norm_name"] = stations_df["station_name"].apply(normalize_name)

    # Keep first code seen per normalized name (in case of duplicate rows).
    name_to_code = (
        stations_df.drop_duplicates(subset="norm_name", keep="first")
        .set_index("norm_name")["station_code"]
        .to_dict()
    )
    code_to_name = (
        stations_df.drop_duplicates(subset="station_code", keep="first")
        .set_index("station_code")["station_name"]
        .to_dict()
    )
    used_codes = set(stations_df["station_code"].dropna().unique())

    # Looser lookup: ignore JN / CANTT suffixes and punctuation. Only unambiguous keys are kept.
    key_to_codes = defaultdict(set)
    for code, name in zip(stations_df["station_code"], stations_df["station_name"]):
        if code and name:
            key_to_codes[_loose_key(name)].add(code)
    key_to_code = {k: next(iter(v)) for k, v in key_to_codes.items() if k and len(v) == 1}
    return name_to_code, code_to_name, used_codes, key_to_code


NAME_TO_CODE, CODE_TO_NAME, _USED_CODES, _KEY_TO_CODE = _load_stations()
_FALLBACK_CACHE = {}  # norm_name -> generated code, so the same unmatched name always
                       # resolves to the SAME synthetic code (needed for intersections to work)


def _make_fallback_code(name: str) -> str:
    """
    If a station name in the schedule file can't be found in stations.csv, invent a
    stable code for it so trains stopping at the "same" unmatched station can still
    be recognised as a valid interchange point.
    """
    base = re.sub(r"[^A-Z]", "", normalize_name(name))[:4] or "STN"
    code = base
    i = 1
    while code in _USED_CODES:
        i += 1
        code = f"{base}{i}"
    _USED_CODES.add(code)
    return code


def _lookup_code_by_name(station_name: str) -> str:
    """Match cleaned/normalized name against the master station list."""
    norm = normalize_name(station_name)
    if not norm:
        return ""
    code = NAME_TO_CODE.get(norm, "")
    if code:
        return code
    # Light fuzzy: treat JN / JUNCTION the same
    alt = norm.replace(" JUNCTION", " JN").replace(" JN.", " JN")
    if alt != norm:
        code = NAME_TO_CODE.get(alt, "")
        if code:
            return code
    return ""


# Names that stations.csv doesn't know, learned from the schedule file itself
# (see _merge_duplicate_blocks). norm_name -> station_code
_LEARNED_NAME_TO_CODE = {}


def _resolve_station_code(row):
    """
    Prefer the official code from stations.csv (by cleaned name).
    Only keep a scraped code if we cannot match the name.
    """
    cleaned_name = clean_station_name(row.get("station_name") or "")
    by_name = _lookup_code_by_name(cleaned_name)
    if by_name:
        return by_name

    existing = (row.get("station_code") or "").strip().upper()
    if existing:
        return existing

    norm = normalize_name(cleaned_name)
    learned = _LEARNED_NAME_TO_CODE.get(norm)
    if learned:
        return learned
    loose = _KEY_TO_CODE.get(_loose_key(cleaned_name))
    if loose:
        return loose

    if norm not in _FALLBACK_CACHE:
        _FALLBACK_CACHE[norm] = _make_fallback_code(cleaned_name or "STN")
    return _FALLBACK_CACHE[norm]


def _display_station_name(code: str, fallback_name: str = "") -> str:
    """One clean display name per station code (prefer stations.csv)."""
    official = CODE_TO_NAME.get(code, "")
    name = clean_station_name(official or fallback_name)
    return name


def _build_station_list(schedule_df) -> list:
    """
    One dropdown entry per station code.
    Avoids duplicates from casing / (PQ) / (RL) name variants.
    """
    best = {}  # code -> display name
    for code, name in zip(schedule_df["station_code"], schedule_df["station_name"]):
        code = (code or "").strip().upper()
        if not code:
            continue
        display = _display_station_name(code, name)
        if not display:
            continue
        # Prefer official master name when available
        if code not in best or code in CODE_TO_NAME:
            best[code] = _display_station_name(code, display)

    return sorted(
        f"{name} ({code})"
        for code, name in best.items()
        if name and code
    )


def _merge_duplicate_blocks(df):
    """
    213 trains appear TWICE in the scraped CSV (once with blank station codes, once with codes
    but sometimes missing stops). Loading both made those trains loop over the same stations and
    corrupted stop order. Here the two blocks are merged into ONE stop list:
      * stops are matched by (arrival, departure),
      * stops that exist in only one block are kept, in route order,
      * codes from the coded block are copied onto the matching uncoded rows,
      * name -> code pairs learned this way are remembered for names stations.csv doesn't know.
    """
    df = df.reset_index(drop=True)
    block_id = (df["train_number"] != df["train_number"].shift()).cumsum()
    df["_block"] = block_id
    blocks_per_train = df.groupby("train_number")["_block"].nunique()
    dup_trains = set(blocks_per_train[blocks_per_train > 1].index)
    if not dup_trains:
        return df.drop(columns="_block")

    keep_parts = [df[~df["train_number"].isin(dup_trains)]]
    cols = [c for c in df.columns if c != "_block"]
    learned = defaultdict(Counter)

    for tn, g in df[df["train_number"].isin(dup_trains)].groupby("train_number", sort=False):
        blocks = [b[cols].to_dict("records") for _, b in g.groupby("_block", sort=True)]
        merged = list(blocks[0])
        for extra in blocks[1:]:
            pos = {}
            for i, r in enumerate(merged):
                pos.setdefault((r["arrival"], r["departure"]), i)
            last = -1
            for r in extra:
                key = (r["arrival"], r["departure"])
                i = pos.get(key)
                if i is not None:
                    base = merged[i]
                    code_new = (r.get("station_code") or "").strip()
                    code_old = (base.get("station_code") or "").strip()
                    if code_new and not code_old:
                        base["station_code"] = code_new
                        learned[normalize_name(base["station_name"])][code_new.upper()] += 1
                    elif code_old and not code_new:
                        learned[normalize_name(r["station_name"])][code_old.upper()] += 1
                    last = i
                else:
                    merged.insert(last + 1, dict(r))
                    last += 1
                    # indexes after the insertion point moved by one
                    pos = {}
                    for j, m in enumerate(merged):
                        pos.setdefault((m["arrival"], m["departure"]), j)
        keep_parts.append(pd.DataFrame(merged, columns=cols))

    for norm, counts in learned.items():
        if norm:
            _LEARNED_NAME_TO_CODE.setdefault(norm, counts.most_common(1)[0][0])

    out = pd.concat([p.drop(columns="_block", errors="ignore") for p in keep_parts], ignore_index=True)
    return out


def _compute_absolute_times(df):
    """
    Build abs_arrival / abs_departure (timedelta from day 1, 00:00 of the train's origin day).

    The scraped `day` column sometimes doesn't tick over at midnight (e.g. 23:59 -> 00:18 both
    on "day 1"), and the origin/terminal rows carry a placeholder 00:00:00. So times are walked
    stop by stop and forced to never go backwards. `day` is then rewritten as the journey day on
    which the train DEPARTS that stop (what the running-days logic needs).
    """
    ONE_DAY = 24 * 3600
    arr_s = pd.to_timedelta(df["arrival"]).dt.total_seconds().to_numpy()
    dep_s = pd.to_timedelta(df["departure"]).dt.total_seconds().to_numpy()
    day0 = (df["day"].to_numpy() - 1) * ONE_DAY
    trains = df["train_number"].to_numpy()

    n = len(df)
    abs_arr = [0.0] * n
    abs_dep = [0.0] * n
    start = 0
    while start < n:
        end = start
        while end + 1 < n and trains[end + 1] == trains[start]:
            end += 1
        prev = 0.0
        for i in range(start, end + 1):
            if i == start:  # origin: arrival is a placeholder
                a = d = day0[i] + dep_s[i]
            elif i == end:  # terminus: departure is a placeholder
                a = day0[i] + arr_s[i]
                while a < prev:
                    a += ONE_DAY
                d = a
            else:
                a = day0[i] + arr_s[i]
                while a < prev:
                    a += ONE_DAY
                d = day0[i] + dep_s[i]
                while d < a:
                    d += ONE_DAY
            abs_arr[i], abs_dep[i] = a, d
            prev = d
        start = end + 1

    df["abs_arrival"] = pd.to_timedelta(abs_arr, unit="s")
    df["abs_departure"] = pd.to_timedelta(abs_dep, unit="s")
    df["day"] = (df["abs_departure"] // pd.Timedelta(days=1)).astype(int) + 1
    return df


def _load_schedule():
    df = pd.read_csv(_data_path("train_schedule_scrapped.csv"), dtype=str)
    df.columns = [c.strip() for c in df.columns]

    for col in ["train_number", "train_name", "station_name", "station_code", "arrival", "departure"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")

    df["train_number"] = df["train_number"].str.strip()
    # Clean names first so (PQ)/(RL)/casing variants map to the same station
    df["station_name"] = df["station_name"].map(clean_station_name)
    df["arrival"] = df["arrival"].replace("", "00:00:00")
    df["departure"] = df["departure"].replace("", "00:00:00")
    df["day"] = pd.to_numeric(df["day"], errors="coerce").fillna(1).astype(int)

    # Trains that were scraped twice -> one clean stop list (also teaches name -> code pairs)
    df = _merge_duplicate_blocks(df)

    # Resolve/repair station codes (many rows arrive with a blank station_code).
    df["station_code"] = df.apply(_resolve_station_code, axis=1)
    # Canonical display name from master list when possible
    df["station_name"] = [
        _display_station_name(code, name)
        for code, name in zip(df["station_code"], df["station_name"])
    ]

    # A few merged trains still list the same station twice (the two scrapes differ by a minute
    # or two). A train only stops at a station once per run, so keep the first occurrence.
    df = df.drop_duplicates(subset=["train_number", "station_code"], keep="first").reset_index(drop=True)

    df["stop_seq"] = df.groupby("train_number").cumcount()
    return _compute_absolute_times(df)


def _load_running_days():
    rd = pd.read_csv(_data_path("running_days_scrapped.csv"), dtype=str)
    rd.columns = [c.strip() for c in rd.columns]
    for col in ["train_number", "train_name", "source", "destination", "running_days"]:
        if col not in rd.columns:
            rd[col] = ""
        rd[col] = rd[col].fillna("")

    rd["train_number"] = rd["train_number"].str.strip()

    def fix_days(val):
        val = re.sub(r"[^01]", "", str(val))  # keep only 0/1 chars
        if not val:
            return ""  # unknown
        return val[-7:].zfill(7)  # restore eaten leading zeros, cap at 7 chars

    rd["running_days"] = rd["running_days"].apply(fix_days)
    return rd


# --- Load everything once (module-level, so it's cached for the life of the process) ---
df2 = _load_schedule()
_running_days_df = _load_running_days()

RUNNING_DAYS_MAP = dict(zip(_running_days_df["train_number"], _running_days_df["running_days"]))
TRAIN_META = _running_days_df.set_index("train_number")[["train_name", "source", "destination"]].to_dict("index")

# Dropdown list: one entry per station code — "STATION NAME (CODE)"
station_list = _build_station_list(df2)
# Searchable index of trains: (train_number, train_name) for the "lookup by train number" feature.
_first_rows = df2.drop_duplicates(subset="train_number")[["train_number", "train_name"]]
TRAIN_INDEX = sorted(
    {
        (row.train_number, (row.train_name or TRAIN_META.get(row.train_number, {}).get("train_name", "")).strip())
        for row in _first_rows.itertuples()
        if row.train_number
    },
    key=lambda x: x[0],
)


# =====================================================================================
# RUNNING-DAYS HELPERS
# =====================================================================================
def running_days_text(train_number: str) -> str:
    code = RUNNING_DAYS_MAP.get(str(train_number), "")
    if not code:
        return "Unknown"
    active = [WEEKDAYS[i] for i, c in enumerate(code) if c == "1"]
    if len(active) == 7:
        return "Daily"
    if not active:
        return "Unknown"
    return ", ".join(active)


def train_runs_on(train_number: str, weekday_idx: int) -> bool:
    """
    True if the train runs on the given weekday (0=Mon..6=Sun).
    If we have no running-day data for this train, we ASSUME it runs (so we don't
    silently hide valid connections just because the days file didn't cover it).
    """
    code = RUNNING_DAYS_MAP.get(str(train_number), "")
    if not code:
        return True
    return code[weekday_idx % 7] == "1"


def origin_weekday_for_boarding(boarding_weekday: int, schedule_day: int) -> int:
    """
    Running-days apply to the train's ORIGIN departure day.

    schedule_day is the CSV journey day at the boarding station (1 = origin day).
    Example: train leaves Chennai Sat (origin), Badnera is day 2 → boarding Sun
    means origin weekday = Sun - 1 = Sat.
    """
    offset = max(int(schedule_day or 1) - 1, 0)
    return (int(boarding_weekday) - offset) % 7


def train_runs_on_boarding_date(train_number: str, boarding_date, schedule_day: int) -> bool:
    """True if boarding on boarding_date at a stop on schedule_day is valid."""
    if boarding_date is None:
        return True
    origin_wd = origin_weekday_for_boarding(boarding_date.weekday(), schedule_day)
    return train_runs_on(train_number, origin_wd)


# =====================================================================================
# FLEXIBLE TRAVEL DATES
# =====================================================================================
def flexible_dates(search_date, days_before=3, days_after=3, today=None) -> list:
    """Dates to try around search_date: [-days_before .. +days_after], never earlier than today.

    Today 12th, travel 13th -> 12, 13, 14, 15, 16.  Travel today -> today .. today+3.
    Travel 20th -> 17 .. 23.
    """
    if search_date is None:
        return []
    today = today or datetime.date.today()
    low = max(search_date - datetime.timedelta(days=max(0, int(days_before))), today)
    high = search_date + datetime.timedelta(days=max(0, int(days_after)))
    if high < low:
        return [search_date]
    return [low + datetime.timedelta(days=i) for i in range((high - low).days + 1)]


# =====================================================================================
# TRAIN TYPES (Rajdhani, Vande Bharat, ...)
# =====================================================================================
# train_types.csv: train_number, category. Built from the per-category train lists plus name
# matches for trains those lists miss. One train can have several categories (e.g. Tejas Rajdhani).
TRAIN_TYPE_ORDER = [
    "Rajdhani", "Vande Bharat", "Shatabdi", "Jan Shatabdi", "Duronto", "Garib Rath",
    "Tejas", "Humsafar", "Amrit Bharat", "Namo Bharat", "Superfast",
]


def _load_train_types():
    path = _data_path("train_types.csv")
    if not os.path.exists(path):
        return {}
    t = pd.read_csv(path, dtype=str).dropna()
    out = defaultdict(set)
    for tn, cat in zip(t["train_number"].str.strip(), t["category"].str.strip()):
        out[tn].add(cat)
    return dict(out)


TRAIN_TYPES = _load_train_types()  # train_number -> {categories}


def train_types_of(train_number) -> set:
    return TRAIN_TYPES.get(str(train_number).strip(), set())


def train_type_label(train_number) -> str:
    """Most specific category for display ('' for plain trains); 'Superfast' only if nothing else."""
    cats = train_types_of(train_number)
    for c in TRAIN_TYPE_ORDER:
        if c in cats:
            return c
    return ""


def available_train_types() -> list:
    present = {c for cats in TRAIN_TYPES.values() for c in cats}
    return [c for c in TRAIN_TYPE_ORDER if c in present]


def train_matches_types(train_number, wanted) -> bool:
    return bool(train_types_of(train_number) & set(wanted or ()))


def _keep_trains_of_types(df, train_cols, wanted):
    """Rows where ANY of train_cols is a train of one of the wanted types."""
    if not wanted or df.empty:
        return df
    mask = pd.Series(False, index=df.index)
    for col in train_cols:
        mask |= df[col].map(lambda tn: train_matches_types(tn, wanted))
    return df[mask]


# =====================================================================================
# SMALL HELPERS
# =====================================================================================
def format_time(t) -> str:
    """'23:40:00' -> '23:40' for cleaner UI."""
    s = str(t or "").strip()
    if not s:
        return ""
    parts = s.split(":")
    if len(parts) >= 2:
        return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}"
    return s


def format_duration(hours) -> str:
    """3.5 -> '3h 30m'."""
    try:
        h = float(hours)
    except (TypeError, ValueError):
        return ""
    total_min = int(round(h * 60))
    hh, mm = divmod(abs(total_min), 60)
    if hh and mm:
        return f"{hh}h {mm}m"
    if hh:
        return f"{hh}h"
    return f"{mm}m"


def comfort_label(layover_hrs: float) -> str:
    """Human-friendly layover quality for UI badges."""
    if layover_hrs < 1.0:
        return "Tight"
    if layover_hrs <= 3.0:
        return "Comfortable"
    if layover_hrs <= 6.0:
        return "Relaxed"
    if layover_hrs <= 12.0:
        return "Long wait"
    return "Overnight+"


def is_overnight_layover(arr_at_c: str, dep_from_c: str) -> bool:
    """True when connection crosses midnight or sits through deep night hours."""
    try:
        arr_h = int(str(arr_at_c).split(":")[0])
        dep_h = int(str(dep_from_c).split(":")[0])
    except (ValueError, IndexError):
        return False
    # Next-day connection, or both sides in awkward late/early window
    if dep_h < arr_h:
        return True
    return arr_h >= 22 or dep_h <= 5


def irctc_train_url(train_number: str) -> str:
    """Public IRCTC train-schedule search deep link (opens in browser)."""
    tn = str(train_number).strip()
    return f"https://www.irctc.co.in/nget/train-search?trainNumber={tn}"


def get_data_stats():
    """Coverage stats for the About / data tab."""
    return {
        "trains": int(df2["train_number"].nunique()),
        "schedule_rows": int(len(df2)),
        "stations_in_schedule": int(df2["station_code"].nunique()),
        "stations_master": len(NAME_TO_CODE),
        "running_days_known": sum(1 for v in RUNNING_DAYS_MAP.values() if v),
        "running_days_unknown": sum(1 for v in RUNNING_DAYS_MAP.values() if not v)
        + max(0, int(df2["train_number"].nunique()) - len(RUNNING_DAYS_MAP)),
    }


# =====================================================================================
# CORE SEARCH — CONNECTING TRAINS
# =====================================================================================
def find_connections_pro(
    start_code,
    end_code,
    via_code=None,
    min_layover_hrs=1,
    max_layover_hrs=12,
    pref_dep_after=None,
    pref_dep_before=None,
    pref_arr_after=None,
    pref_arr_before=None,
    search_date=None,          # datetime.date or None
    sort_by="fastest",         # "fastest" | "layover" | "departure"
    exclude_overnight=False,
    max_results=None,
    train_types=None,          # keep routes where at least one train is of these types
):
    # 1. LEG 1 (start -> mid)
    starts = df2[df2["station_code"] == start_code][
        ["train_number", "stop_seq", "abs_departure", "departure", "train_name", "day"]
    ]
    starts = starts.rename(
        columns={
            "stop_seq": "start_seq",
            "abs_departure": "abs_start_dep",
            "departure": "start_time",
            "train_name": "train1_name",
            "day": "train1_start_day",
        }
    )

    if pref_dep_after is not None:
        starts = starts[starts["start_time"] >= pref_dep_after]
    if pref_dep_before is not None:
        starts = starts[starts["start_time"] <= pref_dep_before]

    leg1 = pd.merge(starts, df2, on="train_number")
    leg1 = leg1[leg1["stop_seq"] > leg1["start_seq"]]
    leg1["t1_duration_hrs"] = (leg1["abs_arrival"] - leg1["abs_start_dep"]).dt.total_seconds() / 3600
    leg1 = leg1[leg1["t1_duration_hrs"] > 0]

    leg1 = leg1[
        [
            "train_number",
            "train1_name",
            "station_code",
            "station_name",
            "start_time",
            "arrival",
            "t1_duration_hrs",
            "train1_start_day",
            "day",
        ]
    ]
    leg1 = leg1.rename(
        columns={
            "train_number": "train1",
            "arrival": "arr_at_c",
            "station_code": "station_c",
            "start_time": "dep_from_a",
            "day": "train1_mid_day",
        }
    )

    # 2. LEG 2 (mid -> end)
    ends = df2[df2["station_code"] == end_code][
        ["train_number", "stop_seq", "abs_arrival", "arrival", "train_name"]
    ]
    ends = ends.rename(
        columns={
            "stop_seq": "end_seq",
            "abs_arrival": "abs_end_arr",
            "arrival": "end_time",
            "train_name": "train2_name",
        }
    )

    if pref_arr_after is not None:
        ends = ends[ends["end_time"] >= pref_arr_after]
    if pref_arr_before is not None:
        ends = ends[ends["end_time"] <= pref_arr_before]

    leg2 = pd.merge(ends, df2, on="train_number")
    leg2 = leg2[leg2["stop_seq"] < leg2["end_seq"]]
    leg2["t2_duration_hrs"] = (leg2["abs_end_arr"] - leg2["abs_departure"]).dt.total_seconds() / 3600
    leg2 = leg2[leg2["t2_duration_hrs"] > 0]

    leg2 = leg2[
        ["train_number", "train2_name", "station_code", "departure", "end_time", "t2_duration_hrs", "day"]
    ]
    leg2 = leg2.rename(
        columns={
            "train_number": "train2",
            "departure": "dep_from_c",
            "station_code": "station_c",
            "end_time": "arr_at_b",
            "day": "train2_mid_day",
        }
    )

    # 3. INTERSECTIONS
    connections = pd.merge(leg1, leg2, on="station_c")
    connections = connections[connections["train1"] != connections["train2"]]
    connections = connections[connections["train1_name"] != connections["train2_name"]]

    if via_code is not None:
        connections = connections[connections["station_c"] == via_code]

    if connections.empty:
        return pd.DataFrame()

    # 4. MATH
    connections["arr_time"] = pd.to_timedelta(connections["arr_at_c"])
    connections["dep_time"] = pd.to_timedelta(connections["dep_from_c"])
    next_day_mask = connections["dep_time"] < connections["arr_time"]
    connections.loc[next_day_mask, "dep_time"] += pd.Timedelta(days=1)

    connections["layover_hours"] = (connections["dep_time"] - connections["arr_time"]).dt.total_seconds() / 3600
    connections["total_journey_hrs"] = (
        connections["t1_duration_hrs"] + connections["layover_hours"] + connections["t2_duration_hrs"]
    )
    connections["crosses_midnight"] = next_day_mask

    # Calendar-day offset of Train 2's departure from the mid station, counted from the day the
    # traveller leaves the START station. Must use the real start time of day, not just durations:
    # leave 22:00 + 4h ride + 3h wait = 05:00 NEXT day (offset 1), though 7h // 24 would say 0.
    start_tod_hrs = pd.to_timedelta(connections["dep_from_a"]).dt.total_seconds() / 3600
    connections["day_offset_calc"] = (
        (start_tod_hrs + connections["t1_duration_hrs"] + connections["layover_hours"]) // 24
    ).astype(int)

    # 5. LAYOVER WINDOW
    valid_routes = connections[
        (connections["layover_hours"] >= min_layover_hrs)
        & (connections["layover_hours"] <= max_layover_hrs)
    ].copy()
    if valid_routes.empty:
        return pd.DataFrame()

    valid_routes = _keep_trains_of_types(valid_routes, ["train1", "train2"], train_types)
    if valid_routes.empty:
        return pd.DataFrame()

    if exclude_overnight:
        overnight_mask = [
            is_overnight_layover(a, d) or bool(cm)
            for a, d, cm in zip(
                valid_routes["arr_at_c"], valid_routes["dep_from_c"], valid_routes["crosses_midnight"]
            )
        ]
        valid_routes = valid_routes[[not x for x in overnight_mask]]
        if valid_routes.empty:
            return pd.DataFrame()

    # 6. OPTIONAL: FILTER BY SPECIFIC TRAVEL DATE (running days at boarding stations)
    if search_date is not None:
        weekday0 = search_date.weekday()  # Monday=0 — date user leaves START station

        valid_routes = valid_routes.copy()
        # Train 1: running days are for ORIGIN; adjust by journey day at start station
        t1_ok = [
            train_runs_on(
                tn,
                origin_weekday_for_boarding(weekday0, day),
            )
            for tn, day in zip(valid_routes["train1"], valid_routes["train1_start_day"])
        ]
        valid_routes = valid_routes[t1_ok]

        if not valid_routes.empty:
            offset_days = valid_routes["day_offset_calc"].astype(int)
            valid_routes["train2_day_offset"] = offset_days
            # Weekday when Train 2 leaves the mid station
            mid_weekday = (weekday0 + offset_days) % 7
            # Convert mid-station weekday → Train 2 origin weekday using Train 2's day at mid
            keep_mask = [
                train_runs_on(tn, origin_weekday_for_boarding(int(mwd), day))
                for tn, mwd, day in zip(
                    valid_routes["train2"], mid_weekday, valid_routes["train2_mid_day"]
                )
            ]
            valid_routes = valid_routes[keep_mask]

        if valid_routes.empty:
            return pd.DataFrame()
    else:
        valid_routes = valid_routes.copy()
        valid_routes["train2_day_offset"] = valid_routes["day_offset_calc"].astype(int)

    # 7. SORT + DEDUPE (keep the best duplicate train1/train2 pair per chosen metric)
    sort_col_map = {"fastest": "total_journey_hrs", "layover": "layover_hours", "departure": "dep_from_a"}
    sort_col = sort_col_map.get(sort_by, "total_journey_hrs")
    valid_routes = valid_routes.sort_values(sort_col)
    valid_routes = valid_routes.drop_duplicates(subset=["train1", "train2"], keep="first")

    # 8. OUTPUT
    final_output = valid_routes[
        [
            "station_name",
            "station_c",
            "train1",
            "train1_name",
            "dep_from_a",
            "arr_at_c",
            "train2",
            "train2_name",
            "dep_from_c",
            "arr_at_b",
            "t1_duration_hrs",
            "t2_duration_hrs",
            "layover_hours",
            "total_journey_hrs",
            "crosses_midnight",
            "train2_day_offset",
        ]
    ].copy()

    final_output["Train_1_Running_Days"] = final_output["train1"].apply(running_days_text)
    final_output["Train_2_Running_Days"] = final_output["train2"].apply(running_days_text)
    final_output["Comfort"] = final_output["layover_hours"].apply(comfort_label)
    final_output["Overnight_Layover"] = [
        "Yes" if (cm or is_overnight_layover(a, d)) else "No"
        for a, d, cm in zip(
            final_output["arr_at_c"], final_output["dep_from_c"], final_output["crosses_midnight"]
        )
    ]
    final_output["Leg1_Hrs"] = final_output["t1_duration_hrs"].round(1)
    final_output["Leg2_Hrs"] = final_output["t2_duration_hrs"].round(1)

    final_output = final_output.rename(
        columns={
            "station_name": "Via_Station",
            "station_c": "Via_Code",
            "train1": "Train_1_No",
            "train1_name": "Train_1_Name",
            "dep_from_a": "Leave_Start",
            "arr_at_c": "Arrive_Mid",
            "train2": "Train_2_No",
            "train2_name": "Train_2_Name",
            "dep_from_c": "Leave_Mid",
            "arr_at_b": "Arrive_End",
            "layover_hours": "Layover_Hrs",
            "total_journey_hrs": "Total_Hrs",
            "train2_day_offset": "Train2_Day_Offset",
        }
    )

    for col in ["Leave_Start", "Arrive_Mid", "Leave_Mid", "Arrive_End"]:
        final_output[col] = final_output[col].apply(format_time)

    final_output["Layover_Hrs"] = final_output["Layover_Hrs"].round(1)
    final_output["Total_Hrs"] = final_output["Total_Hrs"].round(1)

    display_cols = [
        "Via_Station",
        "Via_Code",
        "Train_1_No",
        "Train_1_Name",
        "Leave_Start",
        "Arrive_Mid",
        "Leg1_Hrs",
        "Train_2_No",
        "Train_2_Name",
        "Leave_Mid",
        "Arrive_End",
        "Leg2_Hrs",
        "Layover_Hrs",
        "Total_Hrs",
        "Comfort",
        "Overnight_Layover",
        "Train_1_Running_Days",
        "Train_2_Running_Days",
        "Train2_Day_Offset",
    ]
    final_output = final_output[display_cols]

    out_sort_col = {
        "fastest": "Total_Hrs",
        "layover": "Layover_Hrs",
        "departure": "Leave_Start",
    }.get(sort_by, "Total_Hrs")
    final_output = final_output.sort_values(out_sort_col).reset_index(drop=True)

    if max_results is not None and max_results > 0:
        final_output = final_output.head(int(max_results))

    return final_output


# =====================================================================================
# NEARBY / ALTERNATE STATIONS IN THE SAME CITY
# =====================================================================================
# Curated: the schedule data has no city grouping. Codes missing from the schedule are ignored,
# so the lists can safely be extended.
CITY_CLUSTERS = {
    "Delhi": ["NDLS", "DLI", "NZM", "ANVT", "DEE", "DEC", "DSA", "SZM"],
    "Mumbai": ["CSMT", "LTT", "MMCT", "BDTS", "DDR", "BVI", "TNA", "PNVL"],
    "Kolkata": ["HWH", "SDAH", "KOAA", "SHM", "SRC"],
    "Chennai": ["MAS", "MS", "MSB", "TBM", "PER"],
    "Bengaluru": ["SBC", "YPR", "SMVB", "BNC", "KJM", "BNCE"],
    "Hyderabad": ["SC", "HYB", "KCG", "LPI", "BMT"],
    "Pune": ["PUNE", "SVJR"],
    "Ahmedabad": ["ADI", "MAN"],
    "Lucknow": ["LKO", "LJN", "GTNR"],
    "Patna": ["PNBE", "RJPB", "DNR", "PPTA"],
    "Jaipur": ["JP", "GADJ"],
    "Kochi": ["ERS", "ERN"],
    "Thiruvananthapuram": ["TVC", "TVP"],
    "Ranchi": ["RNC", "HTE"],
    "Guwahati": ["GHY", "KYQ"],
    "Agra": ["AGC", "AF", "RKM"],
    "Prayagraj": ["PRYJ", "PRG"],
    "Surat": ["ST", "UDN"],
    "Coimbatore": ["CBE", "CBF"],
    "Bhopal": ["BPL", "RKMP"],
}
_SCHEDULE_CODES = set(df2["station_code"])
_CODE_TO_CITY = {code: city for city, codes in CITY_CLUSTERS.items() for code in codes}


def station_label(code: str) -> str:
    """'NDLS' -> 'NEW DELHI (NDLS)'"""
    name = _display_station_name(code)
    return f"{name} ({code})" if name else str(code)


def get_nearby_stations(code: str) -> list:
    """The station itself first, then the other stations of its city that trains actually serve."""
    code = (code or "").strip().upper()
    if not code:
        return []
    city = _CODE_TO_CITY.get(code)
    if city is None:
        return [code]
    others = [c for c in CITY_CLUSTERS[city] if c != code and c in _SCHEDULE_CODES]
    return [code] + others


def _station_pairs(start_code, end_code, include_nearby=True) -> list:
    """(from, to) pairs to search. Same-city searches (e.g. NDLS -> NZM) are never widened."""
    if not start_code or not end_code or start_code == end_code:
        return []
    starts, ends = [start_code], [end_code]
    if include_nearby:
        starts, ends = get_nearby_stations(start_code), get_nearby_stations(end_code)
        if set(starts) & set(ends):
            starts, ends = [start_code], [end_code]
    return [(s, e) for s in starts for e in ends if s != e]


def _tag_pair(df, s, e):
    out = df.copy()
    out.insert(0, "End_Code", e)
    out.insert(0, "Start_Code", s)
    return out


def find_connections_multi(start_code, end_code, include_nearby=True, max_results=None, **kwargs):
    """find_connections_pro over every nearby from/to pair; rows carry Start_Code / End_Code."""
    sort_by = kwargs.get("sort_by", "fastest")
    frames = []
    for s, e in _station_pairs(start_code, end_code, include_nearby):
        r = find_connections_pro(s, e, **kwargs)
        if not r.empty:
            frames.append(_tag_pair(r, s, e))
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    sort_col = {"fastest": "Total_Hrs", "layover": "Layover_Hrs", "departure": "Leave_Start"}.get(
        sort_by, "Total_Hrs"
    )
    out = out.sort_values(sort_col, kind="stable").reset_index(drop=True)
    if max_results is not None and max_results > 0:
        out = out.head(int(max_results))
    return out


# =====================================================================================
# THREE-TRAIN CONNECTIONS (start -> A -> B -> end)
# =====================================================================================
THREE_TRAIN_COLUMNS = [
    "Start_Code", "End_Code",
    "Via_1_Station", "Via_1_Code", "Via_2_Station", "Via_2_Code",
    "Train_1_No", "Train_1_Name", "Leave_Start", "Arrive_Via1", "Leg1_Hrs",
    "Train_2_No", "Train_2_Name", "Leave_Via1", "Arrive_Via2", "Leg2_Hrs",
    "Train_3_No", "Train_3_Name", "Leave_Via2", "Arrive_End", "Leg3_Hrs",
    "Layover_1_Hrs", "Layover_2_Hrs", "Total_Hrs", "Comfort", "Overnight_Layover",
    "Train_1_Running_Days", "Train_2_Running_Days", "Train_3_Running_Days",
    "Train2_Day_Offset", "Train3_Day_Offset",
]


def _hours(td_series):
    return td_series.dt.total_seconds() / 3600


def _runs_mask(trains, weekdays, days):
    return [
        train_runs_on(tn, origin_weekday_for_boarding(int(wd), int(d)))
        for tn, wd, d in zip(trains, weekdays, days)
    ]


def find_three_train_connections(
    start_code,
    end_code,
    include_nearby=True,
    min_layover_hrs=1,
    max_layover_hrs=8,
    max_total_hrs=72,
    pref_dep_after=None,
    pref_dep_before=None,
    pref_arr_after=None,
    pref_arr_before=None,
    search_date=None,
    sort_by="fastest",
    exclude_overnight=False,
    max_results=None,
    train_types=None,
):
    """Journeys with two changes: start -> A (train 1), A -> B (train 2), B -> end (train 3).

    max_total_hrs prunes the search early, so pass the best 2-train time to only see faster options.
    """
    pairs = _station_pairs(start_code, end_code, include_nearby)
    if not pairs:
        return pd.DataFrame()
    starts = list(dict.fromkeys(p[0] for p in pairs))
    ends = list(dict.fromkeys(p[1] for p in pairs))
    excluded = set(starts) | set(ends)
    min_lay = float(min_layover_hrs)
    max_lay = float(max_layover_hrs)
    cap = float(max_total_hrs)

    # LEG 1: start -> A
    s = df2[df2["station_code"].isin(starts)][
        ["train_number", "stop_seq", "station_code", "abs_departure", "departure", "train_name", "day"]
    ].rename(columns={
        "train_number": "t1", "stop_seq": "s_seq", "station_code": "Start_Code",
        "abs_departure": "abs_dep1", "departure": "leave_start", "train_name": "t1_name", "day": "t1_start_day",
    })
    if pref_dep_after is not None:
        s = s[s["leave_start"] >= pref_dep_after]
    if pref_dep_before is not None:
        s = s[s["leave_start"] <= pref_dep_before]
    a_rows = df2[~df2["station_code"].isin(excluded)][
        ["train_number", "stop_seq", "station_code", "station_name", "abs_arrival", "arrival"]
    ].rename(columns={
        "train_number": "t1", "stop_seq": "a_seq", "station_code": "via1", "station_name": "via1_name",
        "abs_arrival": "abs_arr_a", "arrival": "arr_a",
    })
    leg1 = s.merge(a_rows, on="t1")
    leg1 = leg1[leg1["a_seq"] > leg1["s_seq"]]
    leg1["dur1"] = _hours(leg1["abs_arr_a"] - leg1["abs_dep1"])
    leg1 = leg1[(leg1["dur1"] > 0) & (leg1["dur1"] < cap)]
    if leg1.empty:
        return pd.DataFrame()
    leg1["dep1_tod"] = _hours(leg1["abs_dep1"]) % 24
    leg1["arr1_tod"] = _hours(leg1["abs_arr_a"]) % 24
    leg1 = leg1[["t1", "t1_name", "Start_Code", "via1", "via1_name", "leave_start", "arr_a",
                 "dur1", "dep1_tod", "arr1_tod", "t1_start_day"]]

    # LEG 3: B -> end
    e = df2[df2["station_code"].isin(ends)][
        ["train_number", "stop_seq", "station_code", "abs_arrival", "arrival", "train_name"]
    ].rename(columns={
        "train_number": "t3", "stop_seq": "e_seq", "station_code": "End_Code",
        "abs_arrival": "abs_arr_end", "arrival": "arrive_end", "train_name": "t3_name",
    })
    if pref_arr_after is not None:
        e = e[e["arrive_end"] >= pref_arr_after]
    if pref_arr_before is not None:
        e = e[e["arrive_end"] <= pref_arr_before]
    b_rows = df2[~df2["station_code"].isin(excluded)][
        ["train_number", "stop_seq", "station_code", "station_name", "abs_departure", "departure", "day"]
    ].rename(columns={
        "train_number": "t3", "stop_seq": "b_seq", "station_code": "via2", "station_name": "via2_name",
        "abs_departure": "abs_dep_b", "departure": "dep_b", "day": "t3_mid_day",
    })
    leg3 = e.merge(b_rows, on="t3")
    leg3 = leg3[leg3["b_seq"] < leg3["e_seq"]]
    leg3["dur3"] = _hours(leg3["abs_arr_end"] - leg3["abs_dep_b"])
    leg3 = leg3[(leg3["dur3"] > 0) & (leg3["dur3"] < cap)]
    if leg3.empty:
        return pd.DataFrame()
    leg3["dep3_tod"] = _hours(leg3["abs_dep_b"]) % 24
    leg3 = leg3[["t3", "t3_name", "End_Code", "via2", "via2_name", "dep_b", "arrive_end",
                 "dur3", "dep3_tod", "t3_mid_day"]]

    min1 = leg1.groupby("via1")["dur1"].min()
    min3 = leg3.groupby("via2")["dur3"].min()

    # MIDDLE: A -> B on one train, only between stations that both outer legs can reach
    m_a = df2[df2["station_code"].isin(min1.index)][
        ["train_number", "stop_seq", "station_code", "abs_departure", "departure", "train_name", "day"]
    ].rename(columns={
        "train_number": "t2", "stop_seq": "ma_seq", "station_code": "via1",
        "abs_departure": "abs_dep2", "departure": "dep_a2", "train_name": "t2_name", "day": "t2_mid_day",
    })
    m_b = df2[df2["station_code"].isin(min3.index)][
        ["train_number", "stop_seq", "station_code", "abs_arrival", "arrival"]
    ].rename(columns={
        "train_number": "t2", "stop_seq": "mb_seq", "station_code": "via2",
        "abs_arrival": "abs_arr2", "arrival": "arr_b2",
    })
    mid = m_a.merge(m_b, on="t2")
    mid = mid[(mid["mb_seq"] > mid["ma_seq"]) & (mid["via1"] != mid["via2"])]
    mid["dur2"] = _hours(mid["abs_arr2"] - mid["abs_dep2"])
    room = cap - 2 * min_lay - mid["via1"].map(min1) - mid["via2"].map(min3)
    mid = mid[(mid["dur2"] > 0) & (mid["dur2"] <= room)]
    if mid.empty:
        return pd.DataFrame()
    mid["dep2_tod"] = _hours(mid["abs_dep2"]) % 24
    mid["arr2_tod"] = _hours(mid["abs_arr2"]) % 24
    mid = mid[["t2", "t2_name", "via1", "via2", "dep_a2", "arr_b2", "dur2", "dep2_tod", "arr2_tod", "t2_mid_day"]]

    # JOIN 1: train 1 -> train 2 at A
    j = mid.merge(leg1, on="via1")
    j = j[(j["t1"] != j["t2"]) & (j["t1_name"] != j["t2_name"])]
    j["lay1"] = (j["dep2_tod"] - j["arr1_tod"]) % 24
    j = j[(j["lay1"] >= min_lay) & (j["lay1"] <= max_lay)]
    j["partial"] = j["dur1"] + j["lay1"] + j["dur2"]
    j = j[j["partial"] + min_lay + j["via2"].map(min3) <= cap]
    if j.empty:
        return pd.DataFrame()

    # JOIN 2: train 2 -> train 3 at B
    j = j.merge(leg3, on="via2")
    j = j[(j["t3"] != j["t2"]) & (j["t3"] != j["t1"]) & (j["t3_name"] != j["t2_name"])]
    j["lay2"] = (j["dep3_tod"] - j["arr2_tod"]) % 24
    j = j[(j["lay2"] >= min_lay) & (j["lay2"] <= max_lay)]
    j["total"] = j["partial"] + j["lay2"] + j["dur3"]
    j = j[j["total"] <= cap]
    j = _keep_trains_of_types(j, ["t1", "t2", "t3"], train_types)
    if j.empty:
        return pd.DataFrame()

    # Calendar-day offsets of trains 2 and 3, counted from the day the traveller leaves the start
    j["off2"] = ((j["dep1_tod"] + j["dur1"] + j["lay1"]) // 24).astype(int)
    j["off3"] = ((j["dep1_tod"] + j["partial"] + j["lay2"]) // 24).astype(int)

    if exclude_overnight:
        keep = [
            not (is_overnight_layover(a1, d1) or is_overnight_layover(a2, d2))
            for a1, d1, a2, d2 in zip(j["arr_a"], j["dep_a2"], j["arr_b2"], j["dep_b"])
        ]
        j = j[keep]
        if j.empty:
            return pd.DataFrame()

    if search_date is not None:
        wd0 = search_date.weekday()
        ok = _runs_mask(j["t1"], [wd0] * len(j), j["t1_start_day"])
        j = j[ok]
        if not j.empty:
            ok = _runs_mask(j["t2"], (wd0 + j["off2"]) % 7, j["t2_mid_day"])
            j = j[ok]
        if not j.empty:
            ok = _runs_mask(j["t3"], (wd0 + j["off3"]) % 7, j["t3_mid_day"])
            j = j[ok]
        if j.empty:
            return pd.DataFrame()

    sort_col = {"fastest": "total", "layover": "lay_max", "departure": "leave_start"}.get(sort_by, "total")
    j["lay_max"] = j[["lay1", "lay2"]].max(axis=1)
    j = j.sort_values(sort_col, kind="stable")
    j = j.drop_duplicates(subset=["t1", "t2", "t3"], keep="first")
    if max_results is not None and max_results > 0:
        j = j.head(int(max_results))
    j = j.reset_index(drop=True)

    out = pd.DataFrame({
        "Start_Code": j["Start_Code"],
        "End_Code": j["End_Code"],
        "Via_1_Station": j["via1_name"],
        "Via_1_Code": j["via1"],
        "Via_2_Station": j["via2_name"],
        "Via_2_Code": j["via2"],
        "Train_1_No": j["t1"],
        "Train_1_Name": j["t1_name"],
        "Leave_Start": j["leave_start"].apply(format_time),
        "Arrive_Via1": j["arr_a"].apply(format_time),
        "Leg1_Hrs": j["dur1"].round(1),
        "Train_2_No": j["t2"],
        "Train_2_Name": j["t2_name"],
        "Leave_Via1": j["dep_a2"].apply(format_time),
        "Arrive_Via2": j["arr_b2"].apply(format_time),
        "Leg2_Hrs": j["dur2"].round(1),
        "Train_3_No": j["t3"],
        "Train_3_Name": j["t3_name"],
        "Leave_Via2": j["dep_b"].apply(format_time),
        "Arrive_End": j["arrive_end"].apply(format_time),
        "Leg3_Hrs": j["dur3"].round(1),
        "Layover_1_Hrs": j["lay1"].round(1),
        "Layover_2_Hrs": j["lay2"].round(1),
        "Total_Hrs": j["total"].round(1),
    })
    out["Comfort"] = j["lay_max"].apply(comfort_label)
    out["Overnight_Layover"] = [
        "Yes" if (is_overnight_layover(a1, d1) or is_overnight_layover(a2, d2)) else "No"
        for a1, d1, a2, d2 in zip(j["arr_a"], j["dep_a2"], j["arr_b2"], j["dep_b"])
    ]
    out["Train_1_Running_Days"] = j["t1"].apply(running_days_text)
    out["Train_2_Running_Days"] = j["t2"].apply(running_days_text)
    out["Train_3_Running_Days"] = j["t3"].apply(running_days_text)
    out["Train2_Day_Offset"] = j["off2"]
    out["Train3_Day_Offset"] = j["off3"]
    return out[THREE_TRAIN_COLUMNS]


def _search_over_dates(search_fn, dates, preferred, key_cols, sort_col, max_results, **kwargs):
    """Run search_fn once per date. Each route is kept once, on the date closest to `preferred`
    (earlier wins a tie); rows get Board_Date (ISO) and Also_OK_On (the other dates it works on)."""
    frames = []
    for d in dates:
        r = search_fn(search_date=d, max_results=None, **kwargs)
        if not r.empty:
            r = r.copy()
            r["Board_Date"] = d.isoformat()
            frames.append(r)
    if not frames:
        return pd.DataFrame()
    allr = pd.concat(frames, ignore_index=True)
    allr["_gap"] = allr["Board_Date"].map(lambda s: abs((datetime.date.fromisoformat(s) - preferred).days))
    allr = allr.sort_values(["_gap", "Board_Date"], kind="stable")
    also = allr.groupby(key_cols)["Board_Date"].agg(
        lambda s: ", ".join(
            datetime.date.fromisoformat(x).strftime("%a %d %b") for x in sorted(s)
        )
    )
    out = allr.drop_duplicates(subset=key_cols, keep="first").drop(columns="_gap")
    out["Also_OK_On"] = [also[k] for k in out[key_cols].itertuples(index=False, name=None)]
    out["Board_On"] = out["Board_Date"].map(
        lambda s: datetime.date.fromisoformat(s).strftime("%a %d %b %Y")
    )
    if sort_col == "_lay_max":
        out["_lay_max"] = out[["Layover_1_Hrs", "Layover_2_Hrs"]].max(axis=1)
    out = out.sort_values(sort_col, kind="stable").drop(columns="_lay_max", errors="ignore")
    out = out.reset_index(drop=True)
    if max_results is not None and max_results > 0:
        out = out.head(int(max_results))
    return out


def find_connections_flexible(start_code, end_code, search_date, dates, include_nearby=True,
                              max_results=None, **kwargs):
    """find_connections_multi over several travel dates (see flexible_dates())."""
    sort_by = kwargs.get("sort_by", "fastest")
    sort_col = {"fastest": "Total_Hrs", "layover": "Layover_Hrs", "departure": "Leave_Start"}.get(
        sort_by, "Total_Hrs"
    )
    return _search_over_dates(
        lambda search_date, max_results, **kw: find_connections_multi(
            start_code, end_code, include_nearby=include_nearby, search_date=search_date,
            max_results=max_results, **kw
        ),
        dates, search_date,
        ["Start_Code", "End_Code", "Via_Code", "Train_1_No", "Train_2_No"],
        sort_col, max_results, **kwargs,
    )


def find_three_train_flexible(start_code, end_code, search_date, dates, include_nearby=True,
                              max_results=None, **kwargs):
    """find_three_train_connections over several travel dates (see flexible_dates())."""
    sort_by = kwargs.get("sort_by", "fastest")
    sort_col = {"fastest": "Total_Hrs", "layover": "_lay_max", "departure": "Leave_Start"}.get(
        sort_by, "Total_Hrs"
    )
    return _search_over_dates(
        lambda search_date, max_results, **kw: find_three_train_connections(
            start_code, end_code, include_nearby=include_nearby, search_date=search_date,
            max_results=max_results, **kw
        ),
        dates, search_date,
        ["Start_Code", "End_Code", "Train_1_No", "Train_2_No", "Train_3_No"],
        sort_col, max_results, **kwargs,
    )


def get_possible_via_stations(start_code, end_code, include_nearby=False):
    """Return a short, route-specific list of 'NAME (CODE)' via-station options
    (instead of forcing the user to scroll every station in the country)."""
    if not start_code or not end_code or start_code == end_code:
        return []
    results = find_connections_multi(
        start_code, end_code, include_nearby=include_nearby, min_layover_hrs=0, max_layover_hrs=24
    )
    if results.empty:
        return []
    pairs = results[["Via_Station", "Via_Code"]].drop_duplicates()
    options = sorted(f"{row.Via_Station} ({row.Via_Code})" for row in pairs.itertuples())
    return options


# =====================================================================================
# DIRECT TRAINS (no change of train)
# =====================================================================================
def find_direct_trains(
    start_code,
    end_code,
    pref_dep_after=None,
    pref_dep_before=None,
    pref_arr_after=None,
    pref_arr_before=None,
    search_date=None,
    flexible_days=0,
    sort_by="fastest",
    train_types=None,
):
    """
    Trains that stop at start then later at end — no interchange needed.

    search_date = date you board at START station (not necessarily the origin day).
    flexible_days = also check N days before and N days after search_date, never before today
    (0 = exact date only).
    """
    if not start_code or not end_code or start_code == end_code:
        return pd.DataFrame()

    starts = df2[df2["station_code"] == start_code][
        ["train_number", "train_name", "stop_seq", "abs_departure", "departure", "day"]
    ].rename(
        columns={
            "stop_seq": "start_seq",
            "abs_departure": "abs_start_dep",
            "departure": "dep_time",
            "day": "dep_day",
        }
    )
    ends = df2[df2["station_code"] == end_code][
        ["train_number", "stop_seq", "abs_arrival", "arrival", "day"]
    ].rename(
        columns={
            "stop_seq": "end_seq",
            "abs_arrival": "abs_end_arr",
            "arrival": "arr_time",
            "day": "arr_day",
        }
    )

    merged = pd.merge(starts, ends, on="train_number")
    merged = merged[merged["end_seq"] > merged["start_seq"]]
    merged["duration_hrs"] = (merged["abs_end_arr"] - merged["abs_start_dep"]).dt.total_seconds() / 3600
    merged = merged[merged["duration_hrs"] > 0]
    merged = _keep_trains_of_types(merged, ["train_number"], train_types)

    if pref_dep_after is not None:
        merged = merged[merged["dep_time"] >= pref_dep_after]
    if pref_dep_before is not None:
        merged = merged[merged["dep_time"] <= pref_dep_before]
    if pref_arr_after is not None:
        merged = merged[merged["arr_time"] >= pref_arr_after]
    if pref_arr_before is not None:
        merged = merged[merged["arr_time"] <= pref_arr_before]

    if search_date is not None and not merged.empty:
        flex = max(0, int(flexible_days or 0))
        candidate_dates = flexible_dates(search_date, flex, flex) if flex else [search_date]

        kept_rows = []
        for row in merged.itertuples(index=False):
            matching_dates = [
                d
                for d in candidate_dates
                if train_runs_on_boarding_date(row.train_number, d, row.dep_day)
            ]
            if not matching_dates:
                continue
            # Prefer the exact travel date, else nearest day
            best = min(
                matching_dates,
                key=lambda d: (abs((d - search_date).days), d.toordinal()),
            )
            origin_wd = origin_weekday_for_boarding(best.weekday(), row.dep_day)
            kept_rows.append(
                {
                    "train_number": row.train_number,
                    "train_name": row.train_name,
                    "dep_time": row.dep_time,
                    "arr_time": row.arr_time,
                    "dep_day": row.dep_day,
                    "arr_day": row.arr_day,
                    "duration_hrs": row.duration_hrs,
                    "start_seq": row.start_seq,
                    "end_seq": row.end_seq,
                    "boarding_date": best,
                    "available_dates": ", ".join(
                        d.strftime("%a %d %b") for d in matching_dates
                    ),
                    "leaves_origin_on": WEEKDAYS[origin_wd],
                }
            )

        merged = pd.DataFrame(kept_rows)
    else:
        if not merged.empty:
            merged = merged.copy()
            merged["boarding_date"] = None
            merged["available_dates"] = ""
            merged["leaves_origin_on"] = ""

    if merged.empty:
        return pd.DataFrame()

    merged = merged.sort_values("duration_hrs").drop_duplicates(subset=["train_number"], keep="first")
    merged["Running_Days"] = merged["train_number"].apply(running_days_text)
    merged["Stops_Between"] = (merged["end_seq"] - merged["start_seq"] - 1).astype(int)

    out_cols = [
        "train_number",
        "train_name",
        "dep_time",
        "arr_time",
        "dep_day",
        "arr_day",
        "duration_hrs",
        "Stops_Between",
        "Running_Days",
        "leaves_origin_on",
        "boarding_date",
        "available_dates",
    ]
    out = merged[out_cols].copy()
    out["dep_time"] = out["dep_time"].apply(format_time)
    out["arr_time"] = out["arr_time"].apply(format_time)
    out["duration_hrs"] = out["duration_hrs"].round(1)
    out["board_date_iso"] = out["boarding_date"].apply(
        lambda d: d.isoformat() if isinstance(d, datetime.date) else ""
    )
    out["boarding_date"] = out["boarding_date"].apply(
        lambda d: d.strftime("%a %d %b %Y") if isinstance(d, datetime.date) else ""
    )
    out = out.rename(
        columns={
            "train_number": "Train_No",
            "train_name": "Train_Name",
            "dep_time": "Departure",
            "arr_time": "Arrival",
            "dep_day": "Journey_Day_At_Start",
            "arr_day": "Journey_Day_At_End",
            "duration_hrs": "Duration_Hrs",
            "leaves_origin_on": "Leaves_Origin_On",
            "boarding_date": "Board_On",
            "available_dates": "Also_OK_On",
            "board_date_iso": "Board_Date",
        }
    )

    sort_map = {"fastest": "Duration_Hrs", "departure": "Departure", "layover": "Duration_Hrs"}
    out = out.sort_values(sort_map.get(sort_by, "Duration_Hrs")).reset_index(drop=True)

        # Hide empty flexible-date columns when not used
    if search_date is None:
        out = out.drop(
            columns=["Board_On", "Also_OK_On", "Leaves_Origin_On", "Board_Date"],
            errors="ignore",
        )
    elif int(flexible_days or 0) <= 0:
        out = out.drop(columns=["Also_OK_On"], errors="ignore")

    return out


def find_directs_multi(start_code, end_code, include_nearby=True, sort_by="fastest", **kwargs):
    """find_direct_trains over every nearby from/to pair; rows carry Start_Code / End_Code."""
    frames = []
    for s, e in _station_pairs(start_code, end_code, include_nearby):
        r = find_direct_trains(s, e, sort_by=sort_by, **kwargs)
        if not r.empty:
            frames.append(_tag_pair(r, s, e))
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    sort_col = "Departure" if sort_by == "departure" else "Duration_Hrs"
    return out.sort_values(sort_col, kind="stable").reset_index(drop=True)


# =====================================================================================
# STATION EXPLORER
# =====================================================================================
def get_trains_at_station(station_code, time_after=None, time_before=None):
    """All trains that stop at a station, with arrival/departure and running days."""
    if not station_code:
        return pd.DataFrame()

    stops = df2[df2["station_code"] == station_code].copy()
    if stops.empty:
        return pd.DataFrame()

    if time_after is not None:
        stops = stops[stops["departure"] >= time_after]
    if time_before is not None:
        stops = stops[stops["departure"] <= time_before]

    if stops.empty:
        return pd.DataFrame()

    # Prefer the earliest stop if a train somehow appears twice
    stops = stops.sort_values(["train_number", "stop_seq"]).drop_duplicates(
        subset=["train_number"], keep="first"
    )

    rows = []
    for row in stops.itertuples():
        meta = TRAIN_META.get(row.train_number, {})
        full = df2[df2["train_number"] == row.train_number].sort_values("stop_seq")
        origin = meta.get("source") or (full["station_name"].iloc[0] if len(full) else "")
        dest = meta.get("destination") or (full["station_name"].iloc[-1] if len(full) else "")
        rows.append(
            {
                "Train_No": row.train_number,
                "Train_Name": row.train_name or meta.get("train_name", ""),
                "Arrival": format_time(row.arrival),
                "Departure": format_time(row.departure),
                "Day": int(row.day),
                "From": origin,
                "To": dest,
                "Running_Days": running_days_text(row.train_number),
            }
        )

    out = pd.DataFrame(rows)
    return out.sort_values("Departure").reset_index(drop=True)


def get_top_via_hubs(start_code, end_code, limit=15, include_nearby=False):
    """Most common interchange stations for a route (by number of valid connections)."""
    results = find_connections_multi(
        start_code, end_code, include_nearby=include_nearby, min_layover_hrs=0.5, max_layover_hrs=18
    )
    if results.empty:
        return pd.DataFrame()
    hubs = (
        results.groupby(["Via_Station", "Via_Code"], as_index=False)
        .agg(
            Connections=("Train_1_No", "count"),
            Best_Total_Hrs=("Total_Hrs", "min"),
            Best_Layover=("Layover_Hrs", "min"),
        )
        .sort_values(["Connections", "Best_Total_Hrs"], ascending=[False, True])
        .head(limit)
        .reset_index(drop=True)
    )
    return hubs


# =====================================================================================
# TRAIN LOOKUP (search by number / name, view full schedule + running days)
# =====================================================================================
def search_trains(query, limit=25):
    """Match a typed query against train number (prefix/contains) or train name (contains)."""
    q = str(query).strip().upper()
    if not q:
        return []
    matches = [(tn, name) for tn, name in TRAIN_INDEX if q in tn.upper() or q in (name or "").upper()]
    # Prioritise exact / prefix train-number matches first.
    matches.sort(key=lambda x: (not x[0].upper().startswith(q), x[0]))
    return matches[:limit]


def get_train_schedule(train_number):
    """Full stop-by-stop schedule for one train, in a UI-friendly format."""
    train_number = str(train_number).strip()
    sched = df2[df2["train_number"] == train_number].sort_values("stop_seq").copy()
    if sched.empty:
        return pd.DataFrame()

    sched["halt_minutes"] = ((sched["abs_departure"] - sched["abs_arrival"]).dt.total_seconds() / 60).round(0)
    sched.loc[sched["halt_minutes"] < 0, "halt_minutes"] = 0

    out = sched[["stop_seq", "day", "station_name", "station_code", "arrival", "departure", "halt_minutes"]].copy()
    out["stop_seq"] = out["stop_seq"] + 1
    out["arrival"] = out["arrival"].apply(format_time)
    out["departure"] = out["departure"].apply(format_time)
    out = out.rename(
        columns={
            "stop_seq": "Stop #",
            "day": "Day",
            "station_name": "Station",
            "station_code": "Code",
            "arrival": "Arrival",
            "departure": "Departure",
            "halt_minutes": "Halt (min)",
        }
    )
    return out.reset_index(drop=True)


def train_stops_between(train_number, from_code, to_code) -> list:
    """
    Stops strictly between from_code and to_code on one train, in running order.
    Each: {code, name, arrival, departure, day_offset, trains}; day_offset = how many calendar days
    after the train leaves from_code it leaves this stop (decides the date of the B->C ticket),
    trains = how many trains call here (a proxy for how well-connected / likely to have quota).
    Empty if the train doesn't call at both stations in that order.
    """
    sched = df2[df2["train_number"] == str(train_number).strip()].sort_values("stop_seq")
    codes = sched["station_code"].tolist()
    from_code, to_code = str(from_code).strip().upper(), str(to_code).strip().upper()
    if from_code not in codes or to_code not in codes:
        return []
    ia, ic = codes.index(from_code), codes.index(to_code)
    if ia >= ic:
        return []
    day = pd.Timedelta(days=1)
    start_day = int(sched["abs_departure"].iloc[ia] // day)
    counts = df2["station_code"].value_counts()
    out = []
    for _, r in sched.iloc[ia + 1:ic].iterrows():
        out.append({
            "code": r["station_code"],
            "name": r["station_name"],
            "arrival": format_time(r["arrival"]),
            "departure": format_time(r["departure"]),
            "day_offset": int(r["abs_departure"] // day) - start_day,
            "trains": int(counts.get(r["station_code"], 0)),
        })
    return out


def get_running_days_bits(train_number):
    return RUNNING_DAYS_MAP.get(str(train_number).strip(), "")


def get_train_summary(train_number):
    """High-level info card for a train: name, source/destination, running days, duration."""
    train_number = str(train_number).strip()
    raw = df2[df2["train_number"] == train_number].sort_values("stop_seq")
    if raw.empty:
        return None

    meta = TRAIN_META.get(train_number, {})
    name = meta.get("train_name") or raw["train_name"].iloc[0] or "Unknown"
    source = meta.get("source") or raw["station_name"].iloc[0]
    destination = meta.get("destination") or raw["station_name"].iloc[-1]
    duration_hrs = round((raw["abs_arrival"].iloc[-1] - raw["abs_departure"].iloc[0]).total_seconds() / 3600, 1)

    return {
        "train_number": train_number,
        "train_name": name,
        "source": source,
        "destination": destination,
        "running_days_text": running_days_text(train_number),
        "running_days_bits": get_running_days_bits(train_number),
        "total_stops": len(raw),
        "total_duration_hrs": duration_hrs,
        "irctc_url": irctc_train_url(train_number),
    }
