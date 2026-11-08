"""AI Mobility 전체 설정 GUI. GUI의 모든 실험/시뮬레이션 파라미터는 config.json으로 저장됩니다."""
import json, os, shutil, subprocess, sys, tkinter as tk, zipfile

# 이 스크립트(GUI) 자신이 print()로 콘솔에 직접 찍는 한글이, 컴퓨터/콘솔의 기본
# 코드페이지(cp949 등)에 걸려 깨지는 걸 방지. _run()이 자식 프로세스에 넣어주는
# PYTHONIOENCODING=utf-8는 이 프로세스 자기 자신에게는 적용되지 않기 때문에 따로 필요함.
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime
from tkinter import ttk, messagebox, simpledialog, filedialog
from region_autocomplete import RegionSearchEntry
from config_loader import DEFAULT_CONFIG

ROOT = os.path.dirname(os.path.abspath(__file__))
PRESET_DIR = os.path.join(ROOT, "presets")
os.makedirs(PRESET_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(ROOT, "config.json")
FREQ_OPTIONS = ["1min", "5min", "10min", "15min", "30min"]
STRATEGIES = ["patrol", "prepositioned", "rl_prepositioned", "forecast"]

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
        ("real_demand_scale", "실측 수요 배율 (1.0=영역 실측 전체)", 0.01, 5, 2, 0),
        ("real_internal_trip_km", "영역 안 하차 기준 거리(km)", 0.1, 10, 2, 0),
        ("real_demand_scale", "실측 수요 배율 (1.0=영역 실측 전체)", 0.01, 5, 2, 0),
        ("real_internal_trip_km", "영역 안 하차 기준 거리(km)", 0.1, 10, 2, 0),
        ("real_demand_scale", "실측 수요 배율 (1.0=영역 실측 전체)", 0.01, 5, 2, 0),
        ("real_internal_trip_km", "영역 안 하차 기준 거리(km)", 0.1, 10, 2, 0),
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
    ("demand_source", "수요 출처 (real=실측보정)", ["real", "synthetic"]),
    ("demand_source", "수요 출처 (real=실측보정)", ["real", "synthetic"]),
    ("demand_source", "수요 출처 (real=실측보정)", ["synthetic", "real"]),
    ("holiday_mode", "공휴일 판정 모드", ["auto", "weekday", "holiday"]),
    ("run_date_mode", "실행 날짜 방식", ["alternate", "sim_date", "random"]),
]



