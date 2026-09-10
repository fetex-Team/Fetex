"""고정 5분 격자와 과거 관측만 사용하는 예측 피처."""
import numpy as np
import pandas as pd
from config_loader import CFG


class TimeSeriesPreprocessor:
    def __init__(self, freq=None, max_lag=None):
        self.freq = freq or CFG['freq']
        self.max_lag = max_lag if max_lag is not None else CFG['max_lag']
        if self.freq != '5min' or self.max_lag < 1:
            raise ValueError('30분 예측은 freq=5min, max_lag>=1이어야 합니다.')

    def aggregate_demands(self, df, timestamp_col='pickup_datetime', spatial_col='h3_index',
                          cells=None, start=None, end=None):
        data = df.copy()
        data[timestamp_col] = pd.to_datetime(data[timestamp_col])
        if data[timestamp_col].isna().any() or data[spatial_col].isna().any():
            raise ValueError('호출 시간과 공간 인덱스에 결측치가 있습니다.')
        cells = sorted(cells if cells is not None else data[spatial_col].unique())
        start = pd.Timestamp(start) if start is not None else data[timestamp_col].min().floor(self.freq)
        end = pd.Timestamp(end) if end is not None else data[timestamp_col].max().floor(self.freq) + pd.Timedelta(self.freq)
        if not cells or pd.isna(start) or pd.isna(end) or end <= start:
            raise ValueError('빈 호출 자료에는 유효한 cells/start/end 범위를 지정하세요.')
        if start.floor(self.freq) != start or end.floor(self.freq) != end:
            raise ValueError('집계 범위는 5분 경계에 맞춰 지정하세요.')
        data = data.loc[(data[timestamp_col] >= start) & (data[timestamp_col] < end)]
        data['time_bucket'] = data[timestamp_col].dt.floor(self.freq)
        # 호출이 없는 지역·시간도 0으로 남겨 lag가 정확히 5분 간격을 뜻하게 한다.
        index = pd.MultiIndex.from_product([pd.date_range(start, end, freq=self.freq, inclusive='left'), cells],
                                          names=['time_bucket', spatial_col])
        counts = data.groupby(['time_bucket', spatial_col]).size()
        return counts.reindex(index, fill_value=0).rename('demand').reset_index()

    def create_features(self, demand_df, spatial_col='h3_index'):
        frame = demand_df.sort_values([spatial_col, 'time_bucket']).copy()
        grouped = frame.groupby(spatial_col)['demand']
        # time_bucket은 완료된 관측 구간이다. 현재 구간 수요도 다음 구간 예측에 사용 가능하다.
        frame['observed_demand'] = frame['demand']
        for lag in range(1, self.max_lag + 1):
            frame[f'lag_{lag}'] = grouped.shift(lag)
        for window in sorted({CFG['rolling_short'], CFG['rolling_long']}):
            frame[f'rolling_mean_{window}'] = grouped.transform(lambda s: s.rolling(window).mean())
        t = frame['time_bucket'].dt
        for name in ('year', 'month', 'day', 'hour', 'minute', 'dayofweek'):
            frame[name] = getattr(t, name)
        frame['is_weekend'] = (frame['dayofweek'] >= 5).astype(int)
        frame['hour_sin'] = np.sin(2 * np.pi * (t.hour + t.minute / 60) / 24)
        frame['hour_cos'] = np.cos(2 * np.pi * (t.hour + t.minute / 60) / 24)
        # 문자열 지역 ID 대신 셀 중심 좌표를 사용해 지역 차이를 모델에 전달한다.
        import h3
        centers = {cell: h3.cell_to_latlng(cell) for cell in frame[spatial_col].unique()}
        frame['cell_lat'] = frame[spatial_col].map(lambda cell: centers[cell][0])
        frame['cell_lng'] = frame[spatial_col].map(lambda cell: centers[cell][1])
        return frame.sort_values(['time_bucket', spatial_col]).reset_index(drop=True)


def make_supervised(frame, horizon=6):
    """t의 완료된 관측으로 t+1...t+6을 예측한다. 끝부분의 미관측 타깃은 제거한다."""
    if horizon != 6:
        raise ValueError('이 미션의 예측 타깃은 5분 단위 6개입니다.')
    frame = frame.sort_values(['h3_index', 'time_bucket']).copy()
    features = [c for c in frame if c not in ('time_bucket', 'h3_index', 'demand')]
    for step in range(1, horizon + 1):
        frame[f'target_{step}'] = frame.groupby('h3_index')['demand'].shift(-step)
    targets = [f'target_{step}' for step in range(1, horizon + 1)]
    return frame.dropna(subset=features + targets).sort_values(['time_bucket', 'h3_index']).reset_index(drop=True), features, targets


def chronological_split(frame, validation_size=0.2, test_size=0.2):
    if not 0 < validation_size < 1 or not 0 < test_size < 1 or validation_size + test_size >= 1:
        raise ValueError('학습·검증·테스트 비율을 확인하세요.')
    times = np.sort(frame.time_bucket.unique())
    if len(times) < 30:
        raise ValueError('시간 분할에 필요한 관측 구간이 부족합니다.')
    val_start = pd.Timestamp(times[int(len(times) * (1 - validation_size - test_size))])
    test_start = pd.Timestamp(times[int(len(times) * (1 - test_size))])
    target_end = frame.time_bucket + pd.Timedelta(minutes=30)
    # 학습/검증 타깃이 다음 분할의 관측 시점까지 넘어가지 않도록 30분을 비운다.
    train = frame.loc[target_end < val_start]
    validation = frame.loc[(frame.time_bucket >= val_start) & (target_end < test_start)]
    test = frame.loc[frame.time_bucket >= test_start]
    if any(part.empty for part in (train, validation, test)):
        raise ValueError('분할 후 빈 데이터셋이 있습니다. 데이터 기간을 늘리세요.')
    return train, validation, test
