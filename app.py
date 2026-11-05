import datetime
from typing import Optional

import pandas as pd
import streamlit as st

try:
    from engine import (
        find_connections_multi,
        find_directs_multi,
        get_nearby_stations,
        station_label,
        get_possible_via_stations,
        get_trains_at_station,
        get_top_via_hubs,
        station_list,
        search_trains,
        get_train_schedule,
        get_train_summary,
        format_duration,
        irctc_train_url,
        WEEKDAYS,
    )
    import trip_tools as tt
    from live_availability import (
        fetch_many_routes,
        fetch_connection_availability,
        fetch_route_availability,
        pick_train,
        status_tone,
    )
except FileNotFoundError as e:
    st.set_page_config(page_title="Train Connector", layout="wide")
    st.error(
        f"Couldn't load one of the data files: **{e}**.\n\n"
        "Make sure `stations.csv`, `train_schedule_scrapped.csv` and "
        "`running_days_scrapped.csv` are all present in the app folder."
    )
    st.stop()


# =====================================================================================
# PAGE + THEME
# =====================================================================================
st.set_page_config(
    page_title="Train Connector",
    layout="wide",
    page_icon="🚆",
    initial_sidebar_state="collapsed",
)

if "theme" not in st.session_state:
    st.session_state.theme = "dark"
if "swap_tick" not in st.session_state:
    st.session_state.swap_tick = 0
if "conn_results" not in st.session_state:
    st.session_state.conn_results = None
if "conn_meta" not in st.session_state:
    st.session_state.conn_meta = {}
if "seat_cache" not in st.session_state:
    st.session_state.seat_cache = {}
if "route_cache" not in st.session_state:
    st.session_state.route_cache = {}  # live ConfirmTkt lookups, shared by fare sorting / class filter
if "direct_results" not in st.session_state:
    st.session_state.direct_results = None
if "direct_meta" not in st.session_state:
    st.session_state.direct_meta = {}
if "direct_seat_cache" not in st.session_state:
    st.session_state.direct_seat_cache = {}
if "show_direct_from_conn" not in st.session_state:
    st.session_state.show_direct_from_conn = False
if "conn_directs" not in st.session_state:
    st.session_state.conn_directs = None


def extract_code(station_string):
    """'NEW DELHI (NDLS)' -> 'NDLS'"""
    if station_string:
        return station_string.split("(")[-1].replace(")", "").strip()
    return None


def nearby_caption(start_code, end_code) -> str:
    """Tell the user which extra stations a nearby-station search will include."""
    parts = []
    for code in (start_code, end_code):
        extra = get_nearby_stations(code)[1:] if code else []
        if extra:
            parts.append(f"{code}: " + ", ".join(extra))
    return "Also searching " + " · ".join(parts) if parts else "No nearby stations known for these stations."


def comfort_color(label: str, dark: bool) -> str:
    palette = {
        "Tight": "#f59e0b" if dark else "#b45309",
        "Comfortable": "#34d399" if dark else "#15803d",
        "Relaxed": "#60a5fa" if dark else "#1d4ed8",
        "Long wait": "#a78bfa" if dark else "#6d28d9",
        "Overnight+": "#f87171" if dark else "#b91c1c",
    }
    return palette.get(label, "#94a3b8" if dark else "#475569")


