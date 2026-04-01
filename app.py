import streamlit as st
import pandas as pd
from engine import find_connections_pro, station_list

# Helper function to extract just the code from "NEW DELHI (NDLS)"
def extract_code(station_string):
    if station_string:
        return station_string.split("(")[-1].replace(")", "").strip()
    return None

# --- UI SETUP ---
st.set_page_config(page_title="Train Connector", layout="wide")
st.title("🚆 Find Connecting trains")
st.markdown("Find the best split-ticket routes when direct trains are full.")

# --- COMPULSORY INPUTS (Using native autocomplete) ---
col1, col2 = st.columns(2)
with col1:
    start_station = st.selectbox(
        "Start Station (Compulsory)", 
        options=[""] + station_list, 
        index=0,
        help="Type or select the city/station you are starting your journey from."
    )
with col2:
    end_station = st.selectbox(
        "End Station (Compulsory)", 
        options=[""] + station_list, 
        index=0,
        help="Type or select your final destination."
    )

st.markdown("---")

# --- OPTIONAL FILTERS (Using Checkboxes) ---
st.subheader("⚙️ Advanced Filters")
col3, col4 = st.columns(2)

# Checkbox 1: Via Station
# Checkbox 1: Via Station
# Checkbox 1: Via Station
with col3:
    use_via = st.checkbox(
        "Route via specific Mid-Station",
        help="If you leave this unchecked, the system will automatically search ALL possible intersecting stations for the best route."
    )
    via_station = None
    if use_via:
        via_station = st.selectbox(
            "Select Mid-Station", 
            options=[""] + station_list, 
            index=0,
            help="Type or select the exact station you want to transfer at."
        )

# Checkbox 2: Max Wait Time
# Checkbox 2: Max Wait Time
with col4:
    use_max_wait = st.checkbox(
        "Set Maximum Waiting Time (Layover)",
        help="Check this to set a strict limit on how long you wait at the connecting station. If left unchecked, the system allows up to 12 hours of waiting time by default."
    )
    max_wait = 12 # Default
    if use_max_wait:
        max_wait = st.slider(
            "Max Layover (Hours)", 
            min_value=1, 
            max_value=24, 
            value=8,
            help="Slide to choose your absolute maximum waiting time."
        )

col5, col6 = st.columns(2)

# Checkbox 3: Departure Time Limits
with col5:
    use_dep_time = st.checkbox(
        "Restrict Departure Time",
        help="Check this if you only want to see trains that leave your starting station during a specific time of day (e.g., after 5:00 PM)."
    )
    dep_after = None
    dep_before = None
    if use_dep_time:
        dep_after_ui = st.time_input("Don't leave before", value=None)
        dep_before_ui = st.time_input("Don't leave after", value=None)
        if dep_after_ui: dep_after = dep_after_ui.strftime("%H:%M:%S")
        if dep_before_ui: dep_before = dep_before_ui.strftime("%H:%M:%S")

# Checkbox 4: Arrival Time Limits
with col6:
    use_arr_time = st.checkbox(
        "Restrict Arrival Time",
        help="Check this if you need to reach your final destination before or after a specific time."
    )
    arr_after = None
    arr_before = None
    if use_arr_time:
        arr_after_ui = st.time_input("Don't arrive before", value=None)
        arr_before_ui = st.time_input("Don't arrive after", value=None)
        if arr_after_ui: arr_after = arr_after_ui.strftime("%H:%M:%S")
        if arr_before_ui: arr_before = arr_before_ui.strftime("%H:%M:%S")

st.markdown("---")

# --- SEARCH BUTTON & RESULTS ---
if st.button("🔍 Find Connections", type="primary", use_container_width=True):
    if not start_station or not end_station:
        st.error("Please select both a Departure and Arrival station.")
    elif start_station == end_station:
        st.warning("Departure and Arrival stations cannot be the same.")
    else:
        with st.spinner('Calculating millions of combinations...'):
            # Extract codes
            s_code = extract_code(start_station)
            e_code = extract_code(end_station)
            v_code = extract_code(via_station) if use_via and via_station else None
            
            # Run the engine
            results = find_connections_pro(
                start_code=s_code, 
                end_code=e_code, 
                via_code=v_code,
                max_layover_hrs=max_wait,
                pref_dep_after=dep_after,
                pref_dep_before=dep_before,
                pref_arr_after=arr_after,
                pref_arr_before=arr_before
            )
            
            if not results.empty:
                st.success(f"Found {len(results)} optimized connections!")
    
    # Get the fastest and shortest layover from the data
                fastest_time = results['Total_Hrs'].min()
                best_layover = results['Layover_Hrs'].min()
    
    # Create 3 professional metric cards
                m1, m2, m3 = st.columns(3)
                m1.metric("Total Connections", len(results))
                m2.metric("⚡ Fastest Route", f"{fastest_time} hrs")
                m3.metric("⏱️ Shortest Wait", f"{best_layover} hrs")
    
                st.markdown("---") # Divider
                st.dataframe(
                    results,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Total_Hrs": st.column_config.ProgressColumn(
                        "Total Journey (Hrs)",
                        help="Total time from start to finish",
                        format="%f",
                        min_value=0,
                        max_value=results['Total_Hrs'].max(),
                    ),
                    "Layover_Hrs": st.column_config.NumberColumn(
                    "Layover",
                    help="Waiting time at mid-station",
                    format="%.1f ⏳"
                )
            }
            )
            else:
                st.success(f"Found {len(results)} optimized connections!")
                st.dataframe(results, use_container_width=True, hide_index=True)

# --- NEW FOOTER CODE ADDED HERE ---
# --- NEW FOOTER CODE ADDED HERE ---
st.markdown("---")
st.info("""
📌 **Technical Notes & Disclaimer:**
* The train schedule data is currently dated up to **2023**.
* **Daily Running Assumption:** This app currently assumes every train runs every day. In reality, some trains may only run on specific days of the week. Please cross-verify the running schedule on the official IRCTC website before booking.
* The application is undergoing upgrades. Please wait for further updates!
""")

# Creator Signature & Copyright
st.markdown(
    """
    <div style='text-align: center; color: gray; padding-top: 20px;'>
        <p>If you want to build something helpful & crazy & wanna suggest features , mail me at: <b>wazirnoob@gmail.com</b></p>
        <p>© 2026 All Rights Reserved.</p>
    </div>
    """, 
    unsafe_allow_html=True
)