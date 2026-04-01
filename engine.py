import pandas as pd

# Load data once to make the app lightning fast
df1 = pd.read_csv('trains.csv', dtype=str)
df2 = pd.read_csv('train_schedule.csv', dtype=str)

# Clean up data globally
df2['arrival'] = df2['arrival'].fillna('00:00:00')
df2['departure'] = df2['departure'].fillna('00:00:00')
df2['day'] = pd.to_numeric(df2['day'])
df2['stop_seq'] = df2.groupby('train_number').cumcount()
df2['abs_arrival'] = pd.to_timedelta(df2['day'] - 1, unit='D') + pd.to_timedelta(df2['arrival'])
df2['abs_departure'] = pd.to_timedelta(df2['day'] - 1, unit='D') + pd.to_timedelta(df2['departure'])

# Create a list of perfectly formatted stations for the UI dropdowns
# It creates strings like: "NEW DELHI (NDLS)" so users can search by name OR code
# Create the station list, but safely drop any empty (NaN) rows first!
raw_stations = (df2['station_name'] + " (" + df2['station_code'] + ")").dropna()
station_list = sorted(list(set(raw_stations)))

def find_connections_pro(start_code, end_code, via_code=None, 
                         min_layover_hrs=1, max_layover_hrs=12,
                         pref_dep_after=None, pref_dep_before=None,
                         pref_arr_after=None, pref_arr_before=None):
    
    # 1. LEG 1
    starts = df2[df2['station_code'] == start_code][['train_number', 'stop_seq', 'abs_departure', 'departure', 'train_name']]
    starts = starts.rename(columns={'stop_seq': 'start_seq', 'abs_departure': 'abs_start_dep', 'departure': 'start_time', 'train_name': 'train1_name'})
    
    if pref_dep_after is not None: starts = starts[starts['start_time'] >= pref_dep_after]
    if pref_dep_before is not None: starts = starts[starts['start_time'] <= pref_dep_before]
        
    leg1 = pd.merge(starts, df2, on='train_number')
    leg1 = leg1[leg1['stop_seq'] > leg1['start_seq']] 
    leg1['t1_duration_hrs'] = (leg1['abs_arrival'] - leg1['abs_start_dep']).dt.total_seconds() / 3600
    leg1 = leg1[leg1['t1_duration_hrs'] > 0] 
    
    leg1 = leg1[['train_number', 'train1_name', 'station_code', 'station_name', 'start_time', 'arrival', 't1_duration_hrs']]
    leg1 = leg1.rename(columns={'train_number': 'train1', 'arrival': 'arr_at_c', 'station_code': 'station_c', 'start_time': 'dep_from_a'})

    # 2. LEG 2
    ends = df2[df2['station_code'] == end_code][['train_number', 'stop_seq', 'abs_arrival', 'arrival', 'train_name']]
    ends = ends.rename(columns={'stop_seq': 'end_seq', 'abs_arrival': 'abs_end_arr', 'arrival': 'end_time', 'train_name': 'train2_name'})
    
    if pref_arr_after is not None: ends = ends[ends['end_time'] >= pref_arr_after]
    if pref_arr_before is not None: ends = ends[ends['end_time'] <= pref_arr_before]
        
    leg2 = pd.merge(ends, df2, on='train_number')
    leg2 = leg2[leg2['stop_seq'] < leg2['end_seq']] 
    leg2['t2_duration_hrs'] = (leg2['abs_end_arr'] - leg2['abs_departure']).dt.total_seconds() / 3600
    leg2 = leg2[leg2['t2_duration_hrs'] > 0] 
    
    leg2 = leg2[['train_number', 'train2_name', 'station_code', 'departure', 'end_time', 't2_duration_hrs']]
    leg2 = leg2.rename(columns={'train_number': 'train2', 'departure': 'dep_from_c', 'station_code': 'station_c', 'end_time': 'arr_at_b'})

    # 3. INTERSECTIONS
    connections = pd.merge(leg1, leg2, on='station_c')
    connections = connections[connections['train1'] != connections['train2']]
    connections = connections[connections['train1_name'] != connections['train2_name']]

    if via_code is not None:
        connections = connections[connections['station_c'] == via_code]

    # 4. MATH
    if connections.empty: return pd.DataFrame()

    connections['arr_time'] = pd.to_timedelta(connections['arr_at_c'])
    connections['dep_time'] = pd.to_timedelta(connections['dep_from_c'])
    next_day_mask = connections['dep_time'] < connections['arr_time']
    connections.loc[next_day_mask, 'dep_time'] += pd.Timedelta(days=1)
    
    connections['layover_hours'] = (connections['dep_time'] - connections['arr_time']).dt.total_seconds() / 3600
    connections['total_journey_hrs'] = connections['t1_duration_hrs'] + connections['layover_hours'] + connections['t2_duration_hrs']

    # 5. CLEANUP
    valid_routes = connections[(connections['layover_hours'] >= min_layover_hrs) & (connections['layover_hours'] <= max_layover_hrs)].copy()
    if valid_routes.empty: return pd.DataFrame()

    valid_routes = valid_routes.sort_values('total_journey_hrs')
    valid_routes = valid_routes.drop_duplicates(subset=['train1', 'train2'], keep='first')
    
    final_output = valid_routes[['station_name', 'train1', 'train1_name', 'dep_from_a', 'arr_at_c', 
                                 'train2', 'train2_name', 'dep_from_c', 'arr_at_b', 'layover_hours', 'total_journey_hrs']].copy()
    
    final_output = final_output.rename(columns={
        'station_name': 'Via_Station', 'train1': 'Train_1_No', 'train1_name': 'Train_1_Name',
        'dep_from_a': 'Leave_Start', 'arr_at_c': 'Arrive_Mid', 'train2': 'Train_2_No',
        'train2_name': 'Train_2_Name', 'dep_from_c': 'Leave_Mid', 'arr_at_b': 'Arrive_End',
        'layover_hours': 'Layover_Hrs', 'total_journey_hrs': 'Total_Hrs'
    })
    
    final_output['Layover_Hrs'] = final_output['Layover_Hrs'].round(1)
    final_output['Total_Hrs'] = final_output['Total_Hrs'].round(1)
    
    return final_output