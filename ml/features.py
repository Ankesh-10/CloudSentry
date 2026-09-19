import pandas as pd
import numpy as np

def build_feature_vector(df: pd.DataFrame, resource_type: str) -> pd.DataFrame:
    """
    Engineers features from raw time-series metric dataframe.
    Expects df with columns: ['time', 'metric_name', 'value']
    Returns a single-row DataFrame (the latest point) with engineered features.
    """
    if df.empty:
        return pd.DataFrame()
        
    # Pivot so each metric is a column
    df['time'] = pd.to_datetime(df['time'])
    df_pivot = df.pivot(index='time', columns='metric_name', values='value').sort_index()
    
    # Fill forward then fill 0 for missing data
    df_pivot = df_pivot.ffill().fillna(0.0)
    
    features = pd.DataFrame(index=[df_pivot.index[-1]])
    
    # Base metrics check to prevent KeyError
    metrics = df_pivot.columns
    
    # Time of day feature
    features['time_of_day'] = df_pivot.index[-1].hour
    
    if resource_type == 'ec2':
        cpu = df_pivot['CPUUtilization'] if 'CPUUtilization' in metrics else pd.Series(0, index=df_pivot.index)
        net_in = df_pivot['NetworkIn'] if 'NetworkIn' in metrics else pd.Series(0, index=df_pivot.index)
        
        # CPU Features
        rolling_cpu_1h = cpu.rolling(window=12, min_periods=1).mean()
        rolling_cpu_24h = cpu.rolling(window=288, min_periods=1).mean()
        
        features['rolling_avg_cpu_1h'] = rolling_cpu_1h.iloc[-1]
        features['rolling_avg_cpu_24h'] = rolling_cpu_24h.iloc[-1]
        
        avg_24_cpu = rolling_cpu_24h.iloc[-1]
        features['cpu_deviation_ratio'] = cpu.iloc[-1] / avg_24_cpu if avg_24_cpu > 0 else 0.0
        
        # Network Features
        rolling_net_24h = net_in.rolling(window=288, min_periods=1).mean()
        avg_24_net = rolling_net_24h.iloc[-1]
        features['network_in_ratio'] = net_in.iloc[-1] / avg_24_net if avg_24_net > 0 else 0.0
        
    elif resource_type == 'lambda':
        invocations = df_pivot['Invocations'] if 'Invocations' in metrics else pd.Series(0, index=df_pivot.index)
        errors = df_pivot['Errors'] if 'Errors' in metrics else pd.Series(0, index=df_pivot.index)
        
        rolling_inv_24h = invocations.rolling(window=288, min_periods=1).mean()
        avg_24_inv = rolling_inv_24h.iloc[-1]
        
        features['invocation_spike_ratio'] = invocations.iloc[-1] / avg_24_inv if avg_24_inv > 0 else 0.0
        features['error_rate'] = errors.iloc[-1] / invocations.iloc[-1] if invocations.iloc[-1] > 0 else 0.0
        
    # Standardize output structure for IF model compatibility across resources
    expected_cols = [
        'time_of_day', 'rolling_avg_cpu_1h', 'rolling_avg_cpu_24h', 
        'cpu_deviation_ratio', 'network_in_ratio', 'invocation_spike_ratio', 
        'error_rate', 'hours_unattached', 'estimated_cost_per_hour'
    ]
    
    for col in expected_cols:
        if col not in features.columns:
            features[col] = 0.0
            
    return features[expected_cols]

def build_training_dataset(df: pd.DataFrame, resource_type: str) -> pd.DataFrame:
    """
    Engineers features for an entire historical dataset (e.g., 7 days of data).
    Returns a DataFrame containing all engineered rows suitable for training.
    """
    if df.empty:
        return pd.DataFrame()
        
    df['time'] = pd.to_datetime(df['time'])
    df_pivot = df.pivot(index='time', columns='metric_name', values='value').sort_index()
    df_pivot = df_pivot.ffill().fillna(0.0)
    
    features = pd.DataFrame(index=df_pivot.index)
    metrics = df_pivot.columns
    
    features['time_of_day'] = df_pivot.index.hour
    
    if resource_type == 'ec2':
        cpu = df_pivot['CPUUtilization'] if 'CPUUtilization' in metrics else pd.Series(0, index=df_pivot.index)
        net_in = df_pivot['NetworkIn'] if 'NetworkIn' in metrics else pd.Series(0, index=df_pivot.index)
        
        rolling_cpu_1h = cpu.rolling(window=12, min_periods=1).mean()
        rolling_cpu_24h = cpu.rolling(window=288, min_periods=1).mean()
        
        features['rolling_avg_cpu_1h'] = rolling_cpu_1h
        features['rolling_avg_cpu_24h'] = rolling_cpu_24h
        
        # Avoid division by zero
        safe_avg_24_cpu = rolling_cpu_24h.replace(0, np.nan)
        features['cpu_deviation_ratio'] = (cpu / safe_avg_24_cpu).fillna(0.0)
        
        rolling_net_24h = net_in.rolling(window=288, min_periods=1).mean()
        safe_avg_24_net = rolling_net_24h.replace(0, np.nan)
        features['network_in_ratio'] = (net_in / safe_avg_24_net).fillna(0.0)
        
    elif resource_type == 'lambda':
        invocations = df_pivot['Invocations'] if 'Invocations' in metrics else pd.Series(0, index=df_pivot.index)
        errors = df_pivot['Errors'] if 'Errors' in metrics else pd.Series(0, index=df_pivot.index)
        
        rolling_inv_24h = invocations.rolling(window=288, min_periods=1).mean()
        safe_avg_24_inv = rolling_inv_24h.replace(0, np.nan)
        
        features['invocation_spike_ratio'] = (invocations / safe_avg_24_inv).fillna(0.0)
        safe_invocations = invocations.replace(0, np.nan)
        features['error_rate'] = (errors / safe_invocations).fillna(0.0)
        
    expected_cols = [
        'time_of_day', 'rolling_avg_cpu_1h', 'rolling_avg_cpu_24h', 
        'cpu_deviation_ratio', 'network_in_ratio', 'invocation_spike_ratio', 
        'error_rate', 'hours_unattached', 'estimated_cost_per_hour'
    ]
    
    for col in expected_cols:
        if col not in features.columns:
            features[col] = 0.0
            
    # Drop the first 24 hours of rows since their 24h rolling averages won't be fully stable
    # 24 hours * 12 points/hour = 288 rows
    if len(features) > 288:
        features = features.iloc[288:]
        
    return features[expected_cols]