def apply_theme_css(theme: str):
    dark = theme == "dark"
    if dark:
        bg = "#0b1220"
        bg2 = "#121a2b"
        panel = "#162033"
        panel2 = "#1c2940"
        text = "#f8fafc"
        muted = "#cbd5e1"
        border = "#334155"
        primary = "#2563eb"      # white text on this = 5.2:1 (passes WCAG AA)
        primary_h = "#1d4ed8"
        accent = "#2dd4bf"
        input_bg = "#1e293b"
        btn_bg = "#1e293b"
        btn_text = "#f8fafc"
        hero_grad = "linear-gradient(135deg, #0b1220 0%, #132447 48%, #0f3d3a 100%)"
        shadow = "0 12px 40px rgba(0,0,0,0.35)"
        alert_bg = "#1c2940"
    else:
        bg = "#f4f7fb"
        bg2 = "#ffffff"
        panel = "#ffffff"
        panel2 = "#eef3f9"
        text = "#0f172a"
        muted = "#475569"
        border = "#d5deea"
        primary = "#1d4ed8"
        primary_h = "#1e40af"
        accent = "#0f766e"
        input_bg = "#ffffff"
        btn_bg = "#ffffff"
        btn_text = "#0f172a"
        hero_grad = "linear-gradient(135deg, #e8f1ff 0%, #f7fbff 45%, #e6f7f4 100%)"
        shadow = "0 10px 30px rgba(15, 23, 42, 0.08)"
        alert_bg = "#eef3f9"

    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Fraunces:opsz,wght@9..144,600;9..144,700&display=swap');

        /* Streamlit theme tokens — widgets read these */
        :root, .stApp, [data-testid="stAppViewContainer"] {{
            --primary-color: {primary} !important;
            --background-color: {bg} !important;
            --secondary-background-color: {panel2} !important;
            --text-color: {text} !important;
            color-scheme: {"dark" if dark else "light"};
        }}

        html, body, [class*="css"] {{
            font-family: 'DM Sans', sans-serif !important;
            color: {text} !important;
        }}

        .stApp, .stApp > header, [data-testid="stAppViewContainer"],
        [data-testid="stMain"], section.main, .main {{
            background-color: {bg} !important;
            color: {text} !important;
        }}

        [data-testid="stHeader"] {{
            background: {bg} !important;
        }}

        .block-container {{
            padding-top: 3.6rem;  /* clear the fixed Streamlit header (renders ~60px tall) */
            padding-bottom: 3rem;
            max-width: 1180px;
        }}

        /* Force readable text on Streamlit content — but NOT inside inputs */
        .stMarkdown, .stMarkdown p, .stMarkdown li,
        .stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4,
        .stMarkdown h5, .stMarkdown h6, .stCaption, [data-testid="stCaption"],
        .stSelectbox label, .stRadio label, .stCheckbox label,
        .stSlider label, .stNumberInput label, .stTextInput label,
        .stDateInput label, .stTimeInput label, .stToggle label,
        h1, h2, h3, h4, h5, h6 {{
            color: {text} !important;
        }}

        .stMarkdown strong, .stMarkdown b {{
            color: {text} !important;
        }}

        /* Hero */
        .tc-hero {{
            background: {hero_grad};
            border: 1px solid {border};
            border-radius: 22px;
            padding: 1.6rem 1.8rem 1.4rem;
            margin-bottom: 1.1rem;
            box-shadow: {shadow};
            position: relative;
            overflow: hidden;
        }}
        .tc-hero::after {{
            content: "";
            position: absolute;
            right: -40px;
            top: -40px;
            width: 180px;
            height: 180px;
            border-radius: 50%;
            background: radial-gradient(circle, {accent}33, transparent 70%);
            pointer-events: none;
        }}
        .tc-brand {{
            font-family: 'Fraunces', Georgia, serif !important;
            font-size: 2.05rem;
            font-weight: 700;
            letter-spacing: -0.02em;
            margin: 0;
            padding: 0;
            color: {text} !important;
            line-height: 1.15;
        }}
        .tc-sub {{
            margin: 0.45rem 0 0;
            color: {muted} !important;
            font-size: 1.02rem;
            max-width: 52ch;
        }}
        .tc-chip-row {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin-top: 0.95rem;
        }}
        .tc-chip {{
            font-size: 0.78rem;
            font-weight: 600;
            letter-spacing: 0.02em;
            padding: 0.28rem 0.65rem;
            border-radius: 999px;
            border: 1px solid {border};
            background: {panel2};
            color: {muted} !important;
        }}

        /* Date strip */
        .tc-date-panel {{
            background: {panel};
            border: 1px solid {border};
            border-radius: 18px;
            padding: 1rem 1.15rem 0.35rem;
            margin: 0.35rem 0 1rem;
            box-shadow: {shadow};
        }}
        .tc-date-label {{
            font-size: 0.78rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: {accent} !important;
            margin-bottom: 0.15rem;
        }}
        .tc-date-hint {{
            color: {muted} !important;
            font-size: 0.88rem;
            margin-top: -0.35rem;
            margin-bottom: 0.55rem;
        }}

        .tc-panel {{
            background: {panel};
            border: 1px solid {border};
            border-radius: 16px;
            padding: 1rem 1.1rem;
            margin-bottom: 0.85rem;
            color: {text} !important;
        }}
        .tc-route-box {{
            border-left: 4px solid var(--route-color, {primary});
            background: {panel2};
            border-radius: 0 12px 12px 0;
            padding: 0.9rem 1rem;
            margin: 0.55rem 0 0.75rem;
            color: {text} !important;
        }}
        .tc-muted {{ color: {muted} !important; font-size: 0.85rem; }}
        .tc-seat-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 0.5rem;
            margin-top: 0.55rem;
        }}
        .tc-seat-pill {{
            border-radius: 10px;
            border: 1px solid {border};
            background: {bg2};
            padding: 0.55rem 0.65rem;
            font-size: 0.84rem;
            color: {text} !important;
        }}
        .tc-seat-pill .cls {{
            font-weight: 700;
            font-size: 0.78rem;
            letter-spacing: 0.04em;
            color: {muted} !important;
            margin-bottom: 0.15rem;
        }}
        .tc-seat-pill.good {{ border-color: #22c55e88; background: {"#10241a" if dark else "#f0fdf4"}; }}
        .tc-seat-pill.warn {{ border-color: #f59e0b88; background: {"#2a2110" if dark else "#fffbeb"}; }}
        .tc-seat-pill.wait {{ border-color: #38bdf888; background: {"#0f2230" if dark else "#f0f9ff"}; }}
        .tc-seat-pill.bad {{ border-color: #ef444488; background: {"#2a1414" if dark else "#fef2f2"}; }}

        div[data-testid="stVerticalBlockBorderWrapper"] {{
            background: {panel} !important;
            border-color: {border} !important;
            color: {text} !important;
        }}

        .stSelectbox label, .stRadio label, .stCheckbox label,
        .stSlider label, .stNumberInput label, .stTextInput label,
        .stDateInput label, .stTimeInput label, .stToggle label {{
            color: {text} !important;
            font-weight: 600 !important;
        }}

        /* Inputs — force visible text in dark + light */
        .stSelectbox div[data-baseweb="select"] > div,
        div[data-baseweb="select"] > div,
        div[data-baseweb="select"] > div > div,
        div[data-baseweb="select"] *,
        div[data-baseweb="select"] span,
        div[data-baseweb="select"] input,
        div[data-baseweb="input"] input,
        .stTextInput input, .stNumberInput input,
        div[data-testid="stDateInput"] input,
        div[data-testid="stTextInput"] input,
        textarea {{
            background-color: {input_bg} !important;
            color: {text} !important;
            -webkit-text-fill-color: {text} !important;
            caret-color: {text} !important;
            border-color: {border} !important;
        }}

        div[data-baseweb="select"] svg {{
            fill: {text} !important;
            color: {text} !important;
        }}

        div[data-baseweb="select"] [data-testid="stMarkdownContainer"],
        div[data-baseweb="select"] [data-testid="stMarkdownContainer"] *,
        div[data-baseweb="select"] div[aria-selected="true"],
        div[data-baseweb="select"] input {{
            color: {text} !important;
            -webkit-text-fill-color: {text} !important;
        }}

        [data-testid="stWidgetLabel"],
        [data-testid="stWidgetLabel"] *,
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stMarkdownContainer"] span {{
            color: {text} !important;
        }}

        /* Placeholder */
        div[data-baseweb="select"] input::placeholder,
        .stTextInput input::placeholder,
        div[data-testid="stDateInput"] input::placeholder {{
            color: {muted} !important;
            -webkit-text-fill-color: {muted} !important;
            opacity: 0.85 !important;
        }}

        div[data-baseweb="popover"] ul,
        div[data-baseweb="menu"],
        ul[role="listbox"],
        li[role="option"] {{
            background-color: {panel} !important;
            color: {text} !important;
        }}
        li[role="option"] span,
        li[role="option"] div {{
            color: {text} !important;
        }}
        li[role="option"]:hover,
        li[aria-selected="true"] {{
            background-color: {panel2} !important;
        }}

        div[data-testid="stDateInput"] input {{
            font-size: 1.2rem !important;
            font-weight: 700 !important;
            padding: 0.85rem 0.9rem !important;
            border-radius: 12px !important;
            border: 2px solid {primary}55 !important;
            min-height: 3.4rem !important;
            background: {input_bg} !important;
            color: {text} !important;
            -webkit-text-fill-color: {text} !important;
        }}

        /* Radio / checkbox text */
        .stRadio label p, .stCheckbox label p, .stToggle label p {{
            color: {text} !important;
        }}

        /* Selectors target the button itself, so they still match when Streamlit wraps a
           button with a tooltip (buttons that have `help=`). */
        [data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-secondary"] p,
        [data-testid="stPopoverButton"], [data-testid="stPopoverButton"] p,
        [data-testid="stPopoverButton"] span {{
            color: {btn_text} !important;
        }}
        [data-testid="stBaseButton-secondary"], [data-testid="stPopoverButton"] {{
            border-radius: 12px !important;
            font-weight: 650 !important;
            border: 1px solid {border} !important;
            background: {btn_bg} !important;
        }}
        [data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primary"] p {{
            color: #ffffff !important;
        }}
        [data-testid="stBaseButton-primary"] {{
            border-radius: 12px !important;
            font-weight: 650 !important;
            background: {primary} !important;
            border: none !important;
            box-shadow: 0 8px 20px {primary}44;
        }}
        [data-testid="stBaseButton-primary"]:hover {{
            background: {primary_h} !important;
        }}
        [data-testid="stBaseButton-secondary"]:disabled, [data-testid="stBaseButton-primary"]:disabled {{
            opacity: 0.55; cursor: not-allowed;
        }}

        .stTabs [data-baseweb="tab-list"] {{
            gap: 0.35rem;
            background: {panel2} !important;
            padding: 0.35rem;
            border-radius: 14px;
            border: 1px solid {border};
        }}
        .stTabs [data-baseweb="tab"] {{
            border-radius: 10px;
            color: {muted} !important;
            font-weight: 600;
            background: transparent !important;
        }}
        .stTabs [aria-selected="true"] {{
            background: {panel} !important;
            color: {text} !important;
        }}

        div[data-testid="stMetric"] {{
            background: {panel2} !important;
            border: 1px solid {border};
            border-radius: 14px;
            padding: 0.65rem 0.85rem;
        }}
        div[data-testid="stMetricValue"],
        div[data-testid="stMetricValue"] * {{
            color: {text} !important;
        }}
        div[data-testid="stMetricLabel"],
        div[data-testid="stMetricLabel"] * {{
            color: {muted} !important;
        }}

        .stExpander, [data-testid="stExpander"],
        [data-testid="stExpander"] details,
        [data-testid="stExpander"] summary {{
            background: {panel} !important;
            border-color: {border} !important;
            color: {text} !important;
            border-radius: 14px !important;
        }}
        [data-testid="stExpander"] summary span,
        [data-testid="stExpander"] p,
        [data-testid="stExpander"] div {{
            color: {text} !important;
        }}

        div[data-testid="stAlert"] {{
            background: {alert_bg} !important;
            color: {text} !important;
        }}
        div[data-testid="stAlert"] * {{
            color: {text} !important;
        }}

        [data-testid="stDataFrame"],
        [data-testid="stDataFrame"] * {{
            color: {text};
        }}

        hr {{
            border-color: {border} !important;
        }}

        .tc-footer {{
            text-align: center;
            color: {muted} !important;
            padding-top: 1.25rem;
            font-size: 0.9rem;
        }}
        .tc-footer b {{
            color: {text} !important;
        }}

        /* ---------- Product polish ---------- */
        /* Hide Streamlit chrome that isn't useful to end users */
        [data-testid="stAppDeployButton"], #MainMenu, footer {{ display: none !important; }}
        [data-testid="stHeader"] {{ height: 2.2rem; }}

        /* Section card (replaces the old empty date-panel box) */
        div[data-testid="stVerticalBlockBorderWrapper"] {{
            border-radius: 16px !important;
            box-shadow: {shadow};
        }}
        .tc-section-title {{
            font-size: 0.74rem; font-weight: 700; letter-spacing: 0.09em;
            text-transform: uppercase; color: {accent} !important; margin: 0 0 0.1rem;
        }}
        .tc-section-hint {{ color: {muted} !important; font-size: 0.86rem; margin: 0 0 0.6rem; }}

        /* Step guide on the empty state */
        .tc-steps {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 0.7rem; margin: 0.4rem 0 0.9rem; }}
        .tc-step {{
            background: {panel}; border: 1px solid {border}; border-radius: 14px; padding: 0.8rem 0.95rem;
        }}
        .tc-step b {{ display: block; margin-bottom: 0.15rem; color: {text} !important; }}
        .tc-step span {{ color: {muted} !important; font-size: 0.86rem; }}
        .tc-step .n {{
            display: inline-flex; width: 1.5rem; height: 1.5rem; align-items: center; justify-content: center;
            border-radius: 999px; background: {primary}; color: #fff !important; font-size: 0.8rem;
            font-weight: 700; margin-bottom: 0.4rem;
        }}

        /* Comfort pill used in result headers */
        .tc-pill {{
            display: inline-block; padding: 0.12rem 0.55rem; border-radius: 999px; font-size: 0.74rem;
            font-weight: 700; border: 1px solid currentColor; vertical-align: middle;
        }}

        /* Tabs: bigger touch targets, scroll instead of wrapping on small screens */
        .stTabs [data-baseweb="tab-list"] {{ overflow-x: auto; flex-wrap: nowrap; scrollbar-width: none; }}
        .stTabs [data-baseweb="tab"] {{ padding: 0.45rem 0.95rem; white-space: nowrap; min-height: 2.6rem; }}
        .stTabs [data-baseweb="tab-highlight"] {{ background: {primary} !important; }}

        /* Keyboard focus must be visible */
        button:focus-visible, input:focus-visible, [role="tab"]:focus-visible {{
            outline: 3px solid {accent} !important; outline-offset: 2px;
        }}

        /* Primary action stands out; secondary buttons stay quiet */
        [data-testid^="stBaseButton-"] {{ min-height: 2.75rem; transition: transform .08s ease, box-shadow .15s ease; }}
        [data-testid="stBaseButton-secondary"]:hover, [data-testid="stPopoverButton"]:hover {{
            border-color: {primary} !important;
        }}
        [data-testid="stPopoverButton"] {{ min-height: 2.75rem; }}
        /* The popover panel and the code box inside it follow the theme too */
        [data-testid="stPopoverBody"] {{ background-color: {panel} !important; border: 1px solid {border}; }}
        [data-testid="stPopoverBody"] pre, [data-testid="stPopoverBody"] code {{
            background-color: {panel2} !important; color: {text} !important; white-space: pre-wrap;
        }}
        [data-testid^="stBaseButton-"]:active {{ transform: translateY(1px); }}

        /* Tooltips (from help=...) render in a portal; the global text-colour rule made them unreadable */
        [data-testid="stTooltipContent"], [data-testid="stTooltipContent"] * {{
            color: {text} !important;
            background-color: {panel2} !important;
            border-radius: 8px;
        }}
        div[data-baseweb="tooltip"] > div, div[data-baseweb="popover"] [data-testid="stTooltipContent"] {{
            background-color: {panel2} !important; border: 1px solid {border};
        }}

        /* Dropdown menus render in a portal outside .stApp, so theme them explicitly */
        body {{ background-color: {bg} !important; }}
        div[data-baseweb="popover"] > div, div[data-baseweb="popover"] [data-baseweb="menu"],
        [data-testid="stSelectboxVirtualDropdown"], [data-testid="stSelectboxVirtualDropdown"] > div {{
            background-color: {panel} !important; color: {text} !important; border-radius: 12px;
        }}
        div[data-baseweb="popover"] li, div[data-baseweb="popover"] [role="option"],
        div[data-baseweb="popover"] [role="option"] * {{ color: {text} !important; }}
        div[data-baseweb="popover"] [role="option"]:hover {{ background-color: {panel2} !important; }}
        div[data-baseweb="popover"] [data-testid="stSelectboxVirtualDropdown"] ~ div,
        div[data-baseweb="popover"] [aria-live] {{ color: {muted} !important; }}

        /* Expanders read as cards */
        [data-testid="stExpander"] {{ border: 1px solid {border} !important; margin-bottom: 0.6rem; }}
        [data-testid="stExpander"] summary {{ font-weight: 600; }}

        /* Metrics: tighter on all sizes */
        div[data-testid="stMetricValue"] {{ font-size: 1.35rem; }}

        /* Phones */
        @media (max-width: 640px) {{
            .block-container {{ padding-left: 0.8rem; padding-right: 0.8rem; padding-top: 3.2rem; }}
            .tc-hero {{ padding: 1.1rem 1.1rem 1rem; border-radius: 16px; }}
            .tc-brand {{ font-size: 1.55rem; }}
            .tc-sub {{ font-size: 0.93rem; }}
            .tc-chip-row {{ display: none; }}
            div[data-testid="stDateInput"] input {{ font-size: 1.05rem !important; min-height: 3rem !important; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_running_day_badges(bits: str, dark: bool):
    on = "#0f766e" if not dark else "#14b8a6"
    off = "#334155" if dark else "#cbd5e1"
    badge_cols = st.columns(7)
    if bits:
        for i, day in enumerate(WEEKDAYS):
            runs = bits[i] == "1"
            badge_cols[i].markdown(
                f"<div style='text-align:center; padding:7px; border-radius:8px; "
                f"background:{on if runs else off}; color:white; font-size:13px; font-weight:600;'>"
                f"{day}</div>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("No running-day data on file — assumed to run daily.")


def _parse_board_date(row) -> Optional[datetime.date]:
    iso = str(row.get("Board_Date") or "").strip()
    if iso:
        try:
            return datetime.date.fromisoformat(iso)
        except ValueError:
            pass
    return None


def _now_india() -> datetime.datetime:
    """Current time in India (IST, UTC+5:30)."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=5, minutes=30)


def _parse_hhmm(time_str) -> Optional[datetime.time]:
    s = str(time_str or "").strip()
    if not s:
        return None
    parts = s.replace(".", ":").split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return datetime.time(h % 24, m % 60)
    except (ValueError, TypeError):
        return None


def departure_booking_status(
    board_date: Optional[datetime.date],
    dep_time_str: str = "",
) -> dict:
    """
    Compare boarding date + departure time with current IST time.
    Returns status: bookable | departed | no_booking | unknown
    """
    now = _now_india()
    today = now.date()
    dep_t = _parse_hhmm(dep_time_str)

    if board_date is None:
        return {
            "status": "unknown",
            "departed": False,
            "label": "",
            "scraped_date_label": "",
            "now_label": now.strftime("%d %b %Y, %I:%M %p IST"),
        }

    scraped_label = board_date.strftime("%a %d %b %Y")
    dep_label = dep_t.strftime("%H:%M") if dep_t else (str(dep_time_str) or "—")

    if board_date < today:
        return {
            "status": "departed",
            "departed": True,
            "label": f"No more booking — train already left on {scraped_label} (dep {dep_label}).",
            "scraped_date_label": scraped_label,
            "now_label": now.strftime("%d %b %Y, %I:%M %p IST"),
        }

    if board_date == today and dep_t is not None:
        dep_dt = datetime.datetime.combine(board_date, dep_t)
        if now >= dep_dt:
            return {
                "status": "departed",
                "departed": True,
                "label": (
                    f"No more booking — train departed at {dep_label} "
                    f"(now {now.strftime('%I:%M %p')} IST)."
                ),
                "scraped_date_label": scraped_label,
                "now_label": now.strftime("%d %b %Y, %I:%M %p IST"),
            }

    if board_date == today:
        return {
            "status": "bookable",
            "departed": False,
            "label": f"Today · departs {dep_label}",
            "scraped_date_label": scraped_label,
            "now_label": now.strftime("%d %b %Y, %I:%M %p IST"),
        }

    return {
        "status": "bookable",
        "departed": False,
        "label": f"Upcoming · {scraped_label} · dep {dep_label}",
        "scraped_date_label": scraped_label,
        "now_label": now.strftime("%d %b %Y, %I:%M %p IST"),
    }


def _enrich_seat_result(
    seat: dict,
    board_date: Optional[datetime.date],
    dep_time_str: str = "",
) -> dict:
    """Attach scraped-date + departed info to a seat result dict."""
    out = dict(seat or {})
    status = departure_booking_status(board_date, dep_time_str)
    out["scraped_date"] = board_date.isoformat() if board_date else out.get("date", "")
    out["scraped_date_label"] = status["scraped_date_label"] or out.get("date", "")
    out["departed"] = status["departed"]
    out["booking_status"] = status["status"]
    out["booking_label"] = status["label"]
    out["checked_at"] = status["now_label"]
    out["dep_time"] = str(dep_time_str or "")
    # Also detect API "TRAIN DEPARTED" style statuses
    for row in out.get("classes") or []:
        st_txt = str(row.get("status") or "").upper()
        if "DEPART" in st_txt:
            out["departed"] = True
            if not out.get("booking_label") or out.get("booking_status") != "departed":
                out["booking_label"] = "No more booking — train departed (from live data)."
            out["booking_status"] = "departed"
            break
    return out


def render_seat_block(leg: dict, title: str, cls: str = "Any"):
    scraped = leg.get("scraped_date_label") or leg.get("date") or "—"
    st.markdown(f"**{title}** · `{leg.get('train_number', '')}` {leg.get('train_name', '')}")
    st.caption(
        f"{leg.get('from', '?')} → {leg.get('to', '?')}  ·  "
        f"**Scraped for:** {scraped}"
        + (f"  ·  Dep {leg.get('dep_time')}" if leg.get("dep_time") else "")
        + (f"  ·  Checked at {leg.get('checked_at')}" if leg.get("checked_at") else "")
    )

    if leg.get("departed") or leg.get("booking_status") == "departed":
        st.error(leg.get("booking_label") or "No more booking — train has departed.")
    elif leg.get("booking_label"):
        st.info(leg.get("booking_label"))

    if not leg.get("ok"):
        st.warning(leg.get("error") or "Could not get seat info for this train.")
        return
    if not leg.get("found"):
        if leg.get("departed"):
            st.caption("Live list may hide departed trains — booking is closed for this departure.")
        else:
            st.info("This train was not found in live seat results for this date. Please check on IRCTC.")
        return
    classes = leg.get("classes") or []
    if not classes:
        st.caption("No seat class info returned — please check on IRCTC.")
        return
    shown = tt.filter_classes(classes, cls)
    if not shown:
        offered = ", ".join(sorted({str(r.get("class") or "?") for r in classes}))
        st.info(f"This train has no **{cls}** class. It offers: {offered}.")
        return
    pills = []
    for row in shown:
        tone = status_tone(row.get("status", ""))
        pills.append(
            f"<div class='tc-seat-pill {tone}'>"
            f"<div class='cls'>{row.get('class') or '?'}</div>"
            f"<div>{row.get('status') or '—'}</div>"
        )
        extra = []
        if row.get("prediction"):
            extra.append(row["prediction"] + (f" ({row['chance']}%)" if row.get("chance") else ""))
        if row.get("fare"):
            extra.append(f"₹{row['fare']}")
        if extra:
            pills[-1] += (
                f"<div class='tc-muted' style='margin-top:2px;font-size:0.75rem;'>"
                f"{' · '.join(extra)}</div>"
            )
        pills[-1] += "</div>"

    st.markdown(f"<div class='tc-seat-grid'>{''.join(pills)}</div>", unsafe_allow_html=True)

def render_direct_train_cards(
    directs: pd.DataFrame,
    start_code: str,
    end_code: str,
    start_label: str,
    end_label: str,
    quota: str = "GN",
    key_prefix: str = "direct",
    cls: str = "Any",
):
    """Show direct trains as cards with optional live seat check (no CSV / IRCTC links)."""
    st.subheader("Direct trains")
    st.caption(f"{start_label} → {end_label}")

    for idx, (_, row) in enumerate(directs.iterrows()):
        row_start, row_end = tt.row_codes(row.to_dict(), start_code, end_code)
        board = row.get("Board_On") or "Any day"
        fare_val = row.get("Fare") if "Fare" in directs.columns else None
        fare_txt = ""
        if fare_val is not None and not pd.isna(fare_val):
            fare_txt = f"  ·  ₹{int(fare_val):,} {row.get('Fare_Class') or ''}".rstrip()
        stn_txt = f"{row_start}→{row_end}  ·  " if (row_start, row_end) != (start_code, end_code) else ""
        title = (
            f"{row['Train_No']} · {row['Train_Name']}  ·  {stn_txt}"
            f"{row['Departure']} → {row['Arrival']}  ·  "
            f"{format_duration(row['Duration_Hrs'])}{fare_txt}"
        )
        with st.expander(title, expanded=(idx < 3)):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Departure", row["Departure"])
            c2.metric("Arrival", row["Arrival"])
            c3.metric("Duration", format_duration(row["Duration_Hrs"]))
            c4.metric("Stops between", int(row.get("Stops_Between", 0) or 0))

            also_ok = str(row.get("Also_OK_On") or "").strip()
            also_html = (
                f"<div class='tc-muted' style='margin-top:4px;'>Also OK on: {also_ok}</div>"
                if also_ok
                else ""
            )
            st.markdown(
                f"""
                <div class="tc-route-box">
                  <div><b>{row['Train_No']}</b> — {row['Train_Name']}</div>
                  <div class="tc-muted" style="margin-top:6px;">
                    Board on: <b>{board}</b>
                    &nbsp;·&nbsp; Runs (from origin): {row.get('Running_Days', '')}
                    &nbsp;·&nbsp; Leaves origin on: {row.get('Leaves_Origin_On', '—')}
                  </div>
                  <div class="tc-muted" style="margin-top:4px;">
                    Journey day at start: {row.get('Journey_Day_At_Start', '—')}
                    &nbsp;·&nbsp; Journey day at end: {row.get('Journey_Day_At_End', '—')}
                  </div>
                  {also_html}
                </div>
                """,
                unsafe_allow_html=True,
            )

            board_date = _parse_board_date(row)
            dep_time = str(row.get("Departure") or "")
            pre_status = departure_booking_status(board_date, dep_time)
            if pre_status["departed"]:
                st.error(pre_status["label"])
            elif board_date:
                st.caption(f"Seat check date: **{pre_status['scraped_date_label']}** · {pre_status['label']}")

            if (row_start, row_end) != (start_code, end_code):
                st.caption(f"Nearby station: **{station_label(row_start)}** → **{station_label(row_end)}**")

            cache_key = f"{key_prefix}|{row_start}|{row_end}|{row['Train_No']}|{board_date}|{quota}"

            if st.button(
                "Check seats",
                key=f"{key_prefix}_seat_{idx}_{row['Train_No']}",
                width="stretch",
                disabled=board_date is None,
                help="Gets live seat availability for this train on the boarding date.",
            ):
                with st.spinner(f"Getting seats for train {row['Train_No']}…"):
                    route = fetch_route_availability(row_start, row_end, board_date, quota)
                    seat = pick_train(route, str(row["Train_No"]))
                    st.session_state.direct_seat_cache[cache_key] = _enrich_seat_result(
                        seat, board_date, dep_time
                    )
                st.rerun()

            if board_date is None:
                st.caption("Choose a travel date to check seats.")

            seat = st.session_state.direct_seat_cache.get(cache_key)
            if seat:
                st.markdown("##### Seat availability")
                render_seat_block(seat, "Train", cls)
                st.caption("Seat info from ConfirmTkt. Confirm on IRCTC before booking.")

            with st.popover("Copy as text", width="stretch"):
                st.caption("Use the copy icon at the top-right of the box.")
                st.code(
                    tt.direct_text(
                        row.to_dict(), start_label, end_label, quota,
                        seat if (seat and seat.get("found")) else None, cls,
                    ),
                    language=None,
                )


def render_connection_cards(
    results: pd.DataFrame,
    start_label: str,
    end_label: str,
    start_code: str,
    end_code: str,
    search_date: Optional[datetime.date],
    quota: str,
    dark: bool,
    limit: int = 12,
    cls: str = "Any",
):
    st.subheader("Route options")
    st.caption(
        f"Showing top {min(limit, len(results))} of {len(results)}. "
        "Use **Check seats (both trains)** to see seats for Train 1 and Train 2 at the same time."
    )

    for i, row in results.head(limit).iterrows():
        row_start, row_end = tt.row_codes(row.to_dict(), start_code, end_code)
        nearby = (row_start, row_end) != (start_code, end_code)
        row_start_label = station_label(row_start) if nearby else start_label
        row_end_label = station_label(row_end) if nearby else end_label
        comfort = row.get("Comfort", "")
        overnight = row.get("Overnight_Layover", "No") == "Yes"
        total_fare = row.get("Total_Fare") if "Total_Fare" in results.columns else None
        has_fare = total_fare is not None and not pd.isna(total_fare)
        title = (
            f"#{i + 1}  ·  via {row['Via_Station']} ({row['Via_Code']})  ·  "
            f"{format_duration(row['Total_Hrs'])} total  ·  "
            f"wait {format_duration(row['Layover_Hrs'])}"
            + (f"  ·  ₹{int(total_fare):,} ({row.get('Fare_Class')})" if has_fare else "")
        )
        with st.expander(title, expanded=(i < 2)):
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Total", format_duration(row["Total_Hrs"]))
            c2.metric("Wait", format_duration(row["Layover_Hrs"]))
            c3.metric("Comfort", comfort)
            c4.metric("Night wait", "Yes" if overnight else "No")
            c5.metric(
                "Total fare",
                f"₹{int(total_fare):,}" if has_fare else "—",
                help=(
                    f"{row.get('Fare_Class')}: ₹{int(row['Fare_Train_1']):,} + ₹{int(row['Fare_Train_2']):,}"
                    if has_fare and not pd.isna(row.get("Fare_Train_1")) and not pd.isna(row.get("Fare_Train_2"))
                    else "Press 'Load fares & classes' above the results to price every route."
                ),
            )

            color = comfort_color(str(comfort), dark)
            st.markdown(
                f"""
                <div class="tc-route-box" style="--route-color:{color};">
                  <div class="tc-muted" style="margin-bottom:6px;">
                    {row_start_label} → <b>{row['Via_Station']}</b> → {row_end_label}
                  </div>
                  <div style="display:flex; flex-wrap:wrap; gap:18px; font-size:14px;">
                    <div>
                      <div class="tc-muted" style="font-size:11px;">TRAIN 1 · {row['Train_1_No']}</div>
                      <div><b>{row['Train_1_Name']}</b></div>
                      <div>{row['Leave_Start']} → {row['Arrive_Mid']}
                           <span class="tc-muted">({format_duration(row['Leg1_Hrs'])})</span></div>
                      <div class="tc-muted" style="font-size:12px;">Runs: {row['Train_1_Running_Days']}</div>
                    </div>
                    <div style="opacity:0.45; align-self:center;">⇄</div>
                    <div>
                      <div class="tc-muted" style="font-size:11px;">TRAIN 2 · {row['Train_2_No']}</div>
                      <div><b>{row['Train_2_Name']}</b></div>
                      <div>{row['Leave_Mid']} → {row['Arrive_End']}
                           <span class="tc-muted">({format_duration(row['Leg2_Hrs'])})</span></div>
                      <div class="tc-muted" style="font-size:12px;">Runs: {row['Train_2_Running_Days']}</div>
                    </div>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if int(row.get("Train2_Day_Offset", 0) or 0) > 0:
                st.caption(
                    f"Train 2 is on travel day +{int(row['Train2_Day_Offset'])} "
                    "(because of wait time or overnight)."
                )

            cache_key = (
                f"{row_start}|{row['Via_Code']}|{row_end}|"
                f"{row['Train_1_No']}|{row['Train_2_No']}|"
                f"{search_date}|{quota}|{row.get('Train2_Day_Offset', 0)}"
            )

            seat_col = st.columns(1)[0]
            with seat_col:
                scrape_disabled = search_date is None
                if st.button(
                    "Check seats (both trains)",
                    key=f"seats_{i}_{row['Train_1_No']}_{row['Train_2_No']}",
                    width="stretch",
                    disabled=scrape_disabled,
                    help="Gets live seats for Train 1 and Train 2 together. Needs a travel date.",
                ):
                    with st.spinner("Getting seats for both trains…"):
                        raw = fetch_connection_availability(
                            start_code=row_start,
                            via_code=str(row["Via_Code"]),
                            end_code=row_end,
                            train1_no=str(row["Train_1_No"]),
                            train2_no=str(row["Train_2_No"]),
                            journey_date=search_date,
                            train2_day_offset=int(row.get("Train2_Day_Offset", 0) or 0),
                            quota=quota,
                        )
                        date2 = search_date + datetime.timedelta(
                            days=int(row.get("Train2_Day_Offset", 0) or 0)
                        )
                        raw["leg1"] = _enrich_seat_result(
                            raw.get("leg1") or {},
                            search_date,
                            str(row.get("Leave_Start") or ""),
                        )
                        raw["leg2"] = _enrich_seat_result(
                            raw.get("leg2") or {},
                            date2,
                            str(row.get("Leave_Mid") or ""),
                        )
                        st.session_state.seat_cache[cache_key] = raw
                    st.rerun()
                if scrape_disabled:
                    st.caption("Choose a travel date above to check seats.")
                elif search_date:
                    st.caption(
                        f"Will scrape seats for **{search_date.strftime('%a %d %b %Y')}** "
                        f"(Train 1) and matching date for Train 2."
                    )

            seat_data = st.session_state.seat_cache.get(cache_key)
            if seat_data:
                st.markdown("##### Seats for both trains")
                a, b = st.columns(2)
                with a:
                    render_seat_block(seat_data.get("leg1") or {}, "Train 1", cls)
                with b:
                    render_seat_block(seat_data.get("leg2") or {}, "Train 2", cls)
                st.caption(
                    "Seat info is live from ConfirmTkt. Always confirm on IRCTC before booking."
                )

            with st.popover("Copy as text", width="stretch"):
                st.caption("Use the copy icon at the top-right of the box.")
                st.code(
                    tt.connection_text(
                        row.to_dict(), row_start_label, row_end_label, search_date, quota,
                        seat_data if seat_data else None, cls,
                    ),
                    language=None,
                )


@st.cache_data(show_spinner=False)
def cached_via_options(start_code, end_code, include_nearby=False):
    return get_possible_via_stations(start_code, end_code, include_nearby)


@st.cache_data(show_spinner=False)
def cached_search_trains(query):
    return search_trains(query)


@st.cache_data(show_spinner=False)
def cached_hubs(start_code, end_code, include_nearby=False):
    return get_top_via_hubs(start_code, end_code, include_nearby=include_nearby)


# =====================================================================================
# HEADER + THEME TOGGLE
# =====================================================================================
apply_theme_css(st.session_state.theme)
dark = st.session_state.theme == "dark"

head_l, head_r = st.columns([7, 1.4], vertical_alignment="top")
with head_l:
    st.markdown(
        """
        <div class="tc-hero">
          <h1 class="tc-brand">Train Connector</h1>
          <p class="tc-sub">
            No direct train? Find the best two-train journey with a change in between,
            then check seats for both trains in one tap.
          </p>
          <div class="tc-chip-row">
            <span class="tc-chip">Connections</span>
            <span class="tc-chip">Direct trains</span>
            <span class="tc-chip">Live seats</span>
            <span class="tc-chip">Station explorer</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with head_r:
    theme_label = "Light mode" if dark else "Dark mode"
    if st.button(theme_label, width="stretch", key="theme_toggle", help="Switch between dark and light theme"):
        st.session_state.theme = "light" if dark else "dark"
        st.rerun()

tab_connect, tab_direct, tab_station, tab_lookup = st.tabs(
    [
        "Find Connections",
        "Direct Trains",
        "Station Explorer",
        "Train Lookup",
    ]
)

# =====================================================================================
# TAB 1: FIND CONNECTIONS
# =====================================================================================
with tab_connect:
    col1, col_swap, col2 = st.columns([5, 1, 5], vertical_alignment="bottom")
    with col1:
        start_station = st.selectbox(
            "Start Station",
            options=[""] + station_list,
            index=0,
            key=f"start_{st.session_state.swap_tick}",
            help="Type or select the city/station you are starting from.",
        )
    with col_swap:
        if st.button("⇄", help="Swap start and end", width="stretch", key="swap_btn"):
            a = st.session_state.get(f"start_{st.session_state.swap_tick}", "")
            b = st.session_state.get(f"end_{st.session_state.swap_tick}", "")
            st.session_state.swap_tick += 1
            st.session_state[f"start_{st.session_state.swap_tick}"] = b
            st.session_state[f"end_{st.session_state.swap_tick}"] = a
            st.rerun()
    with col2:
        end_station = st.selectbox(
            "End Station",
            options=[""] + station_list,
            index=0,
            key=f"end_{st.session_state.swap_tick}",
            help="Type or select your final destination.",
        )

    s_code_live = extract_code(start_station) if start_station else None
    e_code_live = extract_code(end_station) if end_station else None

    use_nearby = st.checkbox(
        "Include nearby stations in the same city",
        value=True,
        key="conn_nearby",
        help="Also search from/to other stations of the same city, e.g. NDLS, NZM, DLI, ANVT for Delhi.",
    )
    if use_nearby and s_code_live and e_code_live:
        st.caption(nearby_caption(s_code_live, e_code_live))

    # ---- JOURNEY DATE ----
    with st.container(border=True):
        st.markdown(
            "<div class='tc-section-title'>Travel date</div>"
            "<div class='tc-section-hint'>Used to check which trains run that day and to look up live seats.</div>",
            unsafe_allow_html=True,
        )
        dcol1, dcol2, dcol3 = st.columns([3, 2, 2])
        with dcol1:
            use_date = st.toggle("Use travel date", value=True, key="conn_use_date")
            search_date = None
            if use_date:
                search_date = st.date_input(
                    "Travel date",
                    value=datetime.date.today(),
                    min_value=datetime.date.today(),
                    max_value=datetime.date.today() + datetime.timedelta(days=120),
                    key="conn_travel_date",
                    label_visibility="collapsed",
                )
        with dcol2:
            if use_date and search_date:
                st.metric("Weekday", search_date.strftime("%A"))
            else:
                st.metric("Weekday", "Any day")
        with dcol3:
            quota = st.selectbox(
                "Quota (for seats)",
                options=["GN", "TQ", "PT", "LD", "SS"],
                index=0,
                help="GN = General, TQ = Tatkal, PT = Premium Tatkal, LD = Ladies, SS = Senior Citizen.",
                key="conn_quota",
            )

    with st.expander("Advanced filters", expanded=False):
        col3, col4 = st.columns(2)
        with col3:
            use_via = st.checkbox(
                "Route via specific mid-station",
                help="Unchecked = search all intersecting stations automatically.",
            )
            via_station = None
            if use_via:
                smart_via_list = []
                if s_code_live and e_code_live and s_code_live != e_code_live:
                    smart_via_list = cached_via_options(s_code_live, e_code_live, use_nearby)
                if smart_via_list:
                    via_station = st.selectbox(
                        "Select Mid-Station",
                        options=[""] + smart_via_list,
                        index=0,
                    )
                else:
                    if s_code_live and e_code_live:
                        st.caption("No route-specific mid-stations found yet — showing full list.")
                    via_station = st.selectbox(
                        "Select Mid-Station",
                        options=[""] + station_list,
                        index=0,
                    )

        with col4:
            use_max_wait = st.checkbox(
                "Set waiting time range",
                help="Default wait window is 1–12 hours.",
            )
            min_wait, max_wait = 1, 12
            if use_max_wait:
                min_wait, max_wait = st.slider(
                    "Wait window (Hours)",
                    min_value=0,
                    max_value=24,
                    value=(1, 8),
                )

        col5, col6 = st.columns(2)
        with col5:
            use_dep_time = st.checkbox("Restrict departure time")
            dep_after = None
            dep_before = None
            if use_dep_time:
                dep_after_ui = st.time_input("Don't leave before", value=None, key="conn_dep_after")
                dep_before_ui = st.time_input("Don't leave after", value=None, key="conn_dep_before")
                if dep_after_ui:
                    dep_after = dep_after_ui.strftime("%H:%M:%S")
                if dep_before_ui:
                    dep_before = dep_before_ui.strftime("%H:%M:%S")
        with col6:
            use_arr_time = st.checkbox("Restrict arrival time")
            arr_after = None
            arr_before = None
            if use_arr_time:
                arr_after_ui = st.time_input("Don't arrive before", value=None, key="conn_arr_after")
                arr_before_ui = st.time_input("Don't arrive after", value=None, key="conn_arr_before")
                if arr_after_ui:
                    arr_after = arr_after_ui.strftime("%H:%M:%S")
                if arr_before_ui:
                    arr_before = arr_before_ui.strftime("%H:%M:%S")

        col7, col8, col9 = st.columns(3)
        with col7:
            sort_choice = st.radio(
                "Sort results by",
                options=["Fastest total journey", "Shortest wait", "Earliest departure"],
                index=0,
            )
            sort_by = {
                "Fastest total journey": "fastest",
                "Shortest wait": "layover",
                "Earliest departure": "departure",
            }[sort_choice]
        with col8:
            exclude_overnight = st.checkbox(
                "Hide overnight waits",
                help="Hide connections with a long night wait at the mid station.",
            )
            max_results = st.number_input(
                "Max results",
                min_value=10,
                max_value=500,
                value=100,
                step=10,
            )
        with col9:
            view_mode = st.radio(
                "Results view",
                options=["Cards + table", "Cards only", "Table only"],
                index=0,
            )

    if st.button("Find Connections", type="primary", width="stretch"):
        if not start_station or not end_station:
            st.error("Please select both a Departure and Arrival station.")
        elif start_station == end_station:
            st.warning("Departure and Arrival stations cannot be the same.")
        else:
            with st.spinner("Searching connecting routes..."):
                s_code = extract_code(start_station)
                e_code = extract_code(end_station)
                v_code = extract_code(via_station) if use_via and via_station else None

                directs = find_directs_multi(
                    s_code,
                    e_code,
                    include_nearby=use_nearby,
                    search_date=search_date,
                    flexible_days=0,  # exact travel date only (not ±3)
                )
                results = find_connections_multi(
                    start_code=s_code,
                    end_code=e_code,
                    include_nearby=use_nearby,
                    via_code=v_code,
                    min_layover_hrs=min_wait,
                    max_layover_hrs=max_wait,
                    pref_dep_after=dep_after,
                    pref_dep_before=dep_before,
                    pref_arr_after=arr_after,
                    pref_arr_before=arr_before,
                    search_date=search_date,
                    sort_by=sort_by,
                    exclude_overnight=exclude_overnight,
                    max_results=int(max_results),
                )

            st.session_state.conn_results = results
            st.session_state.conn_meta = {
                "start_station": start_station,
                "end_station": end_station,
                "s_code": s_code,
                "e_code": e_code,
                "search_date": search_date,
                "quota": quota,
                "view_mode": view_mode,
                "nearby": use_nearby,
                "direct_count": 0 if directs is None or directs.empty else len(directs),
            }
            st.session_state.conn_directs = (
                None if directs is None or directs.empty else directs
            )
            st.session_state.show_direct_from_conn = False
            st.session_state.seat_cache = {}
            st.session_state.direct_seat_cache = {}

    meta = st.session_state.conn_meta or {}
    results = st.session_state.conn_results

    if results is None:
        st.markdown(
            """
            <div class="tc-steps">
              <div class="tc-step"><span class="n">1</span><b>Pick your stations</b>
                <span>Choose where you start and where you want to reach.</span></div>
              <div class="tc-step"><span class="n">2</span><b>Choose a date</b>
                <span>We only show trains that actually run that day.</span></div>
              <div class="tc-step"><span class="n">3</span><b>Check seats</b>
                <span>Open any route and see seats for both trains at once.</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    if results is not None:
        if meta.get("direct_count"):
            st.info(
                f"**{meta['direct_count']} direct train(s)** also run between these stations "
                "on your selected travel date (exact date — not flexible)."
            )
            if st.button(
                "View direct trains & check seats →",
                key="open_direct_from_conn",
                type="primary",
            ):
                st.session_state.show_direct_from_conn = True
                # Prefill Direct Trains tab for later
                st.session_state["direct_start"] = meta.get("start_station", "")
                st.session_state["direct_end"] = meta.get("end_station", "")
                if meta.get("search_date"):
                    st.session_state["direct_date_on"] = True
                    st.session_state["direct_date"] = meta["search_date"]
                    st.session_state["direct_flex_on"] = False  # same exact date as connections
                st.session_state.direct_results = st.session_state.get("conn_directs")
                st.session_state.direct_meta = {
                    "start_station": meta.get("start_station"),
                    "end_station": meta.get("end_station"),
                    "s_code": meta.get("s_code"),
                    "e_code": meta.get("e_code"),
                    "search_date": meta.get("search_date"),
                    "quota": meta.get("quota") or "GN",
                    "flexible": False,
                }
                st.rerun()

            if st.session_state.show_direct_from_conn and st.session_state.get("conn_directs") is not None:
                render_direct_train_cards(
                    st.session_state.conn_directs,
                    meta["s_code"],
                    meta["e_code"],
                    meta["start_station"],
                    meta["end_station"],
                    quota=meta.get("quota") or "GN",
                    key_prefix="conn_direct",
                )
                st.caption("Tip: the same search is also saved under the **Direct Trains** tab.")

        if results.empty:
            st.warning(
                "No connections found matching your filters. "
                "Try widening the wait time, allowing overnight waits, "
                    "or clearing time/date filters."
                )
        else:
            st.success(f"Found {len(results)} optimized connection(s)!")

            fastest_time = results["Total_Hrs"].min()
            best_layover = results["Layover_Hrs"].min()
            via_count = results["Via_Code"].nunique()

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Connections", len(results))
            m2.metric("Fastest", format_duration(fastest_time))
            m3.metric("Shortest wait", format_duration(best_layover))
            m4.metric("Via hubs", via_count)

            if meta.get("search_date"):
                unknown_count = (
                    (results["Train_1_Running_Days"] == "Unknown")
                    | (results["Train_2_Running_Days"] == "Unknown")
                ).sum()
                if unknown_count:
                    st.caption(
                        f"{unknown_count} route(s) include a train with no running-days "
                        "data — assumed to run; verify before booking."
                    )

            with st.expander("Top interchange hubs for this route"):
                hubs = cached_hubs(meta["s_code"], meta["e_code"], bool(meta.get("nearby")))
                if hubs.empty:
                    st.caption("No hub breakdown available.")
                else:
                    st.dataframe(hubs, width="stretch", hide_index=True)

            # ---------- fares, class filter, sorting, export ----------
            q_used = meta.get("quota") or "GN"
            s_date = meta.get("search_date")
            with st.container(border=True):
                st.markdown(
                    "<div class='tc-section-title'>Fares, class &amp; sorting</div>"
                    "<div class='tc-section-hint'>Pick a class to see only routes where BOTH trains have it. "
                    "Fares are the sum of Train 1 + Train 2 for that class.</div>",
                    unsafe_allow_html=True,
                )
                f1, f2, f3 = st.columns(3)
                conn_cls = f1.selectbox(
                    "Travel class", tt.CLASS_CHOICES, key="conn_cls",
                    help="SL = Sleeper, 3A/2A/1A = AC tiers, CC = AC Chair Car. 'Any' = cheapest class both trains share.",
                )
                conn_sort_label = f2.selectbox(
                    "Sort routes by", list(tt.SORT_CONNECTION), key="conn_sort_ui",
                    help="'Cheapest total fare' needs fares to be loaded first.",
                )
                conn_budget = f3.number_input(
                    "Max total fare (₹, 0 = no limit)", min_value=0, max_value=100000, step=100,
                    value=0, key="conn_budget",
                )

                keys = (
                    tt.connection_route_keys(results, meta["s_code"], meta["e_code"], s_date, q_used)
                    if s_date else []
                )
                missing = [k for k in keys if k not in st.session_state.route_cache]
                b1, b2 = st.columns([2, 3], vertical_alignment="center")
                load_label = (
                    "Load fares & classes" if missing and len(missing) == len(keys) else
                    "Load remaining fares" if missing else "Refresh fares"
                )
                if b1.button(load_label, key="conn_load_live", width="stretch", disabled=not s_date,
                             help="Looks up live fares/classes for every route (about 5–30 seconds)."):
                    todo = missing if missing else keys
                    bar = st.progress(0.0, text=f"Fetching {len(todo)} live lookups…")
                    fetched = fetch_many_routes(
                        todo, progress=lambda d, n: bar.progress(d / n, text=f"Fetched {d} of {n}…")
                    )
                    st.session_state.route_cache.update(fetched)
                    bar.empty()
                    failed = sum(1 for r in fetched.values() if not r.get("ok"))
                    if failed:
                        st.warning(f"{failed} of {len(fetched)} live lookups failed — those routes show no fare.")
                    st.rerun()
                if not s_date:
                    b2.caption("Choose a travel date and search again to load live fares.")
                elif keys:
                    b2.caption(f"{len(keys) - len(missing)} of {len(keys)} live lookups loaded.")

            annotated = tt.annotate_connections(
                results, st.session_state.route_cache, meta["s_code"], meta["e_code"], s_date, q_used, conn_cls
            )
            view, hidden = tt.filter_and_sort(
                annotated, cls=conn_cls, max_fare=int(conn_budget),
                sort=tt.SORT_CONNECTION[conn_sort_label],
            )
            summ = tt.view_summary(annotated)
            if tt.SORT_CONNECTION[conn_sort_label] == "fare" and summ["loaded"] == 0:
                st.info("Load fares first, then routes can be sorted by total fare.")
            if conn_cls != "Any":
                if hidden["class"]:
                    st.caption(f"Hid {hidden['class']} route(s) where a train has no {conn_cls} class.")
                if hidden["unverified"]:
                    st.caption(
                        f"{hidden['unverified']} route(s) are shown but not yet confirmed to offer {conn_cls} "
                        "(live data not loaded, or the train wasn't in the live list)."
                    )
            if hidden["closed"]:
                st.caption(f"Hid {hidden['closed']} route(s) where a train has already left or is charted (booking closed).")
            if hidden["budget"]:
                st.caption(f"Hid {hidden['budget']} route(s) over ₹{int(conn_budget):,} or with no fare.")
            if view.empty:
                st.warning("No routes match these filters. Try 'Any' class or a higher budget.")

            if not view.empty:
                x1, x2 = st.columns(2)
                x1.download_button(
                    "Download CSV",
                    data=tt.csv_bytes(
                        view, {"Travel_Date": s_date.isoformat() if s_date else "", "Quota": q_used}
                    ),
                    file_name=tt.csv_filename("connections", meta["s_code"], meta["e_code"], s_date),
                    mime="text/csv",
                    width="stretch",
                    key="conn_csv",
                    help="All routes currently shown, including fares if loaded.",
                )
                with x2.popover("Copy top routes as text", width="stretch"):
                    st.caption("Use the copy icon at the top-right of the box.")
                    st.code(
                        tt.routes_overview_text(
                            view.to_dict("records"), meta["start_station"], meta["end_station"], s_date
                        ),
                        language=None,
                    )

            view_mode = meta.get("view_mode", "Cards + table")
            if view_mode in ("Cards + table", "Cards only") and not view.empty:
                render_connection_cards(
                    view,
                    meta["start_station"],
                    meta["end_station"],
                    meta["s_code"],
                    meta["e_code"],
                    s_date,
                    q_used,
                    dark,
                    cls=conn_cls,
                )

            if view_mode in ("Cards + table", "Table only") and not view.empty:
                st.subheader("Full results table")
                st.dataframe(
                    view.drop(columns=["Fare_Data"], errors="ignore"),
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Fare_Class": st.column_config.TextColumn("Class"),
                        "Fare_Train_1": st.column_config.NumberColumn("Fare T1", format="₹%d"),
                        "Fare_Train_2": st.column_config.NumberColumn("Fare T2", format="₹%d"),
                        "Total_Fare": st.column_config.NumberColumn("Total fare", format="₹%d"),
                        "Seat_Train_1": st.column_config.TextColumn("Seat T1"),
                        "Seat_Train_2": st.column_config.TextColumn("Seat T2"),
                        "Total_Hrs": st.column_config.ProgressColumn(
                            "Total Journey (Hrs)",
                            help="Total time from start to finish",
                            format="%f",
                            min_value=0,
                            max_value=float(view["Total_Hrs"].max()),
                        ),
                        "Layover_Hrs": st.column_config.NumberColumn(
                            "Wait", help="Waiting time at mid-station", format="%.1f"
                        ),
                        "Leg1_Hrs": st.column_config.NumberColumn("Train 1 (Hrs)", format="%.1f"),
                        "Leg2_Hrs": st.column_config.NumberColumn("Train 2 (Hrs)", format="%.1f"),
                        "Comfort": st.column_config.TextColumn("Comfort"),
                        "Overnight_Layover": st.column_config.TextColumn("Overnight"),
                        "Train_1_Running_Days": st.column_config.TextColumn("Train 1 Runs On"),
                        "Train_2_Running_Days": st.column_config.TextColumn("Train 2 Runs On"),
                    },
                )

# =====================================================================================
# TAB 2: DIRECT TRAINS
# =====================================================================================
with tab_direct:
    st.subheader("Direct trains (no change)")
    st.markdown("See if you can go **start → end on a single train** before looking for split tickets.")

    d1, d2 = st.columns(2)
    with d1:
        d_start = st.selectbox("From", options=[""] + station_list, index=0, key="direct_start")
    with d2:
        d_end = st.selectbox("To", options=[""] + station_list, index=0, key="direct_end")

    d_nearby = st.checkbox(
        "Include nearby stations in the same city",
        value=True,
        key="direct_nearby",
        help="Also search from/to other stations of the same city, e.g. NDLS, NZM, DLI, ANVT for Delhi.",
    )
    if d_nearby and d_start and d_end:
        st.caption(nearby_caption(extract_code(d_start), extract_code(d_end)))

    with st.container(border=True):
        st.markdown(
            "<div class='tc-section-title'>Travel date</div>"
            "<div class='tc-section-hint'>The date you board at the From station.</div>",
            unsafe_allow_html=True,
        )
        d3, d4 = st.columns(2)
        with d3:
            d_use_date = st.toggle("Filter by travel date", value=True, key="direct_date_on")
            d_date = None
            d_flex = 0
            if d_use_date:
                d_date = st.date_input(
                    "Travel date",
                    value=datetime.date.today(),
                    min_value=datetime.date.today(),
                    key="direct_date",
                    label_visibility="collapsed",
                )
                d_flexible = st.toggle(
                    "Flexible dates (±3 days)",
                    value=True,
                    key="direct_flex_on",
                    help="Also show trains that work up to 3 days before or after your date.",
                )
                d_flex = 3 if d_flexible else 0
        with d4:
            d_sort = st.radio(
                "Sort by",
                options=["Fastest", "Earliest departure"],
                horizontal=True,
                key="direct_sort",
            )
            d_sort_by = "fastest" if d_sort == "Fastest" else "departure"
            d_quota = st.selectbox(
                "Quota (for seats)",
                options=["GN", "TQ", "PT", "LD", "SS"],
                index=0,
                help="GN = General, TQ = Tatkal, PT = Premium Tatkal, LD = Ladies, SS = Senior Citizen.",
                key="direct_quota",
            )

    if st.button("Search direct trains", type="primary", width="stretch", key="direct_btn"):
        if not d_start or not d_end:
            st.error("Select both stations.")
        elif d_start == d_end:
            st.warning("Stations must be different.")
        else:
            with st.spinner("Looking up direct trains..."):
                directs = find_directs_multi(
                    extract_code(d_start),
                    extract_code(d_end),
                    include_nearby=d_nearby,
                    search_date=d_date,
                    flexible_days=d_flex if d_use_date else 0,
                    sort_by=d_sort_by,
                )
            st.session_state.direct_results = directs
            st.session_state.direct_meta = {
                "start_station": d_start,
                "end_station": d_end,
                "s_code": extract_code(d_start),
                "e_code": extract_code(d_end),
                "search_date": d_date,
                "quota": d_quota,
                "flexible": bool(d_flex),
                "nearby": d_nearby,
            }
            st.session_state.direct_seat_cache = {}

    d_meta = st.session_state.direct_meta or {}
    directs = st.session_state.direct_results

    if directs is not None:
        if directs.empty:
            st.warning("No direct trains found. Try the Connections tab for routes with a change.")
        else:
            st.success(f"{len(directs)} direct train(s) found.")
            m1, m2 = st.columns(2)
            m1.metric("Trains", len(directs))
            m2.metric("Fastest", format_duration(directs["Duration_Hrs"].min()))

            if d_meta.get("search_date"):
                st.caption(
                    "Board on = date at your From station. "
                    "Leaves origin on = weekday the train starts from its first station."
                )

            dm_s = d_meta.get("s_code") or extract_code(d_start)
            dm_e = d_meta.get("e_code") or extract_code(d_end)
            dm_q = d_meta.get("quota") or d_quota

            with st.container(border=True):
                st.markdown(
                    "<div class='tc-section-title'>Fares &amp; class</div>"
                    "<div class='tc-section-hint'>Show one class only, and sort by fare.</div>",
                    unsafe_allow_html=True,
                )
                g1, g2, g3 = st.columns(3)
                dir_cls = g1.selectbox("Travel class", tt.CLASS_CHOICES, key="dir_cls",
                                       help="'Any' shows the cheapest class each train offers.")
                dir_sort_label = g2.selectbox("Order by", list(tt.SORT_DIRECT), key="dir_sort_ui")
                dir_budget = g3.number_input("Max fare (₹, 0 = no limit)", min_value=0, max_value=100000,
                                             step=100, value=0, key="dir_budget")

                dkeys = tt.direct_route_keys(directs, dm_s, dm_e, dm_q)
                dmissing = [k for k in dkeys if k not in st.session_state.route_cache]
                h1, h2 = st.columns([2, 3], vertical_alignment="center")
                if h1.button(
                    "Load fares & classes" if dmissing else "Refresh fares", key="dir_load_live",
                    width="stretch", disabled=not dkeys,
                    help="Looks up live fares/classes for these trains.",
                ):
                    todo = dmissing if dmissing else dkeys
                    bar = st.progress(0.0, text=f"Fetching {len(todo)} live lookups…")
                    fetched = fetch_many_routes(
                        todo, progress=lambda d, n: bar.progress(d / n, text=f"Fetched {d} of {n}…")
                    )
                    st.session_state.route_cache.update(fetched)
                    bar.empty()
                    failed = sum(1 for r in fetched.values() if not r.get("ok"))
                    if failed:
                        st.warning(f"{failed} of {len(fetched)} live lookups failed.")
                    st.rerun()
                if not dkeys:
                    h2.caption("Needs a travel date to look up live fares.")
                else:
                    h2.caption(f"{len(dkeys) - len(dmissing)} of {len(dkeys)} live lookups loaded.")

            d_annot = tt.annotate_directs(directs, st.session_state.route_cache, dm_s, dm_e, dm_q, dir_cls)
            d_view, d_hidden = tt.filter_and_sort(
                d_annot, cls=dir_cls, max_fare=int(dir_budget),
                sort=tt.SORT_DIRECT[dir_sort_label], fare_col="Fare",
            )
            if tt.SORT_DIRECT[dir_sort_label] == "fare" and tt.view_summary(d_annot)["loaded"] == 0:
                st.info("Load fares first, then trains can be ordered by fare.")
            if dir_cls != "Any" and d_hidden["class"]:
                st.caption(f"Hid {d_hidden['class']} train(s) that have no {dir_cls} class.")
            if d_hidden["closed"]:
                st.caption(f"Hid {d_hidden['closed']} train(s) that have already left or are charted (booking closed).")
            if d_hidden["budget"]:
                st.caption(f"Hid {d_hidden['budget']} train(s) over ₹{int(dir_budget):,} or with no fare.")

            if d_view.empty:
                st.warning("No trains match these filters. Try 'Any' class or a higher budget.")
            else:
                st.download_button(
                    "Download CSV",
                    data=tt.csv_bytes(d_view, {
                        "Quota": dm_q,
                    }),
                    file_name=tt.csv_filename("direct", dm_s, dm_e, d_meta.get("search_date")),
                    mime="text/csv", width="stretch", key="dir_csv",
                    help="All trains currently shown, including fares if loaded.",
                )
                render_direct_train_cards(
                    d_view,
                    dm_s,
                    dm_e,
                    d_meta.get("start_station") or d_start,
                    d_meta.get("end_station") or d_end,
                    quota=dm_q,
                    key_prefix="direct_tab",
                    cls=dir_cls,
                )

# =====================================================================================
# TAB 3: STATION EXPLORER
# =====================================================================================
with tab_station:
    st.subheader("Station explorer")
    st.markdown("See every train that stops at a station — useful when planning a change.")

    st_pick = st.selectbox("Station", options=[""] + station_list, index=0, key="station_pick")

    t1, t2 = st.columns(2)
    with t1:
        use_dep_window = st.checkbox("Filter by departure window", key="stn_win")
    dep_a = dep_b = None
    with t2:
        if use_dep_window:
            da = st.time_input("Depart after", value=datetime.time(6, 0), key="stn_after")
            db = st.time_input("Depart before", value=datetime.time(22, 0), key="stn_before")
            dep_a = da.strftime("%H:%M:%S") if da else None
            dep_b = db.strftime("%H:%M:%S") if db else None

    if st_pick and st.button("Show trains at station", type="primary", width="stretch"):
        code = extract_code(st_pick)
        with st.spinner("Loading trains..."):
            trains = get_trains_at_station(code, time_after=dep_a, time_before=dep_b)
        if trains.empty:
            st.warning("No trains found for this station / time window.")
        else:
            st.success(f"{len(trains)} train(s) stop at **{st_pick}**.")
            st.dataframe(trains, width="stretch", hide_index=True)

# =====================================================================================
# TAB 4: TRAIN LOOKUP
# =====================================================================================
with tab_lookup:
    st.subheader("Look up a train")
    st.markdown("Search by **train number** (e.g. `12213`) or **train name** (e.g. `duronto`).")

    query = st.text_input("Search train number or name", placeholder="e.g. 12213 or Duronto")

    selected_train_number = None

    if query:
        matches = cached_search_trains(query)
        if not matches:
            st.warning("No trains matched that search. Try a different number or name.")
        else:
            options = [f"{tn} — {name}" if name else tn for tn, name in matches]
            choice = st.selectbox("Matching trains", options=options, index=0)
            selected_train_number = choice.split(" — ")[0].strip()

    if selected_train_number:
        summary = get_train_summary(selected_train_number)
        if summary is None:
            st.error("Couldn't find schedule details for this train.")
        else:
            st.markdown("---")
            st.markdown(f"### {summary['train_number']} — {summary['train_name']}")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("From", summary["source"])
            c2.metric("To", summary["destination"])
            c3.metric("Total Stops", summary["total_stops"])
            c4.metric("Total Duration", format_duration(summary["total_duration_hrs"]))

            st.markdown("**Runs on:**")
            render_running_day_badges(summary["running_days_bits"], dark)

            st.link_button(
                "Open on IRCTC",
                summary.get("irctc_url") or irctc_train_url(selected_train_number),
                width="content",
            )

            st.markdown("---")
            st.markdown("**Full Schedule**")
            schedule = get_train_schedule(selected_train_number)
            st.dataframe(
                schedule,
                width="stretch",
                hide_index=True,
                column_config={
                    "Halt (min)": st.column_config.NumberColumn("Halt (min)", format="%.0f min"),
                },
            )

st.markdown("---")
st.info(
    """
**Notes**
- If running-day data is missing, the train is treated as running every day.
- Seat info comes from ConfirmTkt — always confirm on IRCTC before booking.
- Prices and seats can change; this app is only for planning.
"""
)

st.markdown(
    """
    <div class='tc-footer'>
        <p>Suggestions: <b>wazirnoob@gmail.com</b></p>
        <p>© 2026 Train Connector</p>
    </div>
    """,
    unsafe_allow_html=True,
)
