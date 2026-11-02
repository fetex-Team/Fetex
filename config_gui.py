"""
AI Mobility 디지털 트윈 통합 설정 GUI (Config GUI).
- 유니티 3D 시연 및 일반 시뮬레이션을 위한 통합 관제탑 역할을 수행합니다.
- 메인 탭에 핵심 파라미터와 원클릭 시나리오 프리셋을 배치하고, 세부 인구/알고리즘 모수는 고급 탭으로 분리하여 가독성과 조작성을 극대화합니다.
- GUI의 모든 설정값은 config.json에 저장되어 모든 모듈(전처리, 예측, 배차, 시뮬레이션)에 100% 동기화됩니다.
"""
import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

ROOT = os.path.dirname(os.path.abspath(__file__))
PRESET_DIR = os.path.join(ROOT, "presets")
os.makedirs(PRESET_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(ROOT, "config.json")

from config_loader import DEFAULT_CONFIG
from region_autocomplete import RegionSearchEntry

FREQ_OPTIONS = ["1min", "5min", "10min", "15min", "30min"]
STRATEGIES = ["patrol", "prepositioned"]
DISPATCH_ALGORITHMS = ["traci", "greedy", "hungarian", "routeExtension", "rl_reposition"]
IDLE_ALGORITHMS = ["randomCircling", "stopOnRoad", "taxiStop"]
DEMAND_SOURCES = ["synthetic", "real"]
HOLIDAY_MODES = ["auto", "weekday", "holiday"]

FACTOR_OPTIONS = {
    "taxi_strategy": ("택시 전략", STRATEGIES),
    "taxi_dispatch_algorithm": ("배차 알고리즘", DISPATCH_ALGORITHMS),
}

# -------------------------------------------------------------
# 1. 메인 탭: 자주 조작하는 핵심 파라미터 (Quick Controls)
# key, label, min, max, decimals, integer
# -------------------------------------------------------------
CORE_NUMERICS = [
    # 시뮬레이션 규모
    ("num_taxis", "택시 대수 (Fleet Size)", 1, 200, 0, 1),
    ("num_passengers", "기본 승객 수 (Passenger Pool)", 10, 10000, 0, 1),
    ("num_normal_cars", "일반 차량 대수 (Background Traffic)", 0, 300, 0, 1),
    ("sim_start_hour", "시뮬레이션 시작 시각 (Hour)", 0, 23, 0, 1),
    ("sim_end_hour", "시뮬레이션 종료 시각 (Hour)", 1, 24, 0, 1),
    ("passenger_wait_timeout", "승객 최대 대기시간 (초)", 60, 3600, 0, 1),
    # 전처리 & 예측
    ("h3_resolution", "H3 공간 해상도 (Resolution)", 6, 10, 0, 1),
    ("xgb_n_estimators", "XGBoost 트리 개수 (n_estimators)", 10, 500, 0, 1),
    ("xgb_max_depth", "XGBoost 최대 깊이 (max_depth)", 2, 16, 0, 1),
    ("xgb_learning_rate", "XGBoost 학습률 (learning_rate)", 0.01, 0.5, 3, 0),
    ("cnn_epochs", "CNN-LSTM 에포크 수 (epochs)", 1, 200, 0, 1),
    # 가격 정책
    ("base_fare", "택시 기본요금 (원)", 1000, 20000, 0, 1),
    ("max_multiplier", "최대 할증 배수 (Max Multiplier)", 1.0, 5.0, 1, 0),
    ("surge_coefficient", "할증 증가 계수 (Surge Coeff)", 0.1, 2.0, 2, 0),
]

# -------------------------------------------------------------
# 2. 고급 탭: 시뮬레이션 세부 인구 & 스케줄 모수 (Advanced Sim)
# -------------------------------------------------------------
ADVANCED_SIM_NUMERICS = [
    ("grid_x", "그리드 가로 블록 수", 2, 20, 0, 1),
    ("grid_y", "그리드 세로 블록 수", 2, 20, 0, 1),
    ("grid_length", "블록 한 변 길이(m)", 10, 1000, 0, 1),
    ("num_auto_cars", "자율주행차 대수", 0, 100, 0, 1),
    ("num_obstacles", "장애물 개수", 0, 100, 0, 1),
    ("depart_jitter_sec", "출발시간 분산 표준편차(초)", 0, 1800, 1, 0),
    ("sumo_time_to_teleport", "SUMO teleport 기준(초)", 0, 7200, 0, 1),
    ("taxi_remaining_edges_threshold", "택시 재타겟팅 잔여 edge 기준", 0, 20, 0, 1),
    ("taxi_target_pick_attempts", "목적지 탐색 시도 횟수", 1, 100, 0, 1),
    ("taxi_fail_threshold", "택시 고립 판정 연속 실패 횟수", 1, 100, 0, 1),
    ("taxi_primary_prob", "프리포지션 1순위 핫스팟 비율", 0, 1, 2, 0),
    ("school_pop_base", "학교 기본 인구 모수", 0, 10000, 0, 1),
    ("company_pop_base", "회사 기본 인구 모수", 0, 10000, 0, 1),
    ("residential_base_pop_per_edge", "주거 edge당 기본 인구", 0, 10000, 0, 1),
    ("residential_schedule_scale", "주거 스케줄 전체 배율", 0, 1, 3, 0),
    ("residential_taxi_probability", "기본승객수 독립가챠 택시확률", 0, 1, 3, 0),
    ("residential_out_hour", "주거 외출 시각", 0, 24, 2, 0),
    ("residential_out_probability", "주거 외출 확률", 0, 1, 3, 0),
    ("residential_evening_in_hour", "주거 저녁 귀가 시각", 0, 24, 2, 0),
    ("residential_evening_in_probability", "주거 저녁 귀가 확률", 0, 1, 3, 0),
    ("residential_late_in_hour", "주거 늦은 귀가 시각", 0, 24, 2, 0),
    ("residential_late_in_probability", "주거 늦은 귀가 확률", 0, 1, 3, 0),
    ("school_start_hour", "학교 등교 시작", 0, 23.99, 2, 0),
    ("school_end_hour", "학교 등교 종료", 0, 24, 2, 0),
    ("school_taxi_peak_hour", "학교 택시확률 peak", 0, 24, 2, 0),
    ("school_afternoon_start_hour", "학교 하교 시작", 0, 24, 2, 0),
    ("school_afternoon_end_hour", "학교 하교 종료", 0, 24, 2, 0),
    ("school_afternoon_fraction", "학교 하교 비율", 0, 1, 3, 0),
    ("company_start_hour", "회사 출근 시작", 0, 24, 2, 0),
    ("company_end_hour", "회사 출근 종료", 0, 24, 2, 0),
    ("company_taxi_peak_hour", "회사 택시확률 peak", 0, 24, 2, 0),
    ("lunch_start_hour", "점심 시작", 0, 24, 2, 0),
    ("lunch_end_hour", "점심 종료", 0, 24, 2, 0),
    ("lunch_company_release_fraction", "회사 점심 이탈 비율", 0, 1, 3, 0),
    ("lunch_taxi_fraction", "점심 택시 비율", 0, 1, 3, 0),
    ("evening_start_hour", "퇴근 시작", 0, 24, 2, 0),
    ("evening_end_hour", "퇴근 주요시간 종료", 0, 24, 2, 0),
    ("evening_taxi_fraction", "퇴근 택시 비율", 0, 1, 3, 0),
    ("late_evening_start_hour", "늦은 퇴근 시작", 0, 24, 2, 0),
    ("late_evening_end_hour", "늦은 퇴근 종료", 0, 24, 2, 0),
    ("evening_residential_fraction", "퇴근 목적지 주거 비율", 0, 1, 3, 0),
    ("restaurant_stay_sec", "음식점 체류시간(초)", 0, 86400, 0, 1),
    ("restaurant_pop_base", "음식점 건물당 인구", 0, 1000, 0, 1),
    ("restaurant_window1_start", "음식점 시간대1 시작", 0, 24, 2, 0),
    ("restaurant_window1_end", "음식점 시간대1 종료", 0, 24, 2, 0),
    ("restaurant_window2_start", "음식점 시간대2 시작", 0, 24, 2, 0),
    ("restaurant_window2_end", "음식점 시간대2 종료", 0, 24, 2, 0),
    ("restaurant_civilian_taxi_probability", "음식점 시민 택시확률", 0, 1, 3, 0),
    ("department_pop_base", "백화점 한 곳당 인구", 0, 5000, 0, 1),
    ("department_window_start", "백화점 방문 시작", 0, 24, 2, 0),
    ("department_window_end", "백화점 방문 종료", 0, 24, 2, 0),
    ("department_taxi_probability", "백화점 택시확률", 0, 1, 3, 0),
    ("department_stay_sec", "백화점 체류시간(초)", 0, 86400, 0, 1),
    ("weather_rain_taxi_bonus", "비 올 때 택시확률 가산", -0.5, 0.5, 3, 0),
    ("weather_cool_taxi_bonus", "쌀쌀할 때 택시확률 가산", -0.5, 0.5, 3, 0),
    ("weather_sunny_taxi_bonus", "맑을 때 택시확률 가산", -0.5, 0.5, 3, 0),
    ("weather_rain_threshold_mm", "비 판정 강수량(mm)", 0, 50, 2, 0),
    ("weather_cool_threshold_c", "쌀쌀함 판정 기온(℃)", -20, 30, 1, 0),
    ("weather_strong_wind_threshold_kmh", "강풍 판정 풍속(km/h)", 0, 150, 1, 0),
    ("weather_strong_wind_taxi_bonus", "강풍 시 택시확률 가산", -0.5, 0.5, 3, 0),
    ("weather_typhoon_wind_threshold_kmh", "태풍 판정 풍속(km/h)", 0, 200, 1, 0),
    ("weather_typhoon_taxi_bonus", "태풍 시 택시확률 가산", -0.5, 0.5, 3, 0),
]

# -------------------------------------------------------------
# 3. 고급 탭: 모델 & 배차 세부 파라미터 (Advanced Models)
# -------------------------------------------------------------
ADVANCED_MODEL_NUMERICS = [
    ("max_lag", "시계열 래그 수 (Max Lag)", 1, 24, 0, 1),
    ("rolling_short", "단기 이동평균 창 (rolling_short)", 1, 24, 0, 1),
    ("rolling_long", "장기 이동평균 창 (rolling_long)", 1, 48, 0, 1),
    ("test_size", "테스트 세트 비율 (test_size)", 0.05, 0.4, 2, 0),
    ("cnn_hidden_dim", "CNN-LSTM 은닉 차원 (hidden_dim)", 16, 256, 0, 1),
    ("cnn_num_layers", "CNN-LSTM 레이어 수 (num_layers)", 1, 4, 0, 1),
    ("cnn_kernel_size", "CNN 커널 크기 (kernel_size)", 1, 7, 0, 1),
    ("cnn_batch_size", "CNN 배치 크기 (batch_size)", 16, 512, 0, 1),
    ("cnn_lr", "CNN 학습률 (learning rate)", 0.0001, 0.05, 5, 0),
    ("min_multiplier", "최소 요금 배수 (min_multiplier)", 0.5, 2.0, 2, 0),
    ("mock_available_taxis_min", "테스트 가용택시 최소", 0, 100, 0, 1),
    ("mock_available_taxis_max", "테스트 가용택시 최대", 0, 200, 0, 1),
    ("mock_taxi_count", "배차 단위테스트 택시 수", 1, 200, 0, 1),
    ("mock_passenger_count", "배차 단위테스트 승객 수", 1, 200, 0, 1),
    ("demo_data_rows", "합성 데이터 생성 행 수", 100, 50000, 0, 1),
    ("demo_data_minutes", "추론 데모 데이터 분(min)", 10, 2000, 0, 1),
    ("xgb_random_state", "XGBoost 난수 시드 (random_state)", 0, 9999, 0, 1),
    ("train_random_state", "학습 분할 난수 시드 (random_state)", 0, 9999, 0, 1),
]

BOOLS = [
    ("use_real_map", "실제 OSM 지도 도로망 사용 (True: 강남역 OSM, False: 합성 그리드)"),
    ("dispatch_use_euclidean", "거리 계산 시 유클리드 좌표 사용 (테스트 전용)"),
    ("suppress_sumo_warnings", "SUMO 콘솔 경고 로그 숨기기 (시뮬레이션 속도 향상)"),
    ("weather_bonus_enabled", "날씨 보정 적용 (현재 날씨 조회 → 택시확률 가산)"),
    ("department_enabled", "백화점/쇼핑몰 방문 승객 생성 (실제 지도에 백화점이 있을 때)"),
]

# -------------------------------------------------------------
# 시나리오 원클릭 프리셋 정의 (유니티 시연 및 벤치마크용)
# -------------------------------------------------------------
SCENARIO_PRESETS = {
    "🚀 강남역 출근길 (08~10시)": {
        "region": "강남역", "use_real_map": True,
        "sim_start_hour": 8, "sim_end_hour": 10,
        "num_taxis": 25, "num_passengers": 1000, "num_normal_cars": 25,
        "taxi_strategy": "prepositioned", "taxi_dispatch_algorithm": "traci",
        "h3_resolution": 8, "freq": "5min", "base_fare": 4800, "max_multiplier": 3.0,
    },
    "🌙 강남역 불금 저녁 (18~22시)": {
        "region": "강남역", "use_real_map": True,
        "sim_start_hour": 18, "sim_end_hour": 22,
        "num_taxis": 35, "num_passengers": 2000, "num_normal_cars": 40,
        "taxi_strategy": "prepositioned", "taxi_dispatch_algorithm": "traci",
        "h3_resolution": 8, "freq": "5min", "base_fare": 4800, "max_multiplier": 3.0,
    },
    "⚡ 초고속 테스트 모드 (60초)": {
        "region": "강남역", "use_real_map": False,
        "sim_start_hour": 8, "sim_end_hour": 9,
        "num_taxis": 10, "num_passengers": 100, "num_normal_cars": 5,
        "taxi_strategy": "patrol", "taxi_dispatch_algorithm": "greedy",
        "h3_resolution": 8, "freq": "5min", "base_fare": 4800, "max_multiplier": 1.5,
    },
    "🏙️ 홍대입구 심야 (22~24시)": {
        "region": "홍대입구", "use_real_map": True,
        "sim_start_hour": 22, "sim_end_hour": 24,
        "num_taxis": 20, "num_passengers": 1200, "num_normal_cars": 15,
        "taxi_strategy": "prepositioned", "taxi_dispatch_algorithm": "traci",
        "h3_resolution": 8, "freq": "5min", "base_fare": 4800, "max_multiplier": 3.0,
    },
}


class ConfigGUI:
    def __init__(self, root):
        self.root = root
        root.title("AI Mobility 관제탑 — CONFIG GUI (통합 설정)")
        root.geometry("820x860")
        self.vars = {}
        self.bool_vars = {}
        self.choice_vars = {}
        self._build()
        self.load_config_file(show=False)

    def _build(self):
        # 상단 타이틀
        header_frame = ttk.Frame(self.root, padding=(12, 10, 12, 6))
        header_frame.pack(fill="x")
        ttk.Label(
            header_frame,
            text="AI MOBILITY CONTROL TOWER",
            font=("Consolas", 10, "bold"),
            foreground="#2563eb",
        ).pack(anchor="w")
        ttk.Label(
            header_frame,
            text="디지털 트윈 & AI 모빌리티 통합 실험 관제 GUI",
            font=("맑은 고딕", 14, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header_frame,
            text="모든 변경사항은 config.json에 저장되어 유니티(Unity) 3D 시뮬레이터 및 AI 모듈에 실시간 동기화됩니다.",
            font=("맑은 고딕", 9),
            foreground="#64748b",
        ).pack(anchor="w", pady=(2, 0))

        # 메인 탭바 (Notebook)
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=12, pady=6)

        # 1. 🌟 핵심 제어 탭 (Quick Controls)
        self._build_quick_controls_tab()

        # 2. 📊 시뮬레이션 고급 탭 (Advanced Sim)
        self._build_scrollable_numeric_tab("시뮬레이션 고급 (세부 인구 모수)", ADVANCED_SIM_NUMERICS)

        # 3. 🧠 AI & 배차 고급 탭 (Advanced Models)
        self._build_scrollable_numeric_tab("AI 모델 & 배차 고급", ADVANCED_MODEL_NUMERICS)

        # 4. 🔧 시스템 & 옵션 탭
        self._build_system_tab()

        # 프리셋 관리 프레임
        pf = ttk.LabelFrame(self.root, text="사용자 정의 프리셋 (Custom Presets)", padding=8)
        pf.pack(fill="x", padx=12, pady=4)
        self.preset_combo = ttk.Combobox(pf, state="readonly")
        self.preset_combo.pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(pf, text="불러오기", command=self.load_preset).pack(side="left", padx=2)
        ttk.Button(pf, text="새로 저장", command=self.save_preset).pack(side="left", padx=2)
        ttk.Button(pf, text="삭제", command=self.delete_preset).pack(side="left", padx=2)
        self.refresh_presets()

        # 하단 실행 버튼 그룹
        bf = ttk.Frame(self.root, padding=(12, 6, 12, 8))
        bf.pack(fill="x")
        ttk.Button(bf, text="💾 설정 적용하기 (Apply)", command=self.save_config_only).pack(side="left", fill="x", expand=True, padx=3)
        ttk.Button(bf, text="▶ 시뮬레이션 플레이 (Run)", command=self.run_main).pack(side="left", fill="x", expand=True, padx=3)
        ttk.Button(bf, text="⚡ 모델 재학습 (Train)", command=self.run_training).pack(side="left", fill="x", expand=True, padx=3)
        ttk.Button(bf, text="🧪 A/B 비교 분석", command=self.open_ab_compare_dialog).pack(side="left", fill="x", expand=True, padx=3)

        # 상태 표시줄
        self.status = ttk.Label(self.root, text="config.json 불러오는 중...", foreground="#475569")
        self.status.pack(anchor="w", padx=14, pady=(0, 6))

    def _build_quick_controls_tab(self):
        """메인 탭: 자주 조작하는 핵심 파라미터와 원클릭 시나리오 버튼"""
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="🌟 핵심 제어 (Quick Controls)")

        # (1) 원클릭 시나리오 프리셋 버튼 그룹
        preset_box = ttk.LabelFrame(tab, text="🚀 원클릭 시나리오 프리셋 (Unity 시연 및 벤치마크)", padding=8)
        preset_box.pack(fill="x", pady=(0, 10))

        btn_row = ttk.Frame(preset_box)
        btn_row.pack(fill="x")
        for title, preset_vals in SCENARIO_PRESETS.items():
            btn = ttk.Button(
                btn_row,
                text=title,
                command=lambda p=preset_vals, t=title: self._apply_quick_preset(p, t),
            )
            btn.pack(side="left", fill="x", expand=True, padx=3)

        # (2) 스크롤 가능한 핵심 설정 리스트
        canvas = tk.Canvas(tab, highlightthickness=0)
        sb = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # 주요 콤보박스 / 선택형 설정 먼저 배치
        env_box = ttk.LabelFrame(inner, text="도시 및 배차 전략 선택", padding=8)
        env_box.pack(fill="x", pady=4)

        # 지역 선택
        r1 = ttk.Frame(env_box)
        r1.pack(fill="x", pady=3)
        ttk.Label(r1, text="대상 지역 (Region)", width=28).pack(side="left")
        self.region = RegionSearchEntry(r1, default="강남역", width=22)
        self.region.pack(side="right")

        # 배차 전략
        r2 = ttk.Frame(env_box)
        r2.pack(fill="x", pady=3)
        ttk.Label(r2, text="택시 주행 전략 (Strategy)", width=28).pack(side="left")
        v_strat = tk.StringVar(value=DEFAULT_CONFIG.get("taxi_strategy", "patrol"))
        self.choice_vars["taxi_strategy"] = v_strat
        ttk.Combobox(r2, textvariable=v_strat, values=STRATEGIES, state="readonly", width=20).pack(side="right")

        # 배차 알고리즘
        r3 = ttk.Frame(env_box)
        r3.pack(fill="x", pady=3)
        ttk.Label(r3, text="배차 알고리즘 (Algorithm)", width=28).pack(side="left")
        v_algo = tk.StringVar(value=DEFAULT_CONFIG.get("taxi_dispatch_algorithm", "traci"))
        self.choice_vars["taxi_dispatch_algorithm"] = v_algo
        ttk.Combobox(r3, textvariable=v_algo, values=DISPATCH_ALGORITHMS, state="readonly", width=20).pack(side="right")

        # freq
        r4 = ttk.Frame(env_box)
        r4.pack(fill="x", pady=3)
        ttk.Label(r4, text="시계열 집계 단위 (freq)", width=28).pack(side="left")
        self.freq_var = tk.StringVar(value=DEFAULT_CONFIG.get("freq", "5min"))
        ttk.Combobox(r4, textvariable=self.freq_var, values=FREQ_OPTIONS, state="readonly", width=20).pack(side="right")

        # 실제 지도 사용 체크박스
        v_map = tk.BooleanVar(value=bool(DEFAULT_CONFIG.get("use_real_map", True)))
        self.bool_vars["use_real_map"] = v_map
        ttk.Checkbutton(env_box, text="실제 OSM 도로망 사용 (강남역/홍대입구 등)", variable=v_map).pack(anchor="w", pady=4)

        # 핵심 슬라이더 배치
        num_box = ttk.LabelFrame(inner, text="핵심 수치 슬라이더", padding=8)
        num_box.pack(fill="x", pady=6)

        for key, label, lo, hi, dec, isint in CORE_NUMERICS:
            row = ttk.Frame(num_box)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=32).pack(side="left")
            var = tk.DoubleVar(value=DEFAULT_CONFIG.get(key, 0))
            self.vars[key] = (var, dec, isint)
            ttk.Entry(row, textvariable=var, width=10).pack(side="right", padx=4)
            ttk.Scale(row, from_=lo, to=hi, variable=var, length=240).pack(side="right", fill="x", expand=True)

    def _build_scrollable_numeric_tab(self, tab_title, items):
        """스크롤 가능한 슬라이더 탭 생성 도우미"""
        frame = ttk.Frame(self.nb, padding=12)
        self.nb.add(frame, text=tab_title)

        canvas = tk.Canvas(frame, highlightthickness=0)
        sb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        for key, label, lo, hi, dec, isint in items:
            row = ttk.Frame(inner)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=34).pack(side="left")
            var = tk.DoubleVar(value=DEFAULT_CONFIG.get(key, 0))
            self.vars[key] = (var, dec, isint)
            ttk.Entry(row, textvariable=var, width=12).pack(side="right", padx=4)
            ttk.Scale(row, from_=lo, to=hi, variable=var, length=300).pack(side="right", fill="x", expand=True)

    def _build_system_tab(self):
        """기타 옵션 및 시스템 설정 탭"""
        f = ttk.Frame(self.nb, padding=14)
        self.nb.add(f, text="🔧 시스템 옵션")

        box1 = ttk.LabelFrame(f, text="시뮬레이션 및 배차 보조 옵션", padding=10)
        box1.pack(fill="x", pady=6)

        for key, label in BOOLS:
            if key in self.bool_vars:
                continue
            v = tk.BooleanVar(value=bool(DEFAULT_CONFIG.get(key, False)))
            self.bool_vars[key] = v
            ttk.Checkbutton(box1, text=label, variable=v).pack(anchor="w", pady=4)

        # 공차 알고리즘
        r = ttk.Frame(box1)
        r.pack(fill="x", pady=6)
        ttk.Label(r, text="SUMO 공차 주행 알고리즘 (taxi_idle_algorithm)", width=36).pack(side="left")
        v_idle = tk.StringVar(value=DEFAULT_CONFIG.get("taxi_idle_algorithm", "randomCircling"))
        self.choice_vars["taxi_idle_algorithm"] = v_idle
        ttk.Combobox(r, textvariable=v_idle, values=IDLE_ALGORITHMS, state="readonly", width=20).pack(side="right")

        # 수요 출처 (synthetic: 카테고리 가정 모델, real: TIMS 실측 보정 외생 수요)
        r_dem = ttk.Frame(box1)
        r_dem.pack(fill="x", pady=6)
        ttk.Label(r_dem, text="승객 수요 출처 (demand_source)", width=36).pack(side="left")
        v_dem = tk.StringVar(value=DEFAULT_CONFIG.get("demand_source", "synthetic"))
        self.choice_vars["demand_source"] = v_dem
        ttk.Combobox(r_dem, textvariable=v_dem, values=DEMAND_SOURCES, state="readonly", width=20).pack(side="right")

        # 평일/공휴일 판정 (auto: 실행 날짜로 판정, weekday/holiday: 강제)
        r_hol = ttk.Frame(box1)
        r_hol.pack(fill="x", pady=6)
        ttk.Label(r_hol, text="평일/공휴일 판정 (holiday_mode)", width=36).pack(side="left")
        v_hol = tk.StringVar(value=DEFAULT_CONFIG.get("holiday_mode", "auto"))
        self.choice_vars["holiday_mode"] = v_hol
        ttk.Combobox(r_hol, textvariable=v_hol, values=HOLIDAY_MODES, state="readonly", width=20).pack(side="right")

        # 시드 설정
        box2 = ttk.LabelFrame(f, text="재현성 시드 (Seed)", padding=10)
        box2.pack(fill="x", pady=6)

        r_seed = ttk.Frame(box2)
        r_seed.pack(fill="x", pady=4)
        ttk.Label(r_seed, text="Passenger Seed (비워두면 무작위 추첨)", width=36).pack(side="left")
        self.seed_var = tk.StringVar()
        ttk.Entry(r_seed, textvariable=self.seed_var, width=20).pack(side="right")

    def _apply_quick_preset(self, preset_dict, name):
        """원클릭 프리셋 즉시 주입"""
        for k, val in preset_dict.items():
            if k == "region" and hasattr(self, "region"):
                self.region.set(val)
            elif k == "freq" and hasattr(self, "freq_var"):
                self.freq_var.set(val)
            elif k in self.choice_vars:
                self.choice_vars[k].set(str(val))
            elif k in self.bool_vars:
                self.bool_vars[k].set(bool(val))
            elif k in self.vars:
                v, _, isint = self.vars[k]
                v.set(int(val) if isint else float(val))
        self.status.config(text=f"시나리오 프리셋 적용 완료: '{name}' (저장하려면 [설정 적용하기] 클릭)")

    def current(self):
        """현재 GUI 위젯에 입력된 전체 설정값을 딕셔너리로 추출"""
        c = DEFAULT_CONFIG.copy()
        c["region"] = self.region.get() if hasattr(self, "region") else "강남역"
        c["freq"] = self.freq_var.get() if hasattr(self, "freq_var") else "5min"
        s = self.seed_var.get().strip() if hasattr(self, "seed_var") else ""
        c["passenger_seed"] = int(s) if s.lstrip("-").isdigit() else None

        c.update({k: v.get() for k, v in self.bool_vars.items()})
        c.update({k: v.get() for k, v in self.choice_vars.items()})

        for k, (v, dec, isint) in self.vars.items():
            c[k] = int(v.get()) if isint else round(float(v.get()), dec)
        return c

    def set_values(self, c):
        """주어진 딕셔너리 값을 GUI 위젯들에 일괄 반영"""
        if hasattr(self, "region"):
            self.region.set(c.get("region", "강남역"))
        if hasattr(self, "freq_var"):
            self.freq_var.set(c.get("freq", DEFAULT_CONFIG["freq"]))
        if hasattr(self, "seed_var"):
            self.seed_var.set("" if c.get("passenger_seed") is None else str(c.get("passenger_seed")))

        for k, v in self.bool_vars.items():
            v.set(bool(c.get(k, DEFAULT_CONFIG.get(k, False))))
        for k, v in self.choice_vars.items():
            v.set(str(c.get(k, DEFAULT_CONFIG.get(k, ""))))
        for k, (v, dec, isint) in self.vars.items():
            if k in c:
                v.set(int(c[k]) if isint else float(c[k]))

    def load_config_file(self, show=True):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                c = json.load(f)
        except Exception:
            c = DEFAULT_CONFIG.copy()
        merged = {**DEFAULT_CONFIG, **c}
        self.set_values(merged)
        if hasattr(self, "status"):
            self.status.config(text="config.json 설정을 성공적으로 불러왔습니다.")

    def save_config_only(self):
        c = self.current()
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        self.status.config(text="설정을 적용했습니다. (config.json 저장 완료 - 모든 모듈에 동기화됨)")

    def refresh_presets(self):
        files = [f[:-5] for f in os.listdir(PRESET_DIR) if f.endswith(".json")]
        self.preset_combo["values"] = files
        if files:
            self.preset_combo.current(0)

    def save_preset(self):
        name = simpledialog.askstring("프리셋 저장", "새 프리셋 이름:")
        if name:
            with open(os.path.join(PRESET_DIR, name + ".json"), "w", encoding="utf-8") as f:
                json.dump(self.current(), f, ensure_ascii=False, indent=2)
            self.refresh_presets()
            self.status.config(text=f"프리셋 '{name}' 저장 완료")

    def load_preset(self):
        n = self.preset_combo.get()
        if not n:
            return
        with open(os.path.join(PRESET_DIR, n + ".json"), "r", encoding="utf-8") as f:
            self.set_values({**DEFAULT_CONFIG, **json.load(f)})
        self.status.config(text=f"프리셋 '{n}' 불러오기 완료")

    def delete_preset(self):
        n = self.preset_combo.get()
        if n and messagebox.askyesno("확인", f"프리셋 '{n}'을 삭제하시겠습니까?"):
            os.remove(os.path.join(PRESET_DIR, n + ".json"))
            self.refresh_presets()

    def _env(self):
        e = os.environ.copy()
        e["PYTHONIOENCODING"] = "utf-8"
        e["PYTHONUTF8"] = "1"
        return e

    def _run(self, script, args=(), blocking=False):
        path = os.path.join(ROOT, script)
        try:
            if blocking:
                return subprocess.run([sys.executable, path, *args], cwd=ROOT, env=self._env(), check=True)
            if os.name == "nt":
                inner_cmd = subprocess.list2cmdline([sys.executable, path, *args])
                full_cmd = f"cmd /k call {inner_cmd}"
                subprocess.Popen(full_cmd, cwd=ROOT, env=self._env(), creationflags=subprocess.CREATE_NEW_CONSOLE)
                return True
            subprocess.Popen([sys.executable, path, *args], cwd=ROOT, env=self._env())
            return True
        except Exception as e:
            messagebox.showerror("실행 오류", str(e))
            return False

    def run_main(self):
        self.save_config_only()
        if self._run(os.path.join("module1_simulation", "build_env.py"), blocking=True):
            self._run("main.py")

    def run_training(self):
        self.save_config_only()
        try:
            self.status.config(text="1/3 train.py 실행 중...")
            self.root.update()
            self._run("train.py", blocking=True)

            self.status.config(text="2/3 category demand 모델 학습 중...")
            self.root.update()
            self._run(os.path.join("module3_prediction", "train_category_demand.py"), blocking=True)

            self.status.config(text="3/3 강화학습 실행 중...")
            self.root.update()
            self._run(os.path.join("module4_dispatch", "rl_train.py"), blocking=True)

            self.status.config(text="전체 학습 완료")
            messagebox.showinfo(
                "학습 완료",
                "전체 학습이 완료되었습니다.\n\n"
                "생성 파일:\n"
                "• saved_models/xgboost_demand.pkl\n"
                "• saved_models/category_demand_models.pkl\n"
                "• module4_dispatch/checkpoints/rl_reposition_model_*.zip",
            )
        except subprocess.CalledProcessError as e:
            self.status.config(text="학습 중 오류 발생")
            messagebox.showerror("학습 오류", f"학습 과정에서 오류가 발생했습니다.\n\n실패한 단계의 종료 코드: {e.returncode}")
        except Exception as e:
            self.status.config(text="학습 중 오류 발생")
            messagebox.showerror("학습 오류", str(e))

    def open_ab_compare_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("A/B 비교하기")
        win.geometry("380x460")
        win.resizable(False, False)

        ttk.Label(
            win,
            text="비교할 값을 체크하세요. 각 항목당 2개 이상 체크해야 합니다.\n체크된 값들의 카테시안 조합만큼 새 창이 병렬로 뜹니다.",
            padding=10,
            wraplength=340,
            justify="left",
        ).pack()

        value_vars = {}
        for key, (label, values) in FACTOR_OPTIONS.items():
            box = ttk.LabelFrame(win, text=label, padding=8)
            box.pack(fill="x", padx=15, pady=6)
            value_vars[key] = {}
            for v in values:
                var = tk.BooleanVar(value=False)
                value_vars[key][v] = var
                ttk.Checkbutton(box, text=v, variable=var).pack(anchor="w")

        def run_compare():
            selected = {}
            for key, (label, values) in FACTOR_OPTIONS.items():
                chosen = [v for v, var in value_vars[key].items() if var.get()]
                if 0 < len(chosen) < 2:
                    messagebox.showwarning("경고", f"'{label}'는 2개 이상 체크해야 비교가 됩니다. (또는 아예 체크 해제)")
                    return
                if chosen:
                    selected[key] = chosen

            if not selected:
                messagebox.showwarning("경고", "비교할 항목을 하나 이상 체크하세요.")
                return

            import itertools

            keys = list(selected.keys())
            combos = [dict(zip(keys, combo)) for combo in itertools.product(*[selected[k] for k in keys])]

            if len(combos) > 8:
                if not messagebox.askyesno("확인", f"조합이 {len(combos)}개라 창이 {len(combos)}개 동시에 뜹니다. 계속할까요?"):
                    return

            win.destroy()
            self.save_config_only()
            self._run("multi_factor_compare.py", [json.dumps(combos, ensure_ascii=False)])

        ttk.Button(win, text="실행 (조합별로 새 창에서 병렬 측정)", command=run_compare).pack(pady=15, fill="x", padx=15)


if __name__ == "__main__":
    root = tk.Tk()
    ConfigGUI(root)
    root.mainloop()
