"""지역별 수요/빈 택시 수로 제한된 범위의 인센티브 배수를 계산한다."""
import math
from config_loader import CFG


class SurgePricingEngine:
    def __init__(self, base_fare=None, min_multiplier=None, max_multiplier=None):
        self.base_fare = CFG['base_fare'] if base_fare is None else base_fare
        self.min_multiplier = CFG['min_multiplier'] if min_multiplier is None else min_multiplier
        self.max_multiplier = CFG['max_multiplier'] if max_multiplier is None else max_multiplier
        self.surge_coefficient = CFG['surge_coefficient']
        if not all(math.isfinite(v) for v in (self.base_fare, self.min_multiplier, self.max_multiplier, self.surge_coefficient)) or self.base_fare < 0 or not 0 < self.min_multiplier <= self.max_multiplier or self.surge_coefficient < 0:
            raise ValueError('요금·배수·계수 범위를 확인하세요.')

    def calculate_imbalance(self, demand, supply):
        if not all(math.isfinite(v) and v >= 0 for v in (demand, supply)):
            raise ValueError('수요와 공급은 유한한 0 이상의 값이어야 합니다.')
        # 공급 0도 JSON/CSV에 기록 가능한 유한한 지표로 표현한다.
        return demand / max(supply, 1)

    def get_multiplier(self, imbalance_ratio):
        if not math.isfinite(imbalance_ratio) or imbalance_ratio < 0:
            raise ValueError('불균형 지표가 유효하지 않습니다.')
        return min(self.max_multiplier, max(self.min_multiplier, 1 + max(0, imbalance_ratio - 1) * self.surge_coefficient))

    def apply_surge_pricing(self, df):
        frame = df.copy()
        frame['imbalance_ratio'] = [self.calculate_imbalance(d, s) for d, s in zip(frame.predicted_demand, frame.available_taxis)]
        frame['surge_multiplier'] = frame.imbalance_ratio.map(self.get_multiplier)
        frame['final_fare'] = (self.base_fare * frame.surge_multiplier).round().astype(int)
        return frame