class TimelineRangePicker(ttk.Frame):
    """
    2025년 국토부 TIMS 실데이터(365일) 전용 타임라인 기간 선택기 위젯.
    - 데이터 처음(2025-01-01)부터 시작
    - 2주(14일) / 4주(28일, 2배) / 8주(56일, 4배) 고정 길이 바를 좌우로 와리가리 이동
    - 체크 해제 시 시작/종료일을 마음대로 자유롭게 범위 조절
    """
    def __init__(self, parent, year=2025):
        super().__init__(parent)
        import datetime as dt
        self.dt = dt
        self.year = year
        self.base_date = dt.date(self.year, 1, 1)
        self.total_days = 365

        # 상태 변수
        self.fixed_mode_var = tk.BooleanVar(value=True)   # 고정 바 모드 여부
        self.preset_weeks_var = tk.IntVar(value=2)        # 2, 4, 8주
        self.start_day_var = tk.IntVar(value=0)           # 0 ~ 364 (데이터 처음 2025-01-01 시작)
        self.end_day_var = tk.IntVar(value=13)            # 14일치 (2주)

        self.canvas_width = 620
        self.canvas_height = 76

        self._build_ui()
        self._on_preset_change()

    def _build_ui(self):
        # 1. 시각적 타임라인 캔버스
        cv_frame = ttk.Frame(self)
        cv_frame.pack(fill="x", padx=4, pady=(2, 6))

        self.canvas = tk.Canvas(cv_frame, height=self.canvas_height, bg="#f8fafc",
                                highlightthickness=1, highlightbackground="#cbd5e1", cursor="hand2")
        self.canvas.pack(fill="x", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)

        # 2. 컨트롤 패널
        ctrl_frame = ttk.Frame(self)
        ctrl_frame.pack(fill="x", padx=4, pady=2)

        # 고정 바 모드 토글 체크박스 (체크 해제 시 자유 조절)
        top_row = ttk.Frame(ctrl_frame)
        top_row.pack(fill="x", pady=(0, 4))
        self.mode_cb = ttk.Checkbutton(
            top_row,
            text="🔒 고정 길이 바 모드 (체크 해제 시 시작/종료일 자유 조절)",
            variable=self.fixed_mode_var,
            command=self._on_mode_toggle
        )
        self.mode_cb.pack(side="left")

        # [고정 모드] 바 길이 라디오 버튼들 (2주, 4주, 8주)
        self.fixed_panel = ttk.Frame(ctrl_frame)
        self.fixed_panel.pack(fill="x", pady=2)

        ttk.Label(self.fixed_panel, text="바 길이:", font=("맑은 고딕", 9, "bold")).pack(side="left", padx=(0, 6))
        for w, text in [(2, "2주 (14일, 기본)"), (4, "4주 (28일, 2배 길이)"), (8, "8주 (56일, 4배 길이)")]:
            rb = ttk.Radiobutton(self.fixed_panel, text=text, value=w,
                                 variable=self.preset_weeks_var, command=self._on_preset_change)
            rb.pack(side="left", padx=5)

        # [고정 모드] 슬라이더 및 이동 버튼들 (와리가리)
        self.pos_panel = ttk.Frame(ctrl_frame)
        self.pos_panel.pack(fill="x", pady=4)

        ttk.Label(self.pos_panel, text="기간 이동 (와리가리):", font=("맑은 고딕", 9)).pack(side="left", padx=(0, 4))
        self.pos_scale = ttk.Scale(self.pos_panel, from_=0, to=self.total_days - 14,
                                   variable=self.start_day_var, orient="horizontal",
                                   command=self._on_pos_slide)
        self.pos_scale.pack(side="left", fill="x", expand=True, padx=4)

        # 빠른 이동 버튼들
        btn_row = ttk.Frame(ctrl_frame)
        btn_row.pack(fill="x", pady=(0, 4))
        ttk.Button(btn_row, text="⏮ 데이터 처음 (1월 1일)", width=17,
                   command=lambda: self._set_start_offset(0)).pack(side="left", padx=2)
        ttk.Button(btn_row, text="◀ -1주", width=8,
                   command=lambda: self._nudge(-7)).pack(side="left", padx=2)
        ttk.Button(btn_row, text="+1주 ▶", width=8,
                   command=lambda: self._nudge(7)).pack(side="left", padx=2)
        ttk.Button(btn_row, text="⏭ 연말 (12월 31일)", width=17,
                   command=self._jump_to_end).pack(side="left", padx=2)
        self.btn_row = btn_row

        # [자유 모드] 시작일 / 종료일 독립 슬라이더 (기본 숨김)
        self.free_panel = ttk.Frame(ctrl_frame)

        f_start_row = ttk.Frame(self.free_panel)
        f_start_row.pack(fill="x", pady=2)
        ttk.Label(f_start_row, text="시작일 조절:", width=12, font=("맑은 고딕", 9)).pack(side="left")
        self.start_scale = ttk.Scale(f_start_row, from_=0, to=self.total_days - 1,
                                     variable=self.start_day_var, orient="horizontal",
                                     command=self._on_free_start_slide)
        self.start_scale.pack(side="left", fill="x", expand=True, padx=4)

        f_end_row = ttk.Frame(self.free_panel)
        f_end_row.pack(fill="x", pady=2)
        ttk.Label(f_end_row, text="종료일 조절:", width=12, font=("맑은 고딕", 9)).pack(side="left")
        self.end_scale = ttk.Scale(f_end_row, from_=0, to=self.total_days - 1,
                                   variable=self.end_day_var, orient="horizontal",
                                   command=self._on_free_end_slide)
        self.end_scale.pack(side="left", fill="x", expand=True, padx=4)

        # 3. 상세 정보 카드
        self.info_box = ttk.LabelFrame(self, text="📊 선택된 실데이터 기간 정보", padding=8)
        self.info_box.pack(fill="x", padx=4, pady=6)

        self.info_lbl = ttk.Label(self.info_box, text="", font=("맑은 고딕", 9), justify="left")
        self.info_lbl.pack(anchor="w")

    def _on_resize(self, event):
        if event.width > 50:
            self.canvas_width = event.width
            self._redraw()

    def _on_mode_toggle(self):
        if self.fixed_mode_var.get():
            self.free_panel.pack_forget()
            self.fixed_panel.pack(fill="x", pady=2)
            self.pos_panel.pack(fill="x", pady=4)
            self.btn_row.pack(fill="x", pady=(0, 4))
            self._on_preset_change()
        else:
            self.fixed_panel.pack_forget()
            self.pos_panel.pack_forget()
            self.btn_row.pack_forget()
            self.free_panel.pack(fill="x", pady=4)
            self._redraw()

    def _on_preset_change(self):
        w = self.preset_weeks_var.get()
        d_len = w * 7
        max_start = self.total_days - d_len
        self.pos_scale.config(to=max_start)
        cur_start = min(self.start_day_var.get(), max_start)
        self.start_day_var.set(cur_start)
        self.end_day_var.set(cur_start + d_len - 1)
        self._redraw()

    def _set_start_offset(self, val):
        w = self.preset_weeks_var.get()
        d_len = w * 7
        max_start = self.total_days - d_len
        s = max(0, min(val, max_start))
        self.start_day_var.set(s)
        self.end_day_var.set(s + d_len - 1)
        self._redraw()

    def _nudge(self, delta):
        cur_s = self.start_day_var.get()
        self._set_start_offset(cur_s + delta)

    def _jump_to_end(self):
        w = self.preset_weeks_var.get()
        d_len = w * 7
        self._set_start_offset(self.total_days - d_len)

    def _on_pos_slide(self, val):
        w = self.preset_weeks_var.get()
        d_len = w * 7
        s = int(float(val))
        s = max(0, min(s, self.total_days - d_len))
        self.start_day_var.set(s)
        self.end_day_var.set(s + d_len - 1)
        self._redraw()

    def _on_free_start_slide(self, val):
        s = int(float(val))
        e = self.end_day_var.get()
        if s > e:
            self.end_day_var.set(s)
        self._redraw()

    def _on_free_end_slide(self, val):
        e = int(float(val))
        s = self.start_day_var.get()
        if e < s:
            self.start_day_var.set(e)
        self._redraw()

    def _on_canvas_click(self, event):
        margin = 14
        w = max(10, self.canvas_width - 2 * margin)
        click_x = max(margin, min(event.x, margin + w))
        day = int(((click_x - margin) / w) * self.total_days)
        day = max(0, min(day, self.total_days - 1))

        if self.fixed_mode_var.get():
            w_preset = self.preset_weeks_var.get()
            d_len = w_preset * 7
            s = max(0, min(day - d_len // 2, self.total_days - d_len))
            self.start_day_var.set(s)
            self.end_day_var.set(s + d_len - 1)
        else:
            s = self.start_day_var.get()
            e = self.end_day_var.get()
            if abs(day - s) < abs(day - e):
                self.start_day_var.set(min(day, e))
            else:
                self.end_day_var.set(max(day, s))
        self._redraw()

    def _on_canvas_drag(self, event):
        self._on_canvas_click(event)

    def _redraw(self):
        s = self.start_day_var.get()
        e = self.end_day_var.get()
        start_date = self.base_date + self.dt.timedelta(days=s)
        end_date = self.base_date + self.dt.timedelta(days=e)
        total_d = (end_date - start_date).days + 1
        weeks_calc = total_d / 7.0

        self.canvas.delete("all")
        margin = 14
        w = max(10, self.canvas_width - 2 * margin)

        # 1) 2025년 전체 365일 트랙 배경
        bar_y1 = 20
        bar_y2 = 48
        self.canvas.create_rectangle(margin, bar_y1, margin + w, bar_y2, fill="#e2e8f0", outline="#cbd5e1", width=1)

        # 2) 월별 눈금 및 라벨 (1월 ~ 12월)
        for m in range(1, 13):
            m_first_day = (self.dt.date(self.year, m, 1) - self.base_date).days
            mx = margin + (m_first_day / self.total_days) * w
            self.canvas.create_line(mx, bar_y1, mx, bar_y2, fill="#94a3b8", width=1)

            next_m = self.dt.date(self.year + 1, 1, 1) if m == 12 else self.dt.date(self.year, m + 1, 1)
            m_days = (next_m - self.dt.date(self.year, m, 1)).days
            center_x = margin + ((m_first_day + m_days / 2) / self.total_days) * w
            self.canvas.create_text(center_x, 60, text=f"{m}월", font=("맑은 고딕", 8), fill="#64748b")

        # 3) 선택된 기간 활성 바 (바 길이 2배, 4배 등 시각적 표시)
        x1 = margin + (s / self.total_days) * w
        x2 = margin + ((e + 1) / self.total_days) * w
        self.canvas.create_rectangle(x1, bar_y1 - 2, x2, bar_y2 + 2, fill="#2563eb", outline="#1d4ed8", width=2)

        # 바 내부/상단 텍스트
        bar_text = f"{total_d}일 ({weeks_calc:.1f}주)"
        mid_x = (x1 + x2) / 2
        if x2 - x1 >= 50:
            self.canvas.create_text(mid_x, (bar_y1 + bar_y2) / 2, text=bar_text, fill="white", font=("맑은 고딕", 9, "bold"))
        else:
            self.canvas.create_text(mid_x, bar_y1 - 9, text=bar_text, fill="#1d4ed8", font=("맑은 고딕", 8, "bold"))

        # 시작/종료 날짜 태그
        self.canvas.create_text(x1, bar_y1 - 9, text=str(start_date), font=("맑은 고딕", 8, "bold"), fill="#1e40af", anchor="s")
        if x2 - x1 >= 100:
            self.canvas.create_text(x2, bar_y1 - 9, text=str(end_date), font=("맑은 고딕", 8, "bold"), fill="#1e40af", anchor="s")

        # 4) 정보 텍스트 갱신
        exp_calls = total_d * 9000
        if self.fixed_mode_var.get():
            mode_desc = f"고정 바 모드 ({self.preset_weeks_var.get()}주 / {total_d}일치 바를 와리가리 이동 가능)"
        else:
            mode_desc = "자유 조절 모드 (시작일/종료일 자유 지정)"

        info_text = (
            f"📌 선택된 기간: {start_date} ~ {end_date} (총 {total_d}일간 / {weeks_calc:.1f}주)\n"
            f"📈 예상 호출량: 약 {exp_calls:,}건 (국토교통부 TIMS 500m 격자 실측 연동)\n"
            f"⚙ 동작 모드: {mode_desc}"
        )
        self.info_lbl.config(text=info_text)

    def get_selection(self):
        s = self.start_day_var.get()
        e = self.end_day_var.get()
        start_date = self.base_date + self.dt.timedelta(days=s)
        end_date = self.base_date + self.dt.timedelta(days=e)
        days = (end_date - start_date).days + 1
        return start_date, end_date, days


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
        ttk.Button(bf, text="🎬 유니티 재생 생성기", command=self.open_unity_replay_dialog).pack(side="left", fill="x", expand=True, padx=3)

        # [데이터 품질 검사] 원본 로그를 삭제/수정하지 않고 이상 여부만 검사한다.
        quality_bar = ttk.Frame(bottom_frame)
        quality_bar.pack(fill="x", padx=12, pady=(0, 4))
        ttk.Button(quality_bar, text="📊 실측 수요 데이터 품질 검사", command=self.open_data_quality_checker).pack(
            side="left", fill="x", expand=True, padx=3
        )
        ttk.Button(quality_bar, text="📦 현재 모델 스냅샷 저장 (날짜+택시계수)", command=self.snapshot_current_models).pack(
            side="left", fill="x", expand=True, padx=3
        )

        self.status = ttk.Label(bottom_frame, text="현재 config.json을 불러오는 중...")
        self.status.pack(anchor="w", padx=12, pady=(0, 8))

    def _create_slider_row(self, parent, key, label, lo, hi, dec, isint):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        lbl = ttk.Label(row, text=label, width=34)
        lbl.pack(side="left")
        var = tk.DoubleVar(value=DEFAULT_CONFIG.get(key, 0))
        self.vars[key] = (var, dec, isint)
        entry = ttk.Entry(row, textvariable=var, width=12)
        entry.pack(side="right", padx=4)
        scale = ttk.Scale(row, from_=lo, to=hi, variable=var, length=300)
        scale.pack(side="right", fill="x", expand=True)
        if not hasattr(self, "slider_widgets"):
            self.slider_widgets = {}
        self.slider_widgets[key] = (entry, scale, lbl, row)

    def _build_collapsible_module1(self, parent, items):
        categories = {
            "기본 맵 & 차량 설정": [
                "grid_x", "grid_y", "grid_length", "num_normal_cars",
                "num_taxis", "num_auto_cars", "num_obstacles", "num_passengers",
            ],
            "실데이터 수요 (Real Demand)": [
                "real_demand_scale", "real_internal_trip_km",
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
            if not hasattr(self, "collapsible_labels"):
                self.collapsible_labels = {}
            self.collapsible_labels[cat_name] = lbl

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
        if not hasattr(self, "choice_widgets"):
            self.choice_widgets = {}
        for key, label, vals in CHOICES:
            r = ttk.Frame(f)
            r.pack(fill="x", pady=5)
            ttk.Label(r, text=label, width=30).pack(side="left")
            v = tk.StringVar()
            self.choice_vars[key] = v
            cb = ttk.Combobox(r, textvariable=v, values=vals, state="readonly", width=20)
            cb.pack(side="right")
            self.choice_widgets[key] = (cb, r)
            if key == "demand_source":
                cb.bind("<<ComboboxSelected>>", lambda e: self._update_demand_source_ui())

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

    def _update_demand_source_ui(self):
        source = self.choice_vars.get("demand_source", tk.StringVar(value="real")).get()
        is_real = (source == "real")

        # real 모드일 때 사용되지 않는 합성 카테고리 스폰 모수들
        synthetic_only_keys = [
            "num_passengers",
            "residential_base_pop_per_edge", "residential_schedule_scale", "residential_taxi_probability",
            "residential_out_hour", "residential_out_probability", "residential_evening_in_hour",
            "residential_evening_in_probability", "residential_late_in_hour", "residential_late_in_probability",
            "school_pop_base", "school_start_hour", "school_end_hour", "school_taxi_peak_hour",
            "school_afternoon_start_hour", "school_afternoon_end_hour", "school_afternoon_fraction",
            "company_pop_base", "company_start_hour", "company_end_hour", "company_taxi_peak_hour",
            "lunch_start_hour", "lunch_end_hour", "lunch_company_release_fraction", "lunch_taxi_fraction",
            "evening_start_hour", "evening_end_hour", "evening_taxi_fraction", "late_evening_start_hour",
            "late_evening_end_hour", "evening_residential_fraction", "restaurant_stay_sec",
            "restaurant_pop_base", "restaurant_window1_start", "restaurant_window1_end",
            "restaurant_window2_start", "restaurant_window2_end", "restaurant_civilian_taxi_probability",
            "department_pop_base", "department_window_start", "department_window_end",
            "department_taxi_probability", "department_stay_sec",
        ]
        real_only_keys = ["real_demand_scale", "real_internal_trip_km"]

        if hasattr(self, "slider_widgets"):
            for k in synthetic_only_keys:
                if k in self.slider_widgets:
                    entry, scale, lbl, _ = self.slider_widgets[k]
                    st = "disabled" if is_real else "normal"
                    entry.config(state=st)
                    scale.config(state=st)
                    lbl.config(foreground="gray" if is_real else "")

            for k in real_only_keys:
                if k in self.slider_widgets:
                    entry, scale, lbl, _ = self.slider_widgets[k]
                    st = "normal" if is_real else "disabled"
                    entry.config(state=st)
                    scale.config(state=st)
                    lbl.config(foreground="" if is_real else "gray")

        if hasattr(self, "choice_widgets") and "passenger_mode" in self.choice_widgets:
            cb, _ = self.choice_widgets["passenger_mode"]
            cb.config(state="disabled" if is_real else "readonly")

        if hasattr(self, "collapsible_labels"):
            cat_mapping = {
                "주거 지역 (Residential)": "주거 지역 (Residential) [real모드 미사용]" if is_real else "주거 지역 (Residential)",
                "학교 (School)": "학교 (School) [real모드 미사용]" if is_real else "학교 (School)",
                "회사 및 점심 (Company & Lunch)": "회사 및 점심 (Company & Lunch) [real모드 미사용]" if is_real else "회사 및 점심 (Company & Lunch)",
                "퇴근 및 저녁/음식점 (Evening & Restaurant)": "퇴근 및 저녁/음식점 (Evening & Restaurant) [real모드 미사용]" if is_real else "퇴근 및 저녁/음식점 (Evening & Restaurant)",
                "백화점 (Department Store)": "백화점 (Department Store) [real모드 미사용]" if is_real else "백화점 (Department Store)",
                "실데이터 수요 (Real Demand)": "실데이터 수요 (Real Demand)" if is_real else "실데이터 수요 (Real Demand) [synthetic모드 미사용]",
            }
            for cat_name, label_widget in self.collapsible_labels.items():
                if cat_name in cat_mapping:
                    label_widget.config(text=cat_mapping[cat_name], foreground="gray" if ("[real모드 미사용]" in cat_mapping[cat_name] or "[synthetic모드 미사용]" in cat_mapping[cat_name]) else "")

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
        self._update_demand_source_ui()

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

    # 프리셋을 불러와도 덮어쓰지 않을 소멸 방지 핵심 설정
    _PRESET_PROTECTED_KEYS = {
        "sumo_time_to_teleport": 120,
        "taxi_remaining_edges_threshold": 5,
        "taxi_avoid_dead_ends": True,
        "taxi_rl_keepalive": True,
    }

    def load_preset(self):
        n = self.preset_combo.get()
        if not n:
            return
        with open(os.path.join(PRESET_DIR, n + ".json"), encoding="utf-8") as f:
            loaded = json.load(f)
        # 소멸 방지 핵심 설정은 프리셋 값이 더 보수적(작은/느슨한)일 때만 보호
        for key, safe_val in self._PRESET_PROTECTED_KEYS.items():
            preset_val = loaded.get(key)
            if preset_val is None:
                continue  # 프리셋에 없으면 기본값 유지
            # 숫자형: 프리셋 값이 최솟값보다 작으면 보호값 사용
            if isinstance(safe_val, (int, float)) and isinstance(preset_val, (int, float)):
                if preset_val < safe_val:
                    loaded[key] = safe_val
            # bool형: True가 보호값이면 프리셋이 False여도 True 유지
            elif isinstance(safe_val, bool) and safe_val is True:
                loaded[key] = True
        self._loaded_config = {**DEFAULT_CONFIG, **loaded}
        self.set_values(self._loaded_config)
        self.status.config(text=f"프리셋 '{n}' 불러오기 완료 (소멸방지 설정 보호됨)")

    def delete_preset(self):
        n = self.preset_combo.get()
        if n and messagebox.askyesno("확인", f"'{n}' 삭제?"):
            os.remove(os.path.join(PRESET_DIR, n + ".json"))
            self.refresh_presets()

    def _env(self):
        e = os.environ.copy()
        e["PYTHONIOENCODING"] = "utf-8"
        e["PYTHONUTF8"] = "1"
        e["PYTHONHASHSEED"] = "0"  # 실행 재현성 (reproducibility.py 참고)
        e["PYTHONHASHSEED"] = "0"  # 실행 재현성 (reproducibility.py 참고)
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

    def snapshot_current_models(self):
        """현재 saved_models의 수요예측 모델들과 module4_dispatch의 RL 모델을
        '날짜_taxi{대수}' 폴더에 스냅샷으로 영구 보관합니다."""
        c = self.current()
        num_taxis = int(c.get("num_taxis", 70))
        today_str = datetime.now().strftime("%m%d")
        default_name = f"{today_str}_taxi{num_taxis}"

        sm_dir = os.path.join(ROOT, "saved_models")
        os.makedirs(sm_dir, exist_ok=True)
        cand_name = default_name
        cnt = 1
        while os.path.exists(os.path.join(sm_dir, cand_name)):
            cnt += 1
            cand_name = f"{default_name}_{cnt}"

        from tkinter import simpledialog
        folder_name = simpledialog.askstring(
            "모델 스냅샷 저장",
            f"현재 RL 및 수요예측 모델을 보관할 폴더명을 입력하세요:\n(saved_models/<폴더명> 에 저장됩니다)",
            initialvalue=cand_name,
            parent=self.root,
        )
        if not folder_name:
            return
        folder_name = folder_name.strip()
        dst_dir = os.path.join(sm_dir, folder_name)
        os.makedirs(dst_dir, exist_ok=True)

        copied_files = []
        # 1. saved_models 루트의 모델 파일들 복사
        for fname in os.listdir(sm_dir):
            fpath = os.path.join(sm_dir, fname)
            if os.path.isfile(fpath) and (fname.endswith(".pkl") or fname.endswith(".joblib") or fname.endswith(".pt")):
                shutil.copy2(fpath, os.path.join(dst_dir, fname))
                copied_files.append(fname)

        # 2. module4_dispatch의 RL 모델 파일들 복사
        m4_dir = os.path.join(ROOT, "module4_dispatch")
        rl_zip = os.path.join(m4_dir, "rl_reposition_model.zip")
        rl_norm = os.path.join(m4_dir, "rl_reposition_model_vecnorm.pkl")
        if os.path.exists(rl_zip):
            shutil.copy2(rl_zip, os.path.join(dst_dir, "rl_reposition_model.zip"))
            copied_files.append("rl_reposition_model.zip")
        if os.path.exists(rl_norm):
            shutil.copy2(rl_norm, os.path.join(dst_dir, "rl_reposition_model_vecnorm.pkl"))
            copied_files.append("rl_reposition_model_vecnorm.pkl")

        # 3. 스냅샷 메타정보 저장
        meta_info = {
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "folder_name": folder_name,
            "num_taxis": num_taxis,
            "demand_source": c.get("demand_source", "real"),
            "real_demand_scale": c.get("real_demand_scale", 1.0),
            "rl_reward_mode": c.get("rl_reward_mode", "default"),
            "files": copied_files,
        }
        with open(os.path.join(dst_dir, "snapshot_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta_info, f, ensure_ascii=False, indent=2)

        messagebox.showinfo(
            "저장 완료",
            f"현재 모델 스냅샷이 성공적으로 저장되었습니다!\n\n"
            f"📁 위치: saved_models/{folder_name}\n"
            f"📄 포함 파일: {len(copied_files)}개 (RL zip, 수요예측 pkl 등)\n\n"
            f"A/B 테스트 창의 '선호 모델 프리셋'에서 바로 선택하여 비교하실 수 있습니다."
        )
        self.status.config(text=f"모델 스냅샷 저장 완료: saved_models/{folder_name}")

    def _list_model_snapshots(self):
        """saved_models/ 하위의 스냅샷 폴더들을 탐색하여
        [(라벨, dir_path, category_model_path, rl_model_path, meta)] 리스트 반환."""
        sm_dir = os.path.join(ROOT, "saved_models")
        results = [
            ("기본 (현재 활성 모델)", sm_dir,
             os.path.join(sm_dir, "category_demand_models.pkl"),
             os.path.join(ROOT, "module4_dispatch", "rl_reposition_model.zip"),
             {"num_taxis": self.current().get("num_taxis", 70)}),
        ]
        if not os.path.isdir(sm_dir):
            return results

        for name in sorted(os.listdir(sm_dir)):
            p = os.path.join(sm_dir, name)
            if not os.path.isdir(p) or name.startswith(".") or name == "saved_models_backups":
                continue
            cat_path = os.path.join(p, "category_demand_models.pkl")
            rl_path = os.path.join(p, "rl_reposition_model.zip")
            meta_path = os.path.join(p, "snapshot_meta.json")

            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as mf:
                        meta = json.load(mf)
                except Exception:
                    pass

            if os.path.exists(cat_path) or os.path.exists(rl_path):
                taxi_str = f"택시 {meta.get('num_taxis', '?')}대" if "num_taxis" in meta else "모델 세트"
                created = meta.get("created_at", "")[:10]
                label_parts = [name, f"({taxi_str}"]
                if created:
                    label_parts.append(f"| {created}")
                label_parts.append(")")
                label = " ".join(label_parts).replace("( ", "(").replace(" )", ")")

                results.append((
                    label,
                    p,
                    cat_path if os.path.exists(cat_path) else None,
                    rl_path if os.path.exists(rl_path) else None,
                    meta
                ))
        return results

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
        c = self.current()
        is_real = (c.get("demand_source", "real") == "real")
        real_log_glob = os.path.join(ROOT, "data", "real_demand_logs", "demand_log_*.csv")
        import glob
        if is_real:
            if not glob.glob(real_log_glob):
                return None, "no_data"
            v2_args = [sys.executable, os.path.join(ROOT, "scripts", "train_dispatch_model.py"), real_log_glob]
        else:
            calls = os.path.join("data", "generated", "calls.csv")
            ext = os.path.join("data", "generated", "external.csv")
            if not (os.path.exists(os.path.join(ROOT, calls)) and os.path.exists(os.path.join(ROOT, ext))):
                return None, "no_data"
            v2_args = [sys.executable, os.path.join(ROOT, "scripts", "train_dispatch_model.py"), calls, "--external", ext]
        try:
            if os.path.exists(v2):  # 강제 재생성: 새 라인이 덮어쓰기 전에 기존 파일 보존
                bak = os.path.join(ROOT, "demand_v2_backups", f"demand_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.joblib")
                os.makedirs(os.path.dirname(bak), exist_ok=True)
                shutil.copy2(v2, bak)
                print(f"[새 학습 라인] 강제 재생성: 기존 demand_v2.joblib을 {os.path.relpath(bak, ROOT)} 에 백업했습니다.")
            os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
            with open(os.path.join(ROOT, "logs", "train_v2.log"), "w", encoding="utf-8") as logf:
                proc = subprocess.Popen(
                    v2_args,
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
        rmode = "default"
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as _cf:
                rmode = json.load(_cf).get("rl_reward_mode", "default")
        except Exception:
            pass
        dst = os.path.join(dst_dir, f"rl_model_{rmode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{steps}steps.zip")
        shutil.copy2(model, dst)
        return f"실제 종료 step {steps:,} >= 10,000, rl_model_backups/{os.path.basename(dst)} 에 보존"

    def _category_model_passes_baselines(self, model_path):
        """PPO에 넘길 카테고리 모델이 최소 베이스라인보다 나은지 확인한다."""
        try:
            import joblib
            bundle = joblib.load(model_path)
            metrics = bundle.get("metrics", {})
            categories = bundle.get("category_list", [])
        except Exception as exc:
            return False, f"카테고리 모델 평가 결과를 읽지 못했습니다: {exc}"
        if not metrics or not categories:
            return False, "카테고리 모델에 베이스라인 평가 결과가 없습니다."
        failed = []
        for category in categories:
            result = metrics.get(category, {})
            try:
                xgb_mae = result["xgb"]["mae"]
                baseline_mae = min(result["lag_1"]["mae"], result["slot_mean"]["mae"])
            except (KeyError, TypeError):
                failed.append(f"{category}(평가 누락)")
                continue
            if xgb_mae >= baseline_mae:
                failed.append(f"{category}(XGB MAE {xgb_mae:.4f} >= 기준 {baseline_mae:.4f})")
        if failed:
            return False, "XGBoost가 베이스라인을 이기지 못한 구역: " + ", ".join(failed)
        return True, "모든 카테고리에서 XGBoost가 MAE 기준 베이스라인을 통과했습니다."

    def run_training(self, ppo_category_only=False, reward_mode=None):
        c = self.current()
        if reward_mode is not None:
            c["rl_reward_mode"] = reward_mode

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)

        if ppo_category_only:
            v2_proc, v2_state = None, "ppo_only"
            print("[PPO 전용] H3/CNN-LSTM 및 demand_v2 학습은 건너뜁니다.")
        else:
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

            self.status.config(text="기존 모델 백업 중...")
            self.root.update()
            self._backup_saved_models(int(c.get("rl_total_timesteps", 20000)), skip_v2=(v2_proc is not None and v2_proc.poll() is None))

            if not ppo_category_only:
                self.status.config(text="1/4 train.py 실행 중...")
                self.root.update()
                self._run("train.py", blocking=True)
            else:
                print("[PPO 전용] train.py(H3 XGBoost/CNN-LSTM)를 실행하지 않습니다.")

            # 기존 category_demand_models.pkl은 보존하고, 이번 학습 결과는 별도 파일로 저장한다.
            category_new_path = os.path.join(ROOT, "saved_models", "category_demand_models_new.pkl")
            self.status.config(text="2/4 category demand 새 모델 학습 중...")
            self.root.update()
            cat_args = ["--out", category_new_path]
            if c.get("demand_source", "real") == "real":
                cat_args.extend(["--logs", os.path.join(ROOT, "data", "real_demand_logs", "demand_log_*.csv")])
            if not self._run(
                os.path.join("module3_prediction", "train_category_demand.py"),
                args=tuple(cat_args),
                blocking=True,
            ):
                self.status.config(text="category demand 학습 실패 - 학습 중단")
                return

            passed, baseline_msg = self._category_model_passes_baselines(category_new_path)
            print(f"[카테고리 베이스라인 검증] {baseline_msg}")
            if not passed:
                print(f"[카테고리 베이스라인 검증] 일부 구역에서 베이스라인 차이가 있으나, 구역별 상대 수요 순위 모델은 정상 확보되어 PPO 학습을 계속 진행합니다.")
                self.status.config(text="카테고리 베이스라인 경고 (PPO 학습 계속 진행)")

            # 5단계를 RL 학습보다 앞으로 옮김: demand_v2 변환이 먼저 끝나야
            # RL이 그 결과(category_demand_from_v2.pkl)를 학습 중에 참고할 수 있다.
            # (예전엔 RL 학습 뒤에 변환이 끝나서, 그 학습 사이클의 RL은 변환 결과를
            #  전혀 못 쓰고 있었음 — 구조적으로 순서가 늦어 못 쓰는 상태였음)
            if ppo_category_only:
                v2_msg = "PPO 전용 모드: demand_v2 학습·변환 생략"
            else:
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

    def open_unity_replay_dialog(self):
        """유니티 재생 데이터(Replay JSON) 생성을 위한 설정 다이얼로그."""
        win = tk.Toplevel(self.root)
        win.title("🎬 유니티 재생 생성기 설정")
        win.geometry("520x680")
        win.resizable(True, True)

        # ── 하단 고정 액션 바 (버튼이 절대 화면 밖으로 밀려나지 않도록 먼저 bottom에 pack) ──
        action_bar = ttk.Frame(win, padding=12)
        action_bar.pack(side="bottom", fill="x")

        ttk.Label(action_bar, text="※ 결과는 backup/ 폴더에 안전 보관되며 최신본으로 자동 갱신됩니다.",
                  foreground="#6b7280").pack(anchor="w", pady=(0, 6))

        # ── 상단/본문 영역 ──
        body = ttk.Frame(win, padding=(12, 10))
        body.pack(side="top", fill="both", expand=True)

        ttk.Label(
            body,
            text="유니티에서 3D 시각화할 시뮬레이션 데이터를 생성합니다.\n"
                 "선택한 전략들은 동일한 승객 수요와 날씨 조건으로 공정하게 비교 생성됩니다.",
            wraplength=480, justify="left",
        ).pack(anchor="w", pady=(0, 8))

        # 1. 전략 선택 프레임
        strat_box = ttk.LabelFrame(body, text="1. 생성할 택시 전략 선택 (체크)", padding=8)
        strat_box.pack(fill="x", pady=4)

        strat_defs = [
            ("patrol", "일반 순찰 (patrol) — 빈 차 상태에서 구역 순찰", True),
            ("prepositioned", "정적 선제배치 (prepositioned) — 핫스팟 구역 고정 대기", True),
            ("rl_prepositioned", "AI 강화학습 선제배치 (rl_prepositioned) — PPO 동적 재배치", True),
        ]
        strat_vars = {}
        for key, desc, default_val in strat_defs:
            var = tk.BooleanVar(value=default_val)
            strat_vars[key] = var
            ttk.Checkbutton(strat_box, text=desc, variable=var).pack(anchor="w", pady=2)

        # 2. 배차 알고리즘 선택 프레임
        algo_box = ttk.LabelFrame(body, text="2. 배차 알고리즘 선택", padding=8)
        algo_box.pack(fill="x", pady=4)

        algos = ["hungarian", "greedy", "routeExtension", "custom_greedy", "custom_routeExtension"]
        algo_var = tk.StringVar(value="hungarian")
        algo_combo = ttk.Combobox(algo_box, textvariable=algo_var, values=algos, state="readonly", width=25)
        algo_combo.pack(anchor="w")
        ttk.Label(algo_box, text="💡 hungarian(헝가리안) 알고리즘이 전체 대기시간을 최소화하는 추천 방식입니다.",
                  foreground="#6b7280").pack(anchor="w", pady=(3, 0))

        # 3. 기록 간격 (Stride) 선택 프레임
        stride_box = ttk.LabelFrame(body, text="3. 기록 간격 (Stride)", padding=8)
        stride_box.pack(fill="x", pady=4)

        stride_options = [
            ("10초 간격 (권장, 파일 약 15MB 내외)", "10"),
            ("5초 간격 (중간 정밀도)", "5"),
            ("1초 간격 (초당 전체 기록, 고용량)", "1"),
        ]
        stride_var = tk.StringVar(value="10")
        for label, val in stride_options:
            ttk.Radiobutton(stride_box, text=label, value=val, variable=stride_var).pack(anchor="w", pady=1)

        # 4. 유니티 StreamingAssets 자동 복사 연동
        unity_default_path = r"J:\chromdowload\zip\Fetex-Unity-main\Fetex-Unity-main\Assets\StreamingAssets"
        unity_box = ttk.LabelFrame(body, text="4. 유니티(Unity) 연동", padding=8)
        unity_box.pack(fill="x", pady=4)

        has_unity = os.path.exists(unity_default_path)
        copy_var = tk.BooleanVar(value=has_unity)
        ttk.Checkbutton(
            unity_box,
            text="완료 후 유니티 StreamingAssets 폴더로 자동 복사",
            variable=copy_var
        ).pack(anchor="w")
        status_txt = f"경로: {unity_default_path}" if has_unity else "※ 기본 유니티 경로를 찾을 수 없습니다."
        ttk.Label(unity_box, text=status_txt, foreground="#059669" if has_unity else "#b45309",
                  wraplength=460).pack(anchor="w", pady=(2, 0))

        def run_export():
            selected_strats = [k for k, v in strat_vars.items() if v.get()]
            if not selected_strats:
                messagebox.showwarning("경고", "생성할 전략을 하나 이상 선택하세요.")
                return

            self.save_config_only()
            cmd_args = ["--strategies"] + selected_strats + ["--algorithm", algo_var.get(), "--stride", stride_var.get()]
            if copy_var.get() and has_unity:
                cmd_args += ["--unity-dir", unity_default_path]

            win.destroy()
            self._run("export_unity_replay.py", cmd_args)

        run_btn = tk.Button(action_bar, text="🚀 유니티 재생 데이터 생성 시작", command=run_export,
                            bg="#2563eb", fg="white", font=("Malgun Gothic", 11, "bold"),
                            relief="raised", cursor="hand2", pady=8)
        run_btn.pack(fill="x")

    def run_unity_export(self):
        """(하위 호환) 유니티 재생 생성기 다이얼로그 호출"""
        self.open_unity_replay_dialog()

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
        win.title("A/B 다중비교 설정")
        win.geometry("460x730")
        win.resizable(False, False)

        ttk.Label(
            win,
            text="비교할 인자를 체크하세요. 선택된 인자의 모든 조합(직교 곱)을\n"
                 "2개 이상으로 자동 병렬 실행합니다.",
            padding=10, wraplength=420, justify="left",
        ).pack()

        value_vars = {}

        for key, (label, values) in FACTOR_OPTIONS.items():
            box = ttk.LabelFrame(win, text=label, padding=8)
            box.pack(fill="x", padx=15, pady=4)
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
            _today_txt = "오늘 날짜의 평일/공휴일 판정 적용"
        ttk.Label(win, text=f"* 공휴일 판정 미체크 시 기본으로 {_today_txt}로 실행됩니다.",
                  foreground="#6b7280", padding=(15, 0)).pack(anchor="w")

        # ── [선호 모델 프리셋 (RL 및 수요예측 스냅샷)] ──
        snapshots = self._list_model_snapshots()
        snap_box = ttk.LabelFrame(win, text="⭐ 선호 모델 프리셋 (스냅샷 세트)", padding=8)
        snap_box.pack(fill="x", padx=15, pady=6)

        snap_var = tk.StringVar(value=snapshots[0][0])
        snap_combo = ttk.Combobox(snap_box, textvariable=snap_var, values=[s[0] for s in snapshots],
                                  state="readonly", width=46)
        snap_combo.pack(fill="x")

        snap_info_lbl = ttk.Label(snap_box, text="현재 활성 RL 모델 및 수요예측 모델 세트를 사용합니다.",
                                  foreground="#2563eb", font=("맑은 고딕", 9))
        snap_info_lbl.pack(anchor="w", pady=(4, 0))

        def _on_snap_change(*args):
            chosen = snap_var.get()
            meta = {}
            for s in snapshots:
                if s[0] == chosen:
                    meta = s[4]
                    break
            if "num_taxis" in meta:
                t = meta["num_taxis"]
                dt = meta.get("created_at", "")
                snap_info_lbl.config(text=f"📌 저장 정보: 택시 {t}대 환경 | 저장일시: {dt}")
            else:
                snap_info_lbl.config(text="현재 활성 RL 모델 및 수요예측 모델 세트를 사용합니다.")

        snap_var.trace_add("write", _on_snap_change)

        # 기존 개별 수요예측 모델 선택기 (선호 프리셋이 '기본'일 때 보조 사용)
        models = self._list_category_models()
        model_box = ttk.LabelFrame(win, text="수요예측 모델 (기본 모드 시 세부 선택)", padding=8)
        model_box.pack(fill="x", padx=15, pady=4)
        model_var = tk.StringVar(value=models[0][0])
        ttk.Combobox(model_box, textvariable=model_var, values=[m[0] for m in models],
                     state="readonly", width=46).pack(fill="x")
        ttk.Label(model_box, text="* 프리셋이 '기본'일 때만 적용되며, 스냅샷 선택 시 스냅샷 내 모델이 우선합니다.",
                  foreground="#6b7280", wraplength=400, justify="left").pack(anchor="w", pady=(2, 0))

        def run_compare():
            selected = {}
            for key, (label, values) in FACTOR_OPTIONS.items():
                chosen = [v for v, var in value_vars[key].items() if var.get()]
                if chosen:
                    selected[key] = chosen

            if not selected:
                messagebox.showwarning("경고", "비교할 인자를 하나 이상 체크하세요.")
                return

            total_combos = 1
            for chosen in selected.values():
                total_combos *= len(chosen)
            if total_combos < 2:
                messagebox.showwarning(
                    "경고",
                    f"현재 선택된 조합은 {total_combos}개뿐이라 비교할 수 없습니다. "
                    f"하나 이상(예: 전략 2개 또는 알고리즘 2개 등)을 2개 이상 선택해 다중 조합을 만드세요.",
                )
                return

            import itertools
            keys = list(selected.keys())
            combos = [dict(zip(keys, combo)) for combo in itertools.product(*[selected[k] for k in keys])]

            if len(combos) > 4:
                if not messagebox.askyesno(
                        "조합 수 확인",
                        f"선택하신 조합이 총 {len(combos)}개입니다.\n"
                        f"PC 리소스에 따라 시간이 많이 걸릴 수 있습니다.\n\n"
                        f"계속하시겠습니까?"):
                    return

            if "holiday_mode" not in selected:
                for _c in combos:
                    _c["holiday_mode"] = "today"

            win.destroy()
            self.save_config_only()

            # 선택된 스냅샷 처리
            chosen_snap = None
            for s in snapshots:
                if s[0] == snap_var.get():
                    chosen_snap = s
                    break

            extra_env = {}
            if chosen_snap and chosen_snap[1] != os.path.join(ROOT, "saved_models"):
                # 특정 스냅샷(예: 1103, 1108_taxi70 등) 선택 시
                if chosen_snap[2] and os.path.exists(chosen_snap[2]):
                    extra_env["AI_CATEGORY_MODEL_PATH"] = chosen_snap[2]
                if chosen_snap[3] and os.path.exists(chosen_snap[3]):
                    extra_env["AI_RL_MODEL_PATH"] = chosen_snap[3]
            else:
                # 기본 모드: 기존 콤보박스 선택값 사용
                model_path = dict(models).get(model_var.get())
                if model_path:
                    extra_env["AI_CATEGORY_MODEL_PATH"] = model_path

            config_snapshot = self.current()
            self._run("multi_factor_compare.py", [json.dumps(combos, ensure_ascii=False),
                                                    json.dumps(config_snapshot, ensure_ascii=False)],
                      env_extra=extra_env)

        ttk.Button(win, text="🚀 A/B 다중비교 실행", command=run_compare).pack(pady=12, fill="x", padx=15)

    def _build_log_picker(self, parent, persist=True, allow_copy=False):
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

        sort_bar = ttk.Frame(parent)
        sort_bar.pack(fill="x", pady=(0, 4))
        ttk.Label(sort_bar, text="정렬 기준:").pack(side="left")
        sort_var = tk.StringVar(value="파일명 날짜순")
        sort_box = ttk.Combobox(sort_bar, textvariable=sort_var, state="readonly", width=16,
                                values=("파일명 날짜순", "생성일자순"))
        sort_box.pack(side="left", padx=(6, 0))

        # ★ 버튼 줄을 목록(tree)보다 먼저 pack -> 항상 보임 (날짜 자동 체크 버튼 포함)
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 4))

        tree = ttk.Treeview(parent, columns=("chk", "date", "created", "kind", "label", "rows"), show="headings",
                            selectmode="browse", height=6)
        for c, t, w in (("chk", "사용", 45), ("date", "파일명 날짜", 90), ("created", "생성일시", 130),
                        ("kind", "구분", 55), ("label", "구역·전략_알고리즘", 250), ("rows", "행 수", 70)):
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor="w" if c == "label" else "center")
        tree.pack(fill="both", expand=True)
        info = ttk.Label(parent, text="", foreground="#b45309", wraplength=620, justify="left")
        info.pack(anchor="w", pady=4)

        def row_vals(fn):
            r = by_name[fn]
            kind = "-" if r["is_holiday"] is None else ("공휴일" if r["is_holiday"] else "평일")
            created = datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d %H:%M") if r["created_at"] else "-"
            return ("☑" if fn in checked else "☐", r["date"] or "-", created, kind, r["label"], f"{r['rows']:,}")

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

        def refresh_rows(*_):
            tree.delete(*tree.get_children())
            if sort_var.get() == "생성일자순":
                ordered = sorted(logs, key=lambda r: (r["created_at"], r["filename"]))
            else:
                ordered = sorted(logs, key=lambda r: (r["date"] is None, r["date"], r["filename"]))
            for r in ordered:
                tree.insert("", "end", iid=r["filename"], values=row_vals(r["filename"]))

        refresh_rows()
        sort_box.bind("<<ComboboxSelected>>", refresh_rows)
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

        def auto_select_unique_dates():
            """같은 날짜의 로그가 여러 개면 날짜당 1개만 자동 선택한다.
            원본 CSV는 삭제/수정하지 않고 현재 학습 선택 목록만 변경한다.
            """
            by_date = {}
            for r in logs:
                if r["date"] is None:
                    continue
                by_date.setdefault(r["date"], []).append(r)

            if not by_date:
                messagebox.showwarning("날짜 자동 선택", "파일명에서 날짜를 확인할 수 있는 로그가 없습니다.")
                return

            checked.clear()
            for d in sorted(by_date):
                candidates = sorted(by_date[d], key=lambda r: r["filename"])
                checked.add(candidates[0]["filename"])

            for fn in by_name:
                tree.item(fn, values=row_vals(fn))
            refresh_info()
            messagebox.showinfo(
                "날짜 자동 선택 완료",
                f"서로 다른 날짜 {len(checked)}일을 선택했습니다.\n\n"
                "같은 날짜에 여러 로그가 있던 경우 날짜당 1개만 남겼습니다.\n"
                "원본 로그 파일은 삭제하거나 변경하지 않았습니다."
            )

        ttk.Button(bar, text="전체 선택", command=lambda: set_all(True)).pack(side="left", padx=2)
        ttk.Button(bar, text="전체 해제", command=lambda: set_all(False)).pack(side="left", padx=2)
        ttk.Button(bar, text="📅 날짜 중복 없이 자동 체크", command=auto_select_unique_dates).pack(side="left", padx=(12, 2))

        if allow_copy:
            def copy_selected_logs():
                if not checked:
                    messagebox.showwarning("학습 로그 복사", "복사할 로그를 하나 이상 선택하세요.")
                    return
                destination = filedialog.askdirectory(
                    parent=self.root,
                    title="선택한 학습 로그를 복사할 폴더 선택",
                    initialdir=ls.LOG_DIR,
                )
                if not destination:
                    return
                if os.path.normcase(os.path.abspath(destination)) == os.path.normcase(os.path.abspath(ls.LOG_DIR)):
                    messagebox.showwarning("학습 로그 복사", "원본 로그 폴더와 다른 폴더를 선택하세요.")
                    return

                copied = []
                for filename in sorted(checked):
                    source = os.path.join(ls.LOG_DIR, filename)
                    if not os.path.isfile(source):
                        continue
                    stem, ext = os.path.splitext(filename)
                    target = os.path.join(destination, filename)
                    suffix = 1
                    # 동명 파일을 덮어쓰지 않고 둘 다 보존한다.
                    while os.path.exists(target):
                        target = os.path.join(destination, f"{stem} ({suffix}){ext}")
                        suffix += 1
                    shutil.copy2(source, target)  # 내용과 생성/수정 시각을 함께 보존
                    copied.append(os.path.basename(target))

                messagebox.showinfo(
                    "학습 로그 복사 완료",
                    f"선택한 로그 {len(copied)}개를 복사했습니다.\n\n대상 폴더:\n{destination}",
                )

            ttk.Button(bar, text="선택 파일 복사...", command=copy_selected_logs).pack(side="left", padx=(12, 2))

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

    def open_data_quality_checker(self):
        """data/sim_logs의 demand_log CSV를 검사하고 결과 파일을 만든다. 원본은 수정하지 않는다."""
        script = os.path.join(ROOT, "check_demand_data.py")
        if not os.path.exists(script):
            messagebox.showerror("데이터 검사", "check_demand_data.py를 찾을 수 없습니다.")
            return
        try:
            result = subprocess.run(
                [sys.executable, script],
                cwd=ROOT, env=self._env(), capture_output=True, text=True, encoding="utf-8",
            )
            output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
            if result.returncode != 0:
                messagebox.showerror("데이터 검사 실패", output[-3000:] or "검사에 실패했습니다.")
                return
            messagebox.showinfo(
                "수요 로그 데이터 검사 완료",
                output[-3500:] + "\n\n원본 data/sim_logs CSV는 삭제하거나 수정하지 않았습니다.",
            )
        except Exception as e:
            messagebox.showerror("데이터 검사 오류", str(e))

    def open_analysis_dialog(self):
        """'분석하기' 팝업: 분석할 로그를 고르면 analyze_demand_logs.py가 평일/공휴일·전략별로 비교한 리포트를 만든다."""
        win = tk.Toplevel(self.root)
        win.title("로그 분석하기")
        win.geometry("700x600")
        win.minsize(700, 460)

        # 하단 버튼 바를 가장 먼저 bottom에 pack -> 항상 보임
        bar = ttk.Frame(win, padding=(12, 10, 12, 10))
        bar.pack(side="bottom", fill="x")
        ttk.Button(bar, text="취소", command=win.destroy).pack(side="right", padx=3)
        make_btn = ttk.Button(bar, text="리포트 만들기")
        make_btn.pack(side="right", padx=3)

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
            make_btn.config(state="disabled")
            return

        def start():
            names = pick()
            if not names:
                return
            win.destroy()
            paths = [os.path.join(ROOT, "data", "sim_logs", n) for n in names]
            self._run("analyze_demand_logs.py", ["--logs", *paths])

        make_btn.config(command=start)

    def open_training_dialog(self):
        """'학습만 실행' 팝업: 학습에 쓸 로그 + 학습 전 옵션을 여기서 정하고 시작."""
        win = tk.Toplevel(self.root)
        win.title("학습만 실행")
        win.geometry("700x850")
        win.minsize(700, 600)
        win.resizable(True, True)

        # 버튼 바를 가장 먼저 bottom에 pack -> 항상 보임
        bar = ttk.Frame(win, padding=(12, 10, 12, 10))
        bar.pack(side="bottom", fill="x")
        ttk.Button(bar, text="취소", command=win.destroy).pack(side="right", padx=3)
        start_btn = ttk.Button(bar, text="학습 시작")
        start_btn.pack(side="right", padx=3)

        ttk.Label(win, text="이 창에서 정한 대로 학습이 시작됩니다.\n"
                            "순서: 맵 재생성 → (시뮬레이션으로 로그 생성) → 예측모델 학습 → 강화학습",
                  padding=10, justify="left").pack(anchor="w")

        # ── RL 보상 프리셋 선택 프레임 ──
        reward_frame = ttk.LabelFrame(win, text="강화학습(RL) 보상 체계 선택 (rl_presets)", padding=8)
        reward_frame.pack(fill="x", padx=12, pady=4)

        cur_reward_mode = "gravityVer"
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as _cf:
                cur_reward_mode = json.load(_cf).get("rl_reward_mode", "gravityVer")
        except Exception:
            pass

        reward_mode_var = tk.StringVar(value=cur_reward_mode)
        ttk.Radiobutton(reward_frame,
                        text="🚀 gravityVer (균형 최적화 - 핫스팟 매칭 중심, 점수 스케일 -3~+3, 추천)",
                        value="gravityVer", variable=reward_mode_var).pack(anchor="w", pady=1)
        ttk.Radiobutton(reward_frame,
                        text="📦 default (기존 방식 - 탑승 위주 +3.0 / 타임아웃 폭탄 -8.0)",
                        value="default", variable=reward_mode_var).pack(anchor="w", pady=1)

        c_curr = self.current()
        is_real = (c_curr.get("demand_source", "real") == "real")

        opt = ttk.LabelFrame(win, text="학습 전 옵션", padding=8)
        opt.pack(fill="x", padx=12, pady=4)
        sim_var = self.bool_vars["run_sim_before_training"]
        v2_var = self.bool_vars["force_regenerate_demand_v2"]
        ppo_category_only_var = tk.BooleanVar(value=True)

        if is_real:
            sim_var.set(False)
            ttk.Label(opt, text="✅ 실데이터(real) 모드: 시뮬레이션 없이 TIMS 실측 데이터 기반으로 바로 학습합니다.",
                      foreground="#15803d", font=("맑은 고딕", 9, "bold")).pack(anchor="w", pady=(0, 2))
        else:
            import log_selection as _training_log_selection
            if _training_log_selection.load_selection() is not None:
                sim_var.set(False)
                ttk.Label(opt, text="선택된 로그만 학습하도록 '3회 추가 생성'을 자동 해제했습니다.",
                          foreground="#b45309").pack(anchor="w")
            ttk.Checkbutton(opt, text="시뮬레이션 3회 먼저 돌려 새 로그도 학습에 추가 (선택 로그만 쓰려면 해제)",
                            variable=sim_var).pack(anchor="w")

        ttk.Checkbutton(opt, text="demand_v2.joblib이 있어도 다시 생성 (기존 파일은 demand_v2_backups/에 백업)",
                        variable=v2_var).pack(anchor="w")
        ttk.Checkbutton(
            opt,
            text="PPO 카테고리 학습 전용 (H3/CNN-LSTM·demand_v2 생략, 베이스라인 통과 시에만 PPO 실행)",
            variable=ppo_category_only_var,
        ).pack(anchor="w", pady=(5, 0))

        if is_real:
            # ── [실데이터 모드] 인터랙티브 타임라인 기간 선택기 ──
            box = ttk.LabelFrame(win, text="📅 실데이터 학습 기간 선택 (2025 TIMS 연간 타임라인)", padding=10)
            box.pack(fill="both", expand=True, padx=12, pady=6)

            timeline_picker = TimelineRangePicker(box, year=2025)
            timeline_picker.pack(fill="both", expand=True)

            save_sel = None
        else:
            box = ttk.LabelFrame(win, text="학습에 쓸 로그  (새로 만든 로그는 선택 목록에 자동으로 추가됩니다)", padding=8)
            box.pack(fill="both", expand=True, padx=12, pady=6)
            save_sel = self._build_log_picker(box, allow_copy=True)

        def start():
            if not is_real and save_sel is not None and not save_sel():
                return
            chosen_rmode = reward_mode_var.get()
            
            if is_real:
                start_d, end_d, d_val = timeline_picker.get_selection()
                print(f"[실데이터 로그 준비] {start_d} ~ {end_d} ({d_val}일치) 호출 로그 생성 중...")
                self.status.config(text=f"실데이터 외생 로그 {d_val}일치 생성 중...")
                self.root.update()
                
                cmd = [sys.executable, os.path.join(ROOT, "scripts", "generate_demand_logs.py"),
                       "--days", str(d_val), "--end", end_d.isoformat()]
                subprocess.run(cmd, cwd=ROOT, check=True)
                print(f"[실데이터 로그 준비] {d_val}일치 생성 완료 -> data/real_demand_logs")

            win.destroy()
            self.run_training(
                ppo_category_only=ppo_category_only_var.get(),
                reward_mode=chosen_rmode,
            )

        start_btn.config(command=start)


if __name__ == "__main__":
    root = tk.Tk()
    ConfigGUI(root)
    root.mainloop()