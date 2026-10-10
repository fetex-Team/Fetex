"""AI Mobility 전체 설정 GUI. GUI의 모든 실험/시뮬레이션 파라미터는 config.json으로 저장됩니다."""
import json, os, shutil, subprocess, sys, tkinter as tk, zipfile

# 이 스크립트(GUI) 자신이 print()로 콘솔에 직접 찍는 한글이, 컴퓨터/콘솔의 기본
# 코드페이지(cp949 등)에 걸려 깨지는 걸 방지. _run()이 자식 프로세스에 넣어주는
# PYTHONIOENCODING=utf-8는 이 프로세스 자기 자신에게는 적용되지 않기 때문에 따로 필요함.
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime
from tkinter import ttk, messagebox, simpledialog
from region_autocomplete import RegionSearchEntry
from config_loader import DEFAULT_CONFIG

ROOT = os.path.dirname(os.path.abspath(__file__))
PRESET_DIR = os.path.join(ROOT, "presets")
os.makedirs(PRESET_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(ROOT, "config.json")
FREQ_OPTIONS = ["1min", "5min", "10min", "15min", "30min"]
STRATEGIES = ["patrol", "prepositioned", "rl_prepositioned"]

FACTOR_OPTIONS = {
    "taxi_strategy": ("택시 전략 (체크)", STRATEGIES),
    "taxi_dispatch_algorithm": ("배차 알고리즘 (체크)", ["greedy", "routeExtension", "hungarian", "custom_greedy", "custom_routeExtension"]),
    "holiday_mode": ("공휴일 (체크)", ["weekday", "holiday"]),
}
FACTOR_VALUE_LABELS = {"weekday": "평일", "holiday": "공휴일"}
AB_WARN_COMBOS = 5  # 조합이 이 개수 이상이면 실행 전에 경고

NUMERIC_GROUPS = {
    "Module 1 — 맵/시뮬레이션": [
        ("grid_x", "그리드 가로 블록 수", 2, 20, 0, 1), ("grid_y", "그리드 세로 블록 수", 2, 20, 0, 1),
        ("grid_length", "블록 한 변 길이(m)", 10, 1000, 0, 1),
        ("num_normal_cars", "일반 차량 대수", 0, 500, 0, 1), ("num_taxis", "택시 대수", 0, 500, 0, 1),
        ("num_auto_cars", "자율주행차 대수", 0, 100, 0, 1), ("num_obstacles", "장애물 개수", 0, 100, 0, 1),
        ("num_passengers", "기본 승객 수", 0, 10000, 0, 1),
        ("sim_start_hour", "시뮬레이션 시작 시각", 0, 23, 0, 1), ("sim_end_hour", "시뮬레이션 종료 시각", 1, 24, 0, 1),
        ("passenger_wait_timeout", "승객 최대 대기시간(초)", 1, 7200, 0, 1),
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
        ("lunch_start_hour", "점심 시작", 0, 24, 2, 0), ("lunch_end_hour", "점심 종료", 0, 24, 2, 0),
        ("lunch_company_release_fraction", "회사 점심 이탈 비율", 0, 1, 3, 0),
        ("lunch_taxi_fraction", "점심 택시 비율", 0, 1, 3, 0),
        ("evening_start_hour", "퇴근 시작", 0, 24, 2, 0), ("evening_end_hour", "퇴근 주요시간 종료", 0, 24, 2, 0),
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
        ("department_pop_base", "백화점 건물당 인구", 0, 1000, 0, 1),
        ("department_window_start", "백화점 시간대 시작", 0, 24, 2, 0),
        ("department_window_end", "백화점 시간대 종료", 0, 24, 2, 0),
        ("department_taxi_probability", "백화점 시민 택시확률", 0, 1, 3, 0),
        ("department_stay_sec", "백화점 체류시간(초)", 0, 86400, 0, 1),
    ],
    "Module 2 — 전처리": [
        ("h3_resolution", "H3 resolution", 5, 15, 0, 1), ("max_lag", "Max lag", 1, 100, 0, 1),
        ("rolling_short", "단기 rolling window", 1, 100, 0, 1), ("rolling_long", "장기 rolling window", 1, 200, 0, 1),
        ("forecast_horizons", "미래 예측 구간 수", 1, 48, 0, 1),
    ],
    "Module 3 — 예측모델": [
        ("xgb_n_estimators", "XGB n_estimators", 10, 1000, 0, 1), ("xgb_max_depth", "XGB max_depth", 1, 30, 0, 1),
        ("xgb_learning_rate", "XGB learning_rate", 0.001, 1, 4, 0), ("test_size", "Test size", 0.01, 0.5, 3, 0),
        ("cnn_hidden_dim", "CNN hidden_dim", 8, 512, 0, 1), ("cnn_num_layers", "CNN num_layers", 1, 8, 0, 1),
        ("cnn_kernel_size", "CNN kernel_size", 1, 15, 0, 1), ("cnn_epochs", "CNN epochs", 1, 1000, 0, 1),
        ("cnn_batch_size", "CNN batch_size", 1, 512, 0, 1), ("cnn_lr", "CNN learning rate", 0.00001, 0.1, 6, 0),
    ],
    "Module 4 — 배차/인센티브": [
        ("base_fare", "기본 요금", 0, 100000, 0, 1), ("min_multiplier", "최소 요금 배수", 0.1, 10, 2, 0),
        ("max_multiplier", "최대 요금 배수", 0.1, 10, 2, 0), ("surge_coefficient", "수급 불균형 요금 계수", 0, 5, 3, 0),
        ("reposition_fraction", "규칙 기반 선배치 비율", 0, 1, 2, 0), ("incentive_weight", "인센티브 리워드 가중치", 0, 5, 2, 0),
        ("target_hotspot_ratio", "목표 핫스팟 택시 비율(0~1)", 0, 1, 2, 0),
        ("rl_max_decisions", "RL 에피소드 결정 횟수(분)", 1, 1440, 0, 1),
        ("rl_total_timesteps", "RL 총 학습 steps", 1000, 500000, 0, 1),
        ("rl_rollout_steps", "RL rollout steps (batch의 배수)", 32, 4096, 0, 1),
        ("rl_n_envs", "RL 병렬 env 개수 (CPU 코어 수 이하 권장, 1=병렬 안 씀)", 1, 32, 0, 1),
        ("rl_batch_size", "RL batch size", 8, 1024, 0, 1), ("rl_seed", "RL random seed", 0, 2147483647, 0, 1),
        ("rl_learning_rate", "RL 학습률", 0.00001, 0.01, 6, 0), ("rl_gamma", "RL 장기 보상 비율(gamma)", 0.8, 1, 3, 0),
        ("mock_available_taxis_min", "테스트 가용택시 최소", 0, 500, 0, 1),
        ("mock_available_taxis_max", "테스트 가용택시 최대", 0, 500, 0, 1),
        ("mock_taxi_count", "배차 테스트 택시 수", 1, 500, 0, 1),
        ("mock_passenger_count", "배차 테스트 승객 수", 1, 500, 0, 1),
    ],
    "공통/실험": [
        ("demo_data_rows", "학습 데모 데이터 행 수", 100, 100000, 0, 1),
        ("demo_data_minutes", "추론 데모 데이터 길이", 10, 100000, 0, 1),
        ("xgb_random_state", "XGB random state", 0, 2147483647, 0, 1),
        ("train_random_state", "학습 random state", 0, 2147483647, 0, 1),
    ],
}

BOOLS = [
    ("use_real_map", "실제 OSM 지도 사용"),
    ("dispatch_use_euclidean", "Module 4 테스트 거리: 유클리드 사용"),
    ("suppress_sumo_warnings", "SUMO 경고 로그 숨기기 (속도 향상)"),
    ("department_enabled", "백화점 승객 생성 (건물 위치는 build_env 때 OSM에서 찾음, 없으면 자동 꺼짐)"),
    ("run_sim_before_training", "학습만 실행 시 시뮬레이션 먼저 돌려서 로그 생성 (끄면 기존 로그로 바로 학습)"),
    ("rl_randomize_passenger_seed", "RL 학습: 매 에피소드마다 다른 승객 호출 패턴 사용"),
    ("force_regenerate_demand_v2", "학습만 실행 시 demand_v2.joblib이 이미 있어도 새 라인으로 다시 생성 (기존 파일은 demand_v2_backups/에 백업)"),
]
CHOICES = [
    ("taxi_strategy", "택시 전략", STRATEGIES),
    ("taxi_dispatch_algorithm", "SUMO taxi dispatch algorithm", ["greedy", "routeExtension", "hungarian", "custom_greedy", "custom_routeExtension"]),
    ("taxi_idle_algorithm", "SUMO taxi idle algorithm", ["randomCircling", "stopOnRoad", "taxiStop"]),
    ("passenger_mode", "승객 생성 모드", ["replay", "legacy"]),
    ("holiday_mode", "공휴일 판정 모드", ["auto", "weekday", "holiday"]),
    ("run_date_mode", "실행 날짜 방식", ["alternate", "sim_date", "random"]),
]


class ConfigGUI:
    def __init__(self, root):
        self.root = root
        root.title("AI Mobility — CONFIG GUI (전체 설정)")
        root.geometry("780x820")
        self.vars = {}
        self._loaded_config = {}
        self._build()
        self.load_config_file(show=False)

    def _build(self):
        ttk.Label(self.root, text="CONFIG GUI — 프로젝트 전체 실험/시뮬레이션 설정",
                  font=("맑은 고딕", 14, "bold")).pack(pady=10)

        bottom_frame = ttk.Frame(self.root)
        bottom_frame.pack(fill="x", side="bottom", padx=12, pady=(0, 8))
        content_frame = ttk.Frame(self.root)
        content_frame.pack(fill="both", expand=True)
        nb = ttk.Notebook(content_frame)
        nb.pack(fill="both", expand=True)
        for tab, items in NUMERIC_GROUPS.items():
            self._numeric_tab(nb, tab, items)
        self._special_tab(nb)

        pf = ttk.LabelFrame(bottom_frame, text="프리셋", padding=8)
        pf.pack(fill="x", padx=12, pady=4)
        self.preset_combo = ttk.Combobox(pf, state="readonly")
        self.preset_combo.pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(pf, text="불러오기", command=self.load_preset).pack(side="left")
        ttk.Button(pf, text="저장", command=self.save_preset).pack(side="left")
        ttk.Button(pf, text="삭제", command=self.delete_preset).pack(side="left")
        self.refresh_presets()

        bf = ttk.Frame(bottom_frame)
        bf.pack(fill="x", padx=12, pady=8)
        ttk.Button(bf, text="적용하기", command=self.save_config_only).pack(side="left", fill="x", expand=True, padx=3)
        ttk.Button(bf, text="▶ 플레이", command=self.run_main).pack(side="left", fill="x", expand=True, padx=3)
        ttk.Button(bf, text="학습만 실행", command=self.open_training_dialog).pack(side="left", fill="x", expand=True, padx=3)
        ttk.Button(bf, text="🧪 A/B 비교하기", command=self.open_ab_compare_dialog).pack(side="left", fill="x", expand=True, padx=3)
        ttk.Button(bf, text="📊 분석하기", command=self.open_analysis_dialog).pack(side="left", fill="x", expand=True, padx=3)
        ttk.Button(bf, text="🎮 유니티에서 작업하기", command=self.run_unity_export).pack(side="left", fill="x", expand=True, padx=3)

        self.status = ttk.Label(bottom_frame, text="현재 config.json을 불러오는 중...")
        self.status.pack(anchor="w", padx=12, pady=(0, 8))

    def _create_slider_row(self, parent, key, label, lo, hi, dec, isint):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, width=34).pack(side="left")
        var = tk.DoubleVar(value=DEFAULT_CONFIG.get(key, 0))
        self.vars[key] = (var, dec, isint)
        ttk.Entry(row, textvariable=var, width=12).pack(side="right", padx=4)
        ttk.Scale(row, from_=lo, to=hi, variable=var, length=300).pack(side="right", fill="x", expand=True)

    def _build_collapsible_module1(self, parent, items):
        categories = {
            "기본 맵 & 차량 설정": [
                "grid_x", "grid_y", "grid_length", "num_normal_cars",
                "num_taxis", "num_auto_cars", "num_obstacles", "num_passengers",
            ],
            "시뮬레이션 & 로직 기준": [
                "sim_start_hour", "sim_end_hour", "passenger_wait_timeout", "depart_jitter_sec",
                "sumo_time_to_teleport", "taxi_remaining_edges_threshold",
                "taxi_target_pick_attempts", "taxi_fail_threshold", "taxi_primary_prob",
            ],
            "주거 지역 (Residential)": [
                "residential_base_pop_per_edge", "residential_schedule_scale", "residential_taxi_probability",
                "residential_out_hour", "residential_out_probability", "residential_evening_in_hour",
                "residential_evening_in_probability", "residential_late_in_hour", "residential_late_in_probability",
            ],
            "학교 (School)": [
                "school_pop_base", "school_start_hour", "school_end_hour", "school_taxi_peak_hour",
                "school_afternoon_start_hour", "school_afternoon_end_hour", "school_afternoon_fraction",
            ],
            "회사 및 점심 (Company & Lunch)": [
                "company_pop_base", "company_start_hour", "company_end_hour", "company_taxi_peak_hour",
                "lunch_start_hour", "lunch_end_hour", "lunch_company_release_fraction", "lunch_taxi_fraction",
            ],
            "퇴근 및 저녁/음식점 (Evening & Restaurant)": [
                "evening_start_hour", "evening_end_hour", "evening_taxi_fraction", "late_evening_start_hour",
                "late_evening_end_hour", "evening_residential_fraction", "restaurant_stay_sec",
                "restaurant_pop_base", "restaurant_window1_start", "restaurant_window1_end",
                "restaurant_window2_start", "restaurant_window2_end", "restaurant_civilian_taxi_probability",
            ],
            "백화점 (Department Store)": [
                "department_pop_base", "department_window_start", "department_window_end",
                "department_taxi_probability", "department_stay_sec",
            ],
        }

        item_dict = {item[0]: item for item in items}

        for cat_name, keys in categories.items():
            group_frame = ttk.LabelFrame(parent, padding=5)
            group_frame.pack(fill="x", expand=True, pady=4, padx=2)

            header_frame = ttk.Frame(group_frame)
            header_frame.pack(fill="x")

            content_frame = ttk.Frame(group_frame, padding=(5, 5))
            content_frame.pack(fill="x", expand=True)

            is_expanded = [False]

            def make_toggle(c_frame, btn, expanded):
                def _toggle():
                    if expanded[0]:
                        c_frame.pack_forget()
                        btn.config(text="▶ 펼치기")
                        expanded[0] = False
                    else:
                        c_frame.pack(fill="x", expand=True)
                        btn.config(text="▼ 접기")
                        expanded[0] = True
                return _toggle

            lbl = ttk.Label(header_frame, text=cat_name, font=("맑은 고딕", 10, "bold"))
            lbl.pack(side="left", padx=5)

            btn = ttk.Button(header_frame, text="▶ 펼치기", width=10)
            btn.config(command=make_toggle(content_frame, btn, is_expanded))
            btn.pack(side="right")
            content_frame.pack_forget()  # 기본 접힘

            for k in keys:
                if k in item_dict:
                    key, label, lo, hi, dec, isint = item_dict[k]
                    self._create_slider_row(content_frame, key, label, lo, hi, dec, isint)

    def _numeric_tab(self, nb, tab, items):
        frame = ttk.Frame(nb, padding=12)
        nb.add(frame, text=tab.split("—")[0].strip() if "—" in tab else tab)

        canvas = tk.Canvas(frame, highlightthickness=0)
        sb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)

        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)

        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # Module 1일 경우에만 접기/펼치기 카테고리 적용
        if "Module 1" in tab:
            self._build_collapsible_module1(inner, items)
        else:
            for key, label, lo, hi, dec, isint in items:
                self._create_slider_row(inner, key, label, lo, hi, dec, isint)

    def _toggle_weather_frame(self):
        if self.weather_bonus_var.get():
            self.weather_frame.pack(fill="x", pady=6)
        else:
            self.weather_frame.pack_forget()

    def _special_tab(self, nb):
        frame = ttk.Frame(nb)
        nb.add(frame, text="공통/선택")

        canvas = tk.Canvas(frame, highlightthickness=0)
        sb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        f = ttk.Frame(canvas, padding=12)

        f.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=f, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)

        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        r = ttk.Frame(f)
        r.pack(fill="x", pady=5)
        ttk.Label(r, text="지역", width=30).pack(side="left")
        self.region_var = tk.StringVar()
        self.region = RegionSearchEntry(r, default="홍대입구", width=24)
        self.region.pack(side="right")

        r = ttk.Frame(f)
        r.pack(fill="x", pady=5)
        ttk.Label(r, text="freq", width=30).pack(side="left")
        self.freq_var = tk.StringVar()
        ttk.Combobox(r, textvariable=self.freq_var, values=FREQ_OPTIONS, state="readonly", width=20).pack(side="right")

        self.bool_vars = {}
        for key, label in BOOLS:
            v = tk.BooleanVar(value=bool(DEFAULT_CONFIG.get(key, False)))
            self.bool_vars[key] = v
            ttk.Checkbutton(f, text=label, variable=v).pack(anchor="w", pady=4)

        self.choice_vars = {}
        for key, label, vals in CHOICES:
            r = ttk.Frame(f)
            r.pack(fill="x", pady=5)
            ttk.Label(r, text=label, width=30).pack(side="left")
            v = tk.StringVar()
            self.choice_vars[key] = v
            ttk.Combobox(r, textvariable=v, values=vals, state="readonly", width=20).pack(side="right")

        r = ttk.Frame(f)
        r.pack(fill="x", pady=8)
        ttk.Label(r, text="Passenger seed (비우면 랜덤)", width=30).pack(side="left")
        self.seed_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.seed_var, width=20).pack(side="right")

        self.text_vars = {}
        for key, label in [
            ("sim_date", "시뮬레이션 기준 날짜 (YYYY-MM-DD)"),
            ("demo_data_start", "데모 데이터 시작 시각"),
            ("taxi_respawn_prefix", "택시 재생성 ID 접두어"),
        ]:
            r = ttk.Frame(f)
            r.pack(fill="x", pady=5)
            ttk.Label(r, text=label, width=30).pack(side="left")
            v = tk.StringVar()
            self.text_vars[key] = v
            ttk.Entry(r, textvariable=v, width=24).pack(side="right")

        # [공휴일 보정] 공휴일/주말일 때 카테고리별 택시확률 배율 (평일은 항상 1.0)
        ttk.Label(f, foreground="gray", wraplength=520, justify="left", text=(
            "공휴일 판정: auto=아래 날짜로 자동(토·일·한국 공휴일), weekday/holiday=강제.\n"
            "실행 날짜: sim_date=위 '기준 날짜' 고정(평가·A/B용), alternate=평일/공휴일 번갈아(학습 로그용), random=랜덤.")
        ).pack(anchor="w", pady=(4, 0))
        self.holiday_frame = ttk.LabelFrame(f, text="공휴일 배율 (공휴일/주말일 때 택시확률에 곱함)", padding=8)
        self.holiday_frame.pack(fill="x", pady=(8, 4))
        self.holiday_mult_vars = {}
        _hm_defaults = DEFAULT_CONFIG.get("holiday_multipliers", {})
        for key, label in [
            ("school", "등교 (0=공휴일 등교 없음)"),
            ("company", "출근 (점심·퇴근 인원도 같이 줄어듦)"),
            ("restaurant", "음식점 방문"),
            ("general", "기본 수요 (num_passengers 가챠)"),
            ("department", "백화점 방문 (공휴일 증가)"),
            ("lunch", "점심 이동 (아침 인원에서 파생, 1.0 권장)"),
            ("evening", "퇴근 (아침 인원에서 파생, 1.0 권장)"),
        ]:
            row = ttk.Frame(self.holiday_frame)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=34).pack(side="left")
            var = tk.DoubleVar(value=_hm_defaults.get(key, 1.0))
            self.holiday_mult_vars[key] = var
            ttk.Entry(row, textvariable=var, width=8).pack(side="right", padx=4)
            ttk.Scale(row, from_=0, to=3, variable=var, length=200).pack(side="right", fill="x", expand=True)

        # [날씨 보정] 체크하면 아래 슬라이더가 나타남
        self.weather_bonus_var = tk.BooleanVar(value=bool(DEFAULT_CONFIG.get("weather_bonus_enabled", False)))
        self.bool_vars["weather_bonus_enabled"] = self.weather_bonus_var
        self.weather_bonus_var.trace_add("write", lambda *args: self._toggle_weather_frame())
        ttk.Checkbutton(f, text="날씨적용 (오늘 날씨로 택시확률 보정)", variable=self.weather_bonus_var).pack(anchor="w", pady=(12, 4))

        self.weather_frame = ttk.LabelFrame(f, text="날씨 보정 값", padding=8)
        weather_items = [
            ("weather_rain_taxi_bonus", "비 올 때 택시확률 가산", 0, 1, 3, 0),
            ("weather_cool_taxi_bonus", "쌀쌀할 때 택시확률 가산", 0, 1, 3, 0),
            ("weather_sunny_taxi_bonus", "맑을 때 택시확률 가산", 0, 1, 3, 0),
            ("weather_rain_threshold_mm", "비로 판정하는 강수량(mm)", 0, 50, 2, 0),
            ("weather_cool_threshold_c", "쌀쌀함 판정 기온(°C)", -20, 40, 1, 0),
            ("weather_strong_wind_threshold_kmh", "강풍 판정 풍속(km/h, 50.4=14m/s)", 0, 200, 1, 0),
            ("weather_strong_wind_taxi_bonus", "강풍일 때 택시확률 가산(음수=감소)", -1, 1, 3, 0),
            ("weather_typhoon_wind_threshold_kmh", "태풍급 판정 풍속(km/h, 61.2=17m/s, 강풍보다 우선)", 0, 200, 1, 0),
            ("weather_typhoon_taxi_bonus", "태풍급일 때 택시확률 가산(음수=감소)", -1, 1, 3, 0),
        ]
        for key, label, lo, hi, dec, isint in weather_items:
            row = ttk.Frame(self.weather_frame)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=28).pack(side="left")
            var = tk.DoubleVar(value=DEFAULT_CONFIG.get(key, 0))
            self.vars[key] = (var, dec, isint)
            ttk.Entry(row, textvariable=var, width=10).pack(side="right", padx=4)
            ttk.Scale(row, from_=lo, to=hi, variable=var, length=220).pack(side="right", fill="x", expand=True)
        self._toggle_weather_frame()

    def current(self):
        c = {**DEFAULT_CONFIG, **self._loaded_config}
        c.update({
            "region": self.region.get() or "홍대입구",
            "freq": self.freq_var.get() or DEFAULT_CONFIG["freq"],
            "passenger_seed": None,
            "taxi_strategy": self.choice_vars["taxi_strategy"].get() or DEFAULT_CONFIG["taxi_strategy"],
        })
        s = self.seed_var.get().strip()
        c["passenger_seed"] = int(s) if s.lstrip("-").isdigit() else None
        c.update({k: v.get() for k, v in self.bool_vars.items()})
        c.update({k: v.get() for k, v in self.choice_vars.items()})
        c.update({k: v.get().strip() for k, v in self.text_vars.items()})
        for k, (v, dec, isint) in self.vars.items():
            c[k] = int(v.get()) if isint else round(float(v.get()), dec)
        c["holiday_multipliers"] = {k: round(float(v.get()), 2) for k, v in self.holiday_mult_vars.items()}
        return c

    def set_values(self, c):
        self.region.set(c.get("region", "홍대입구"))
        self.freq_var.set(c.get("freq", DEFAULT_CONFIG["freq"]))
        self.seed_var.set("" if c.get("passenger_seed") is None else str(c.get("passenger_seed")))
        for k, v in self.text_vars.items():
            v.set(c.get(k, DEFAULT_CONFIG.get(k, "")))
        for k, v in self.bool_vars.items():
            v.set(bool(c.get(k, DEFAULT_CONFIG.get(k, False))))
        for k, v in self.choice_vars.items():
            v.set(c.get(k, DEFAULT_CONFIG.get(k, "")))
        for k, (v, dec, isint) in self.vars.items():
            if k in c:
                v.set(int(c[k]) if isint else float(c[k]))
        _hm = {**DEFAULT_CONFIG.get("holiday_multipliers", {}), **(c.get("holiday_multipliers") or {})}
        for k, v in self.holiday_mult_vars.items():
            v.set(float(_hm.get(k, 1.0)))

    def load_config_file(self, show=True):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                c = json.load(f)
        except Exception:
            c = DEFAULT_CONFIG.copy()
        merged = {**DEFAULT_CONFIG, **c}
        self._loaded_config = merged.copy()
        self.set_values(merged)
        if hasattr(self, "status"):
            self.status.config(text="현재 config.json을 GUI에 불러왔습니다.")

    def save_config_only(self):
        c = self.current()
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        self._loaded_config = c.copy()
        self.status.config(text="설정을 적용했습니다. (config.json 저장 완료)")

    def refresh_presets(self):
        files = [f[:-5] for f in os.listdir(PRESET_DIR) if f.endswith(".json")]
        self.preset_combo["values"] = files
        if files:
            self.preset_combo.current(0)

    def save_preset(self):
        name = simpledialog.askstring("프리셋", "이름:")
        if name:
            with open(os.path.join(PRESET_DIR, name + ".json"), "w", encoding="utf-8") as f:
                json.dump(self.current(), f, ensure_ascii=False, indent=2)
            self.refresh_presets()
            self.status.config(text=f"프리셋 '{name}' 저장 완료")

    def load_preset(self):
        n = self.preset_combo.get()
        if not n:
            return
        with open(os.path.join(PRESET_DIR, n + ".json"), encoding="utf-8") as f:
            self._loaded_config = {**DEFAULT_CONFIG, **json.load(f)}
        self.set_values(self._loaded_config)
        self.status.config(text=f"프리셋 '{n}' 불러오기 완료")

    def delete_preset(self):
        n = self.preset_combo.get()
        if n and messagebox.askyesno("확인", f"'{n}' 삭제?"):
            os.remove(os.path.join(PRESET_DIR, n + ".json"))
            self.refresh_presets()

    def _env(self):
        e = os.environ.copy()
        e["PYTHONIOENCODING"] = "utf-8"
        e["PYTHONUTF8"] = "1"
        return e

    def _run(self, script, args=(), blocking=False, env_extra=None):
        path = os.path.join(ROOT, script)
        try:
            env = self._env()
            if env_extra:
                env.update({str(k): str(v) for k, v in env_extra.items()})
            if blocking:
                return subprocess.run([sys.executable, path, *args], cwd=ROOT, env=env, check=True)
            if os.name == "nt":
                inner_cmd = subprocess.list2cmdline([sys.executable, path, *args])
                full_cmd = f'cmd /k call {inner_cmd}'
                subprocess.Popen(full_cmd, cwd=ROOT, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
                return True
            subprocess.Popen([sys.executable, path, *args], cwd=ROOT, env=env)
            return True
        except Exception as e:
            messagebox.showerror("실행 오류", str(e))
            return False

    def run_main(self):
        self.save_config_only()
        if self._run(os.path.join("module1_simulation", "build_env.py"), blocking=True):
            self._run("main.py")

    def _backup_saved_models(self, rl_total_timesteps: int, skip_v2: bool = False):
        """train.py가 saved_models/를 덮어쓰기 전에, 날짜/시각 붙여서 통째로 백업.
        rl_total_timesteps가 1만 미만인 짧은 테스트 실행이면 백업을 건너뜀."""
        if rl_total_timesteps < 10000:
            self.status.config(text=f"백업 생략 (rl_total_timesteps={rl_total_timesteps} < 10000, 테스트성 실행)")
            self.root.update()
            return
        src = os.path.join(ROOT, "saved_models")
        if not os.path.isdir(src) or not os.listdir(src):
            return  # 백업할 기존 모델이 없으면 스킵
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(ROOT, "saved_models_backups", timestamp)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        # skip_v2: 새 학습 라인이 demand_v2.joblib을 쓰는 중이면 반쪽짜리 파일을 복사하지 않도록 제외
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("demand_v2.joblib") if skip_v2 else None)
        self.status.config(text=f"기존 모델을 saved_models_backups/{timestamp}에 백업했습니다.")
        self.root.update()


    def _start_v2_line(self, force=False):
        """[새 학습 라인] demand_v2.joblib이 있으면 SKIP, 없으면 scripts/train_dispatch_model.py를 백그라운드로 시작.
        force=True(GUI 체크박스)면 demand_v2.joblib이 있어도 다시 생성하고, 덮어쓰기 전에 기존 파일을 demand_v2_backups/에 복사해 둔다.
        기존 라인과 독립적으로 돌며, 여기서 종료된다. 반환: (Popen 또는 None, 상태 'skip'/'started'/'no_data'/'error:...')"""
        v2 = os.path.join(ROOT, "saved_models", "demand_v2.joblib")
        if os.path.exists(v2) and not force:
            return None, "skip"
        calls = os.path.join("data", "generated", "calls.csv")
        ext = os.path.join("data", "generated", "external.csv")
        if not (os.path.exists(os.path.join(ROOT, calls)) and os.path.exists(os.path.join(ROOT, ext))):
            return None, "no_data"
        try:
            if os.path.exists(v2):  # 강제 재생성: 새 라인이 덮어쓰기 전에 기존 파일 보존
                bak = os.path.join(ROOT, "demand_v2_backups", f"demand_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.joblib")
                os.makedirs(os.path.dirname(bak), exist_ok=True)
                shutil.copy2(v2, bak)
                print(f"[새 학습 라인] 강제 재생성: 기존 demand_v2.joblib을 {os.path.relpath(bak, ROOT)} 에 백업했습니다.")
            os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
            with open(os.path.join(ROOT, "logs", "train_v2.log"), "w", encoding="utf-8") as logf:
                proc = subprocess.Popen(
                    [sys.executable, os.path.join(ROOT, "scripts", "train_dispatch_model.py"), calls, "--external", ext],
                    cwd=ROOT, env=self._env(), stdout=logf, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
        except Exception as e:
            return None, f"error:{e}"
        return proc, "started"

    def _v2_cell_warning(self):
        """demand_v2.joblib의 H3 셀 vs 방금 만든 맵(runtime_meta.json)의 셀을 비교.
        다르면 경고 문구를, 같거나 비교 불가면 None을 반환."""
        try:
            r = subprocess.run(
                [sys.executable, os.path.join(ROOT, "scripts", "convert_v2_to_category.py"), "--check-cells"],
                cwd=ROOT, env=self._env(), capture_output=True, text=True, encoding="utf-8",
            )
        except Exception:
            return None
        return r.stdout.strip() if r.returncode == 3 and r.stdout.strip() else None

    def _finish_v2_line(self, proc, state):
        """[3단계] 새 라인이 만든 demand_v2.joblib을 받아 category_demand_from_v2.pkl로 변환.
        (RL 학습 앞으로 옮겨짐 - 변환 결과가 있어야 RL이 그걸 참고해서 학습할 수 있음)"""
        if state == "no_data":
            return "변환 생략 - demand_v2.joblib도, 학습 데이터(data/generated/calls.csv, external.csv)도 없음"
        if state.startswith("error"):
            return f"변환 생략 - 새 라인 시작 실패 ({state})"
        if state == "started":
            self.status.config(text="3/4 새 학습 라인(demand_v2) 완료 대기 중...")
            self.root.update()
            rc = proc.wait()
            if rc != 0 or not os.path.exists(os.path.join(ROOT, "saved_models", "demand_v2.joblib")):
                return f"변환 생략 - 새 학습 라인 실패 (종료코드 {rc}), logs/train_v2.log 확인"
        self.status.config(text="3/4 demand_v2 -> 카테고리 모델 변환 중...")
        self.root.update()
        cell_warn = self._v2_cell_warning()
        if not self._run(os.path.join("scripts", "convert_v2_to_category.py"), blocking=True):
            return "변환 실패 (오류 팝업 참고)"
        msg = "saved_models/category_demand_from_v2.pkl 생성"
        if state == "skip":
            msg += " (demand_v2.joblib이 이미 있어 새 학습은 SKIP)"
        if cell_warn:
            msg += f"\n   [경고] {cell_warn}"
        return msg

    def _backup_rl_model(self):
        """RL 학습이 끝난 뒤, 저장된 zip의 실제 종료 step이 10,000 이상일 때만 백업."""
        model = os.path.join(ROOT, "module4_dispatch", "rl_reposition_model.zip")
        try:
            with zipfile.ZipFile(model) as z:
                steps = int(json.loads(z.read("data").decode("utf-8"))["num_timesteps"])
        except Exception as e:
            return f"백업 생략 - 실제 종료 step 확인 불가 ({e})"
        if steps < 10000:
            return f"백업 생략 - 실제 종료 step {steps:,} < 10,000"
        dst_dir = os.path.join(ROOT, "rl_model_backups")
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, f"rl_reposition_model_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{steps}steps.zip")
        shutil.copy2(model, dst)
        return f"실제 종료 step {steps:,} >= 10,000, rl_model_backups/{os.path.basename(dst)} 에 보존"

    def run_training(self):
        c = self.current()

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)

        # [새 학습 라인] 기존 라인(build_env 이후)을 기다리지 않고 지금 바로 동시에 시작
        v2_proc, v2_state = self._start_v2_line(bool(c.get("force_regenerate_demand_v2", False)))
        print({
            "skip": "[새 학습 라인] demand_v2.joblib이 이미 있어 SKIP합니다 (프로세스를 띄우지 않음).",
            "no_data": "[새 학습 라인] data/generated/calls.csv 또는 external.csv가 없어 시작하지 못했습니다.",
            "started": "[새 학습 라인] 백그라운드로 시작했습니다. 별도 창은 없고 진행 상황은 logs/train_v2.log 에서 확인하세요.",
        }.get(v2_state, f"[새 학습 라인] 시작 실패: {v2_state}"))

        try:
            self.status.config(text="0/4 sumo 환경(build_env.py) 재생성 중...")
            self.root.update()

            if not self._run(os.path.join("module1_simulation", "build_env.py"), blocking=True):
                self.status.config(text="build_env.py 실행 실패 - 학습 중단")
                return

            # v2를 SKIP한 경우, 방금 만든 맵과 셀이 맞는지 여기서 바로 알림
            if v2_state == "skip":
                v2_cell_warn = self._v2_cell_warning()
                if v2_cell_warn:
                    print("[경고] demand_v2.joblib 학습을 SKIP했지만 " + v2_cell_warn)

            if c.get("run_sim_before_training", True):
                import time as _time, log_selection as _ls
                _sim_t0 = _time.time()
                n_sim_runs = 3
                for i in range(n_sim_runs):
                    self.status.config(text=f"0.5/4 시뮬레이션으로 학습 로그 생성 중... ({i + 1}/{n_sim_runs})")
                    self.root.update()
                    if not self._run("measure_wait_time.py", blocking=True):
                        self.status.config(text="시뮬레이션(로그 생성) 실행 실패 - 학습 중단")
                        return
                _added = _ls.add_new_logs_to_selection(_sim_t0)
                if _added:
                    print(f"[로그 선택] 방금 만든 로그 {len(_added)}개를 학습 선택 목록에 추가했습니다: {', '.join(_added)}")
            else:
                self.status.config(text="0.5/4 시뮬레이션 생략 (기존 로그로 학습)")
                self.root.update()

            self.status.config(text="1/4 기존 모델 백업 중...")
            self.root.update()
            self._backup_saved_models(int(c.get("rl_total_timesteps", 20000)), skip_v2=(v2_proc is not None and v2_proc.poll() is None))

            self.status.config(text="1/4 train.py 실행 중...")
            self.root.update()
            self._run("train.py", blocking=True)

            # 기존 category_demand_models.pkl은 보존하고, 이번 학습 결과는 별도 파일로 저장한다.
            category_new_path = os.path.join(ROOT, "saved_models", "category_demand_models_new.pkl")
            self.status.config(text="2/4 category demand 새 모델 학습 중...")
            self.root.update()
            if not self._run(
                os.path.join("module3_prediction", "train_category_demand.py"),
                args=("--out", category_new_path),
                blocking=True,
            ):
                self.status.config(text="category demand 학습 실패 - 학습 중단")
                return

            # 5단계를 RL 학습보다 앞으로 옮김: demand_v2 변환이 먼저 끝나야
            # RL이 그 결과(category_demand_from_v2.pkl)를 학습 중에 참고할 수 있다.
            # (예전엔 RL 학습 뒤에 변환이 끝나서, 그 학습 사이클의 RL은 변환 결과를
            #  전혀 못 쓰고 있었음 — 구조적으로 순서가 늦어 못 쓰는 상태였음)
            self.status.config(text="3/4 새 학습 라인(demand_v2) 완료 대기 + 변환 중...")
            self.root.update()
            v2_msg = self._finish_v2_line(v2_proc, v2_state)

            # 파일 존재 여부만 보면, 예전에 만들어진 category_demand_from_v2.pkl이
            # 이번엔 변환이 실패/생략됐는데도 "이번 것"으로 오인될 수 있다.
            # 이번 호출이 실제로 변환을 완료했다는 것("saved_models/... 생성"으로 시작하는
            # 성공 메시지)까지 확인해야 안전하다.
            v2_converted_path = os.path.join(ROOT, "saved_models", "category_demand_from_v2.pkl")
            v2_converted_this_run = v2_msg.startswith("saved_models/category_demand_from_v2.pkl 생성")
            
            # [결함 수정] H3 셀 모델을 무지성으로 복제한 v2 변환본 대신, 
            # 카테고리별로 완벽하게 정상 학습된(train_category_demand.py) 모델을 항상 사용하도록 강제 고정
            category_model_for_rl = category_new_path
            rl_source_note = "category_demand_models_new.pkl (카테고리 전용 정상 학습 모델 - RL 오염 방지)"

            self.status.config(text="4/4 강화학습 실행 중...")
            self.root.update()
            rl_ok = self._run(
                os.path.join("module4_dispatch", "rl_train.py"),
                blocking=True,
                env_extra={"AI_CATEGORY_MODEL_PATH": category_model_for_rl},
            )

            # RL 백업: 위 두 학습 라인과 무관한 별도 처리 (RL이 정상 종료됐을 때만)
            rl_backup_msg = self._backup_rl_model() if rl_ok else "백업 생략 - RL 학습이 정상 종료되지 않음"

            # 이번 라운드에서 RL이 실제로 참고한 카테고리 모델을 파이프라인 고정 이름
            # (category_demand_models.pkl)으로 "승격"한다. 이렇게 해야 이번 라운드가
            # 끝난 뒤 곧바로 ▶ 플레이(measure_wait_time.py)를 눌러도, RL이 학습 때
            # 참고했던 것과 같은 수요예측을 실행 시점에도 그대로 이어서 쓴다.
            # 다음 라운드 시작 시 _backup_saved_models()가 이걸 포함해서 통째로
            # 날짜 붙여 백업해두므로, saved_models/는 항상 "최신 활성 세트"로 채워져
            # 있고 예전 세트는 saved_models_backups/에 라운드별로 쌓이는 구조가 된다.
            active_path = os.path.join(ROOT, "saved_models", "category_demand_models.pkl")
            if rl_ok and os.path.abspath(category_model_for_rl) != os.path.abspath(active_path):
                shutil.copy2(category_model_for_rl, active_path)
                promote_msg = f"category_demand_models.pkl <- {os.path.basename(category_model_for_rl)} (승격 완료)"
            elif not rl_ok:
                promote_msg = "승격 생략 - RL 학습이 정상 종료되지 않음 (기존 category_demand_models.pkl 유지)"
            else:
                promote_msg = "승격 불필요 - 이미 category_demand_models.pkl을 그대로 사용함"

            self.status.config(text="전체 학습 완료")
            messagebox.showinfo(
                "학습 완료",
                "전체 학습이 완료되었습니다.\n\n"
                "생성/갱신 파일 (saved_models/):\n"
                "• category_demand_models.pkl (활성 - 방금 승격됨)\n"
                "• category_demand_models_new.pkl / category_demand_from_v2.pkl (이번 라운드 원본)\n"
                "• rl_reposition_model.zip (module4_dispatch/, 별도 백업됨)\n\n"
                f"[승격] {promote_msg}\n"
                f"[RL이 학습에 참고한 수요예측] {rl_source_note}\n"
                f"[RL 백업] {rl_backup_msg}\n"
                f"[demand_v2 변환] {v2_msg}",
            )

        except subprocess.CalledProcessError as e:
            self.status.config(text="학습 중 오류 발생")
            messagebox.showerror(
                "학습 오류",
                f"학습 과정에서 오류가 발생했습니다.\n\n실패한 단계의 종료 코드: {e.returncode}",
            )
        except Exception as e:
            self.status.config(text="학습 중 오류 발생")
            messagebox.showerror("학습 오류", str(e))

    def run_unity_export(self):
        """유니티 재생용 JSON(map.json + 전략/알고리즘별 replay json)을 생성.
        export_unity_replay.py가 내부에서 build_env.py 재실행 + 시뮬레이션까지 다 함."""
        self.save_config_only()
        self._run("export_unity_replay.py")

    def _list_category_models(self):
        """[(표시이름, 경로)] 수요예측 모델 후보. 맨 위가 '최신 성공작'(마지막으로 RL까지 정상 종료된 학습의 모델).
        활성과 내용이 똑같은 파일(변환본 등)은 중복으로 나열하지 않고 활성 이름 뒤에 표시."""
        import filecmp
        from datetime import datetime as _dt

        def stamp(p):
            return _dt.fromtimestamp(os.path.getmtime(p)).strftime("%m-%d %H:%M")

        sm = os.path.join(ROOT, "saved_models")
        active = os.path.join(sm, "category_demand_models.pkl")
        others = [
            ("v2 변환본", os.path.join(sm, "category_demand_from_v2.pkl")),
            ("학습 중간본(_new)", os.path.join(sm, "category_demand_models_new.pkl")),
        ]
        bdir = os.path.join(ROOT, "saved_models_backups")
        if os.path.isdir(bdir):
            for ts in sorted(os.listdir(bdir), reverse=True)[:10]:
                others.append((f"백업 {ts}", os.path.join(bdir, ts, "category_demand_models.pkl")))

        out, same_as_active = [], []
        for name, p in others:
            if not os.path.exists(p):
                continue
            if os.path.exists(active) and filecmp.cmp(active, p, shallow=False):
                if not name.startswith("백업"):
                    same_as_active.append(name)
                    continue  # 활성과 같은 내용 -> 따로 나열 안 함
            out.append((f"{name} ({stamp(p)})", p))
        if os.path.exists(active):
            note = f" · {', '.join(same_as_active)}와 동일" if same_as_active else ""
            out.insert(0, (f"최신 성공작 ({stamp(active)}){note}", active))
        return out or [("최신 성공작 (모델 파일 없음)", active)]

    def open_ab_compare_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("A/B 비교하기")
        win.geometry("420x650")
        win.resizable(False, False)

        ttk.Label(
            win,
            text="비교할 값을 체크하세요. 체크된 값들의 카테시안 조합(전체 곱)이\n"
                 "2개 이상이면 비교가 진행됩니다. (항목별로는 1개만 체크해도 됨)",
            padding=10, wraplength=340, justify="left",
        ).pack()

        value_vars = {}

        for key, (label, values) in FACTOR_OPTIONS.items():
            box = ttk.LabelFrame(win, text=label, padding=8)
            box.pack(fill="x", padx=15, pady=6)
            value_vars[key] = {}
            for v in values:
                var = tk.BooleanVar(value=False)
                value_vars[key][v] = var
                ttk.Checkbutton(box, text=FACTOR_VALUE_LABELS.get(v, v), variable=var).pack(anchor="w")

        try:
            from holiday_utils import is_holiday_date
            from datetime import date as _date
            _t = _date.today()
            _today_txt = f"오늘({_t})은 {'공휴일/주말' if is_holiday_date(_t) else '평일'}"
        except Exception:
            _today_txt = "오늘 날짜로 평일/공휴일 자동 판정"
        ttk.Label(win, text=f"※ 공휴일 칸을 체크 안 하면 {_today_txt}로 실행해요.",
                  foreground="#6b7280", padding=(15, 0)).pack(anchor="w")

        models = self._list_category_models()
        model_box = ttk.LabelFrame(win, text="수요예측 모델 (선택)", padding=8)
        model_box.pack(fill="x", padx=15, pady=6)
        model_var = tk.StringVar(value=models[0][0])
        ttk.Combobox(model_box, textvariable=model_var, values=[m[0] for m in models],
                     state="readonly", width=44).pack(fill="x")
        ttk.Label(model_box, text="rl_prepositioned 전략이 참고하는 수요예측입니다.",
                  foreground="#6b7280").pack(anchor="w", pady=(4, 0))
        ttk.Label(win, text="※ 비교로 생긴 로그를 학습에 쓰려면 '학습만 실행' 창에서 고르세요.",
                  foreground="#6b7280", padding=(15, 0)).pack(anchor="w")

        def run_compare():
            selected = {}
            for key, (label, values) in FACTOR_OPTIONS.items():
                chosen = [v for v, var in value_vars[key].items() if var.get()]
                if chosen:
                    selected[key] = chosen

            if not selected:
                messagebox.showwarning("경고", "비교할 항목을 하나 이상 체크하세요.")
                return

            total_combos = 1
            for chosen in selected.values():
                total_combos *= len(chosen)
            if total_combos < 2:
                messagebox.showwarning(
                    "경고",
                    f"현재 체크된 조합은 {total_combos}개뿐이라 비교가 안 됩니다. "
                    f"전체 조합(항목별 체크 수를 곱한 값)이 2개 이상 되도록 더 체크하세요.",
                )
                return

            import itertools
            keys = list(selected.keys())
            combos = [dict(zip(keys, combo)) for combo in itertools.product(*[selected[k] for k in keys])]

            if len(combos) >= AB_WARN_COMBOS:
                if not messagebox.askyesno(
                        "⚠ 조합이 많아요",
                        f"조합이 {len(combos)}개라 SUMO 창이 {len(combos)}개 동시에 뜹니다.\n"
                        f"PC 메모리/CPU가 부족하면 느려지거나 멈출 수 있어요 ({AB_WARN_COMBOS}개 이상 경고).\n\n"
                        f"그래도 실행할까요?"):
                    return

            if "holiday_mode" not in selected:
                for _c in combos:
                    _c["holiday_mode"] = "today"   # 미체크: 오늘 날짜 기준 자동 판정

            win.destroy()
            self.save_config_only()
            model_path = dict(models)[model_var.get()]
            self._run("multi_factor_compare.py", [json.dumps(combos, ensure_ascii=False)],
                      env_extra={"AI_CATEGORY_MODEL_PATH": model_path})

        ttk.Button(win, text="실행 (조합별로 새 창에서 병렬 측정)", command=run_compare).pack(pady=15, fill="x", padx=15)

    def _build_log_picker(self, parent, persist=True):
        """학습 팝업 안에 들어가는 '학습에 쓸 로그' 선택 목록. 반환값 save()를 호출하면 selected_logs.json에 저장.
        로그가 없으면 None."""
        import log_selection as ls
        logs = ls.list_logs()
        if not logs:
            ttk.Label(parent, text="data/sim_logs에 로그가 아직 없습니다. (시뮬레이션 먼저 돌리기를 켜면 만들어집니다)",
                      foreground="#6b7280").pack(anchor="w")
            return None
        saved = ls.load_selection()
        checked = {r["filename"] for r in logs} if saved is None else set(saved)
        by_name = {r["filename"]: r for r in logs}

        tree = ttk.Treeview(parent, columns=("chk", "date", "kind", "label", "rows"), show="headings",
                            selectmode="browse", height=9)
        for c, t, w in (("chk", "사용", 45), ("date", "날짜", 90), ("kind", "구분", 55), ("label", "구역·전략_알고리즘", 330), ("rows", "행 수", 70)):
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor="w" if c == "label" else "center")
        tree.pack(fill="both", expand=True)
        info = ttk.Label(parent, text="", foreground="#b45309", wraplength=620, justify="left")
        info.pack(anchor="w", pady=4)

        def row_vals(fn):
            r = by_name[fn]
            kind = "-" if r["is_holiday"] is None else ("공휴일" if r["is_holiday"] else "평일")
            return ("☑" if fn in checked else "☐", r["date"] or "-", kind, r["label"], f"{r['rows']:,}")

        def refresh_info():
            sel = [by_name[f] for f in checked]
            n_h = sum(1 for r in sel if r["is_holiday"])
            n_w = sum(1 for r in sel if r["is_holiday"] is False)
            msg = f"선택 {len(sel)}개 (평일 {n_w} · 공휴일 {n_h}) · {sum(r['rows'] for r in sel):,}행"
            g = ls.same_date_groups(checked)
            if g:
                msg += "\n⚠ 같은 날짜가 겹쳐 그날 수요가 2배로 합쳐집니다 → 날짜당 하나만 남기세요: " + \
                       " / ".join(f"{d}({len(v)}개)" for d, v in sorted(g.items()))
            info.config(text=msg)

        for r in logs:
            tree.insert("", "end", iid=r["filename"], values=row_vals(r["filename"]))
        refresh_info()

        def on_click(event):
            fn = tree.identify_row(event.y)
            if tree.identify_region(event.x, event.y) != "cell" or not fn:
                return
            checked.symmetric_difference_update({fn})
            tree.item(fn, values=row_vals(fn))
            refresh_info()
        tree.bind("<Button-1>", on_click)

        def set_all(on):
            checked.clear()
            if on:
                checked.update(by_name)
            for fn in by_name:
                tree.item(fn, values=row_vals(fn))
            refresh_info()

        bar = ttk.Frame(parent)
        bar.pack(anchor="w")
        ttk.Button(bar, text="전체 선택", command=lambda: set_all(True)).pack(side="left", padx=2)
        ttk.Button(bar, text="전체 해제", command=lambda: set_all(False)).pack(side="left", padx=2)

        def save():
            """True=진행 가능, False=취소."""
            if not checked:
                messagebox.showwarning("학습 로그", "최소 1개는 선택해야 합니다.")
                return False
            if ls.same_date_groups(checked) and not messagebox.askyesno(
                    "같은 날짜 겹침", "같은 날짜 로그가 함께 선택돼 있어 그날 수요가 겹쳐 집계됩니다. 그래도 진행할까요?"):
                return False
            if not persist:
                return sorted(checked)      # 분석 팝업: 학습 선택 파일은 건드리지 않고 목록만 돌려줌
            if checked == set(by_name):
                ls.clear_selection()        # 전부 선택 = 선택 안 함(이후 새 로그도 자동 포함)
            else:
                ls.save_selection(checked)
            return True
        return save

    def open_analysis_dialog(self):
        """'분석하기' 팝업: 분석할 로그를 고르면 analyze_demand_logs.py가 평일/공휴일·전략별로 비교한 리포트를 만든다."""
        win = tk.Toplevel(self.root)
        win.title("로그 분석하기")
        win.geometry("700x600")
        top = ttk.LabelFrame(win, text="🗺 지도·순위 보기 (브라우저)", padding=8)
        top.pack(fill="x", padx=12, pady=(10, 4))
        ttk.Label(top, text="출발 지도, 전략·알고리즘 순위, 시간대별 그래프를 브라우저에서 봐요.\n"
                            "로그는 거기서 체크박스로 고르면 돼요. (끝낼 때는 열린 콘솔 창을 닫기)",
                  justify="left").pack(side="left")
        ttk.Button(top, text="브라우저로 열기",
                   command=lambda: (win.destroy(), self._run("analyzer_server.py"))).pack(side="right")
        ttk.Label(win, text="아래는 표/CSV/그래프 리포트(report.md)를 만들 때 쓸 로그예요. (학습용 로그 선택에는 영향 없음)\n"
                            "결과는 data/analysis/<날짜_시각>/ 에 저장돼요.",
                  padding=(12, 6), justify="left").pack(anchor="w")
        box = ttk.LabelFrame(win, text="리포트로 분석할 로그", padding=8)
        box.pack(fill="both", expand=True, padx=12, pady=6)
        pick = self._build_log_picker(box, persist=False)
        if pick is None:
            return

        def start():
            names = pick()
            if not names:
                return
            win.destroy()
            paths = [os.path.join(ROOT, "data", "sim_logs", n) for n in names]
            self._run("analyze_demand_logs.py", ["--logs", *paths])

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=12, pady=10)
        ttk.Button(bar, text="취소", command=win.destroy).pack(side="right", padx=3)
        ttk.Button(bar, text="리포트 만들기", command=start).pack(side="right", padx=3)

    def open_training_dialog(self):
        """'학습만 실행' 팝업: 학습에 쓸 로그 + 학습 전 옵션을 여기서 정하고 시작."""
        win = tk.Toplevel(self.root)
        win.title("학습만 실행")
        win.geometry("700x560")
        ttk.Label(win, text="이 창에서 정한 대로 학습이 시작됩니다.\n"
                            "순서: 맵 재생성 → (시뮬레이션으로 로그 생성) → 예측모델 학습 → 강화학습",
                  padding=10, justify="left").pack(anchor="w")

        opt = ttk.LabelFrame(win, text="학습 전 옵션", padding=8)
        opt.pack(fill="x", padx=12, pady=4)
        sim_var = self.bool_vars["run_sim_before_training"]
        v2_var = self.bool_vars["force_regenerate_demand_v2"]
        # 사용자가 기존 로그를 골라 둔 경우, 그 선택만으로 학습하는 것이 안전한 기본값이다.
        # 추가 시뮬레이션은 선택 로그를 보완하는 것이 아니라 새 로그를 학습 목록에 섞는다.
        import log_selection as _training_log_selection
        if _training_log_selection.load_selection() is not None:
            sim_var.set(False)
            ttk.Label(opt, text="선택된 로그만 학습하도록 '3회 추가 생성'을 자동 해제했습니다.",
                      foreground="#b45309").pack(anchor="w")
        ttk.Checkbutton(opt, text="시뮬레이션 3회 먼저 돌려 새 로그도 학습에 추가 (선택 로그만 쓰려면 해제)", variable=sim_var).pack(anchor="w")
        ttk.Checkbutton(opt, text="demand_v2.joblib이 있어도 다시 생성 (기존 파일은 demand_v2_backups/에 백업)", variable=v2_var).pack(anchor="w")

        box = ttk.LabelFrame(win, text="학습에 쓸 로그  (새로 만든 로그는 선택 목록에 자동으로 추가됩니다)", padding=8)
        box.pack(fill="both", expand=True, padx=12, pady=6)
        save_sel = self._build_log_picker(box)

        def start():
            if save_sel is not None and not save_sel():
                return
            win.destroy()
            self.run_training()

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=12, pady=10)
        ttk.Button(bar, text="취소", command=win.destroy).pack(side="right", padx=3)
        ttk.Button(bar, text="학습 시작", command=start).pack(side="right", padx=3)


if __name__ == "__main__":
    root = tk.Tk()
    ConfigGUI(root)
    root.mainloop()
