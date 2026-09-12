"""시간·지역 키로 외부 관측을 결합한다. 미래 값으로 결측치를 채우지 않는다."""
import pandas as pd

EXTERNAL_COLUMNS = ['temperature', 'precipitation', 'traffic_index', 'event_flag', 'is_holiday']


def merge_external_data(df, external=None):
    if external is None:
        raise ValueError('시간·H3별 외부 데이터가 필요합니다. 합성 자료도 명시적으로 전달하세요.')
    frame, external = df.copy(), external.copy()
    keys = ['time_bucket', 'h3_index']
    for table in (frame, external):
        table['time_bucket'] = pd.to_datetime(table['time_bucket'])
    if external.duplicated(keys).any():
        raise ValueError('외부 데이터의 시간·지역 키가 중복되었습니다.')
    if not set(EXTERNAL_COLUMNS).issubset(external):
        raise ValueError(f'외부 데이터에 필요한 열: {EXTERNAL_COLUMNS}')
    frame = frame.merge(external[keys + EXTERNAL_COLUMNS], on=keys, how='left', validate='one_to_one')
    frame = frame.sort_values(['h3_index', 'time_bucket'])
    for col in EXTERNAL_COLUMNS:
        frame[col] = pd.to_numeric(frame[col], errors='raise')
        if frame[col].isin([float('inf'), float('-inf')]).any():
            raise ValueError(f'{col}에 유한하지 않은 관측이 있습니다.')
        frame[col + '_missing'] = frame[col].isna().astype(int)
        # 선행 관측만 전달하며 최초 미관측은 명시적인 0+missing 표시를 사용한다.
        frame[col] = frame.groupby('h3_index')[col].ffill().fillna(0)
    return frame.sort_values(keys).reset_index(drop=True)
