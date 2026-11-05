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
from tools.region_autocomplete import RegionSearchEntry
from config_loader import (DEFAULT_CONFIG, write_run_config, read_user_config,
                           DEFAULT_PRESET_DIR, USER_PRESET_DIR)
from config_schema import (  # 설정 항목 정의(데이터)
    FREQ_OPTIONS, STRATEGIES, FACTOR_OPTIONS, FACTOR_VALUE_LABELS, AB_WARN_COMBOS, NUMERIC_GROUPS,
    BOOLS, CHOICES, WEATHER_NUMERICS, TEXTS, HOLIDAY_MULT_ITEMS, BASIC_KEYS, DETAIL_SECTIONS, TIPS)

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")


def _project_python():
    """하위 스크립트를 실행할 파이썬. 프로젝트 가상환경(.venv / venv)이 있으면 그것을 쓴다.
    GUI를 시스템 파이썬으로 켜도 sumolib·traci·xgboost 등이 설치된 환경에서 실행되게 하기 위함."""
    for name in (".venv", "venv"):
        exe = os.path.join(ROOT, name, "Scripts" if os.name == "nt" else "bin",
                           "python.exe" if os.name == "nt" else "python")
        if os.path.exists(exe):
            return exe
    return sys.executable


PYTHON = _project_python()


class ToolTip:
    """위젯에 마우스를 올리면 설명을 띄운다."""

    def __init__(self, widget, text):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.attributes("-topmost", True)  # GUI 창 뒤에 가려지지 않게
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, justify="left", wraplength=420, background="#fffbe6",
                 relief="solid", borderwidth=1, padx=6, pady=4, font=("맑은 고딕", 9)).pack()

    def _hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


def _scroll_area(parent):
    """세로 스크롤되는 영역. 마우스가 영역 안에 있을 때만 휠이 이 영역을 움직인다."""
    canvas = tk.Canvas(parent, highlightthickness=0)
    sb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas, padding=8)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    win = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
    canvas.configure(yscrollcommand=sb.set)
    canvas.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    wheel = lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
    canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
    return inner


class ConfigGUI:
    def __init__(self, root):
        self.root = root
        root.title("AI Mobility — CONFIG GUI")
        root.geometry("820x840")
        self.vars, self.bool_vars, self.choice_vars, self.text_vars = {}, {}, {}, {}
        self.holiday_mult_vars = {}
        self._loaded_config = {}
        self._numeric_meta = {k: (label, lo, hi, dec, isint)
                              for items in NUMERIC_GROUPS.values() for k, label, lo, hi, dec, isint in items}
        self._numeric_meta.update({k: (label, lo, hi, dec, isint) for k, label, lo, hi, dec, isint in WEATHER_NUMERICS})
        self._bool_meta = dict(BOOLS + [("weather_bonus_enabled", "날씨적용 (현재 날씨로 택시확률 보정)")])
        self._choice_meta = {k: (label, vals) for k, label, vals in CHOICES}
        self._choice_meta["freq"] = ("집계 시간 칸 (freq)", FREQ_OPTIONS)
        self._text_meta = dict(TEXTS)
        self._check_sections()
        self._build()
        self.load_config_file(show=False)

    def _check_sections(self):
        """모든 설정이 세부 설정의 한 섹션에 들어가 있는지 (빠지면 화면에서 바꿀 수 없음)."""
        listed = [k for _, keys in DETAIL_SECTIONS for k in keys]
        known = (set(self._numeric_meta) | set(self._bool_meta) | set(self._choice_meta) | set(self._text_meta)
                 | {"region", "passenger_seed", "holiday_multipliers"})
        missing, unknown = known - set(listed), set(listed) - known
        if missing or unknown:
            print(f"[경고] 세부 설정 섹션 확인 필요 — 빠짐: {sorted(missing)}, 알 수 없음: {sorted(unknown)}")

    # ---------------------------------------------------------------- 화면 구성
    def _build(self):
        ttk.Style().configure("Section.TButton", anchor="w", font=("맑은 고딕", 10, "bold"))
        ttk.Label(self.root, text="CONFIG GUI — 시뮬레이션·학습 설정", font=("맑은 고딕", 14, "bold")).pack(pady=(10, 0))
        ttk.Label(self.root, text="실행 버튼은 지금 화면 값으로 실행합니다(config.json은 그대로). "
                                  "[적용하기]를 눌러야 내 기본 설정(config.json)으로 저장됩니다.",
                  foreground="#64748b").pack(pady=(2, 8))

        bottom = ttk.Frame(self.root)
        bottom.pack(fill="x", side="bottom", padx=12, pady=(0, 8))
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8)
        basic = ttk.Frame(nb)
        detail = ttk.Frame(nb)
        nb.add(basic, text="기본")
        nb.add(detail, text="세부 설정")
        # 세부 설정에서 모든 변수를 만든 뒤, 기본 탭은 같은 변수를 공유하는 위젯만 만든다
        self._build_detail_tab(detail)
        self._build_basic_tab(basic)

        bf = ttk.Frame(bottom)
        bf.pack(fill="x", pady=8)
        buttons = [
            ("적용하기", self.save_config_only, "지금 화면 값을 내 기본 설정(config.json)으로 저장"),
            ("▶ 플레이", self.run_main, "지금 화면 값으로 지도 생성 후 SUMO GUI 시뮬레이션"),
            ("학습만 실행", self.open_training_dialog, "로그 생성·수요 예측·RL 학습"),
            ("🧪 A/B 비교하기", self.open_ab_compare_dialog, "전략·배차 조합을 같은 손님 목록으로 병렬 비교"),
            ("📊 분석하기", self.open_analysis_dialog, "학습 로그 분석 (평일/공휴일, 전략별)"),
            ("🎬 유니티 재생 생성기", self.open_unity_replay_dialog, "SUMO 결과를 Unity 재생 파일로 내보내기"),
        ]
        for text, cmd, tip in buttons:
            b = ttk.Button(bf, text=text, command=cmd)
            b.pack(side="left", fill="x", expand=True, padx=3)
            ToolTip(b, tip)
        self.status = ttk.Label(bottom, text="config.json을 불러오는 중...")
        self.status.pack(anchor="w", pady=(0, 4))

    def _build_basic_tab(self, parent):
        f = ttk.Frame(parent, padding=12)
        f.pack(fill="both", expand=True)
        self.preset_box = ttk.LabelFrame(f, text="프리셋 (누르면 화면에 적용, 저장은 [적용하기])", padding=8)
        self.preset_box.pack(fill="x", pady=(0, 10))
        self._refresh_preset_buttons()
        box = ttk.LabelFrame(f, text="기본 설정 (항목에 마우스를 올리면 설명)", padding=8)
        box.pack(fill="x")
        for key in BASIC_KEYS:
            self._row(box, key, create=False)
        ttk.Label(f, text="나머지 설정은 '세부 설정' 탭에 있습니다. 세부 설정에서 만든 개인 프리셋도 위 버튼에 나타납니다.",
                  foreground="#64748b").pack(anchor="w", pady=(10, 0))

    def _build_detail_tab(self, parent):
        inner = _scroll_area(parent)
        top = ttk.LabelFrame(inner, text="개인 프리셋 (presets/user/)", padding=8)
        top.pack(fill="x", pady=(0, 6))
        b = ttk.Button(top, text="현재 화면을 프리셋으로 저장", command=self.save_preset)
        b.pack(side="left", padx=3)
        ToolTip(b, "지금 화면 값을 이름을 붙여 저장. 기본 탭의 프리셋 버튼에 추가된다.")
        self.preset_combo = ttk.Combobox(top, state="readonly", width=24)
        self.preset_combo.pack(side="left", padx=6)
        ttk.Button(top, text="삭제", command=self.delete_preset).pack(side="left")
        ctl = ttk.Frame(inner)
        ctl.pack(fill="x", pady=(2, 4))
        ttk.Button(ctl, text="모두 펼치기", command=lambda: self._toggle_all(True)).pack(side="left", padx=3)
        ttk.Button(ctl, text="모두 접기", command=lambda: self._toggle_all(False)).pack(side="left", padx=3)
        self._sections = []
        for title, keys in DETAIL_SECTIONS:
            content = self._section(inner, title)
            for key in keys:
                self._row(content, key, create=True)
        self._toggle_weather_rows()

    def _section(self, parent, title):
        """접기/펼치기 섹션. 기본은 접힘."""
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=2)
        content = ttk.Frame(frame, padding=(14, 2, 4, 6))
        state = {"open": False}
        btn = ttk.Button(frame, text=f"▶  {title}", style="Section.TButton")

        def toggle(open_=None):
            state["open"] = (not state["open"]) if open_ is None else open_
            btn.config(text=f"{'▼' if state['open'] else '▶'}  {title}")
            if state["open"]:
                content.pack(fill="x")
            else:
                content.pack_forget()
        btn.config(command=toggle)
        btn.pack(fill="x")
        self._sections.append(toggle)
        return content

    def _toggle_all(self, open_):
        for toggle in self._sections:
            toggle(open_)

    def _row(self, parent, key, create):
        """설정 한 줄. create=True면 변수를 새로 만들고, False면 세부 설정에서 만든 변수를 공유한다."""
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        if key == "region":
            lbl = ttk.Label(row, text="지역", width=34)
            lbl.pack(side="left")
            if create:
                self.region = RegionSearchEntry(row, default="강남역", width=24)
                self.region.pack(side="right")
            else:
                # RegionSearchEntry는 한 위젯이라 기본 탭에는 같은 값을 쓰는 입력칸을 따로 둔다
                self.basic_region = RegionSearchEntry(row, default="강남역", width=24)
                self.basic_region.pack(side="right")
                self._link_region()
        elif key in self._numeric_meta:
            label, lo, hi, dec, isint = self._numeric_meta[key]
            if create:
                self.vars[key] = (tk.DoubleVar(value=DEFAULT_CONFIG.get(key, 0)), dec, isint)
            var = self.vars[key][0]
            lbl = ttk.Label(row, text=label, width=34)
            lbl.pack(side="left")
            ttk.Entry(row, textvariable=var, width=10).pack(side="right", padx=4)
            ttk.Scale(row, from_=lo, to=hi, variable=var, length=260).pack(side="right", fill="x", expand=True)
        elif key in self._bool_meta:
            if create:
                self.bool_vars[key] = tk.BooleanVar(value=bool(DEFAULT_CONFIG.get(key, False)))
            lbl = ttk.Checkbutton(row, text=self._bool_meta[key], variable=self.bool_vars[key])
            lbl.pack(anchor="w")
            if key == "weather_bonus_enabled" and create:
                self.weather_bonus_var = self.bool_vars[key]
                self.weather_bonus_var.trace_add("write", lambda *a: self._toggle_weather_rows())
        elif key in self._choice_meta:
            label, vals = self._choice_meta[key]
            if create:
                v = tk.StringVar()
                if key == "freq":
                    self.freq_var = v
                else:
                    self.choice_vars[key] = v
            var = self.freq_var if key == "freq" else self.choice_vars[key]
            lbl = ttk.Label(row, text=label, width=34)
            lbl.pack(side="left")
            ttk.Combobox(row, textvariable=var, values=vals, state="readonly", width=22).pack(side="right")
        elif key in self._text_meta:
            if create:
                self.text_vars[key] = tk.StringVar()
            lbl = ttk.Label(row, text=self._text_meta[key], width=34)
            lbl.pack(side="left")
            ttk.Entry(row, textvariable=self.text_vars[key], width=24).pack(side="right")
        elif key == "passenger_seed":
            if create:
                self.seed_var = tk.StringVar()
            lbl = ttk.Label(row, text="승객 시드 (비우면 랜덤)", width=34)
            lbl.pack(side="left")
            ttk.Entry(row, textvariable=self.seed_var, width=24).pack(side="right")
        elif key == "holiday_multipliers":
            lbl = ttk.Label(row, text="공휴일 배율 (공휴일·주말일 때 택시확률에 곱함)", font=("맑은 고딕", 9, "bold"))
            lbl.pack(anchor="w")
            defaults = DEFAULT_CONFIG.get("holiday_multipliers", {})
            for cat, cat_label in HOLIDAY_MULT_ITEMS:
                r = ttk.Frame(parent)
                r.pack(fill="x", pady=1)
                ttk.Label(r, text="   " + cat_label, width=34).pack(side="left")
                var = tk.DoubleVar(value=defaults.get(cat, 1.0))
                self.holiday_mult_vars[cat] = var
                ttk.Entry(r, textvariable=var, width=10).pack(side="right", padx=4)
                ttk.Scale(r, from_=0, to=3, variable=var, length=260).pack(side="right", fill="x", expand=True)
        else:
            return
        if create and key in {k for k, *_ in WEATHER_NUMERICS}:
            self._weather_rows = getattr(self, "_weather_rows", []) + [row]
        ToolTip(lbl, TIPS.get(key, ""))
        ToolTip(row, TIPS.get(key, ""))

    def _link_region(self):
        """기본 탭과 세부 설정의 지역 입력칸을 같은 값으로 맞춘다 (어느 쪽에서 바꿔도 반영)."""
        def sync(src, dst):
            def _f(*_):
                if dst.get() != src.get():
                    dst.set(src.get())
            return _f
        for a, b in ((self.region, self.basic_region), (self.basic_region, self.region)):
            var = getattr(a, "var", None) or getattr(a, "textvariable", None)
            if isinstance(var, tk.Variable):
                var.trace_add("write", sync(a, b))

    def _toggle_weather_rows(self):
        """날씨적용을 켰을 때만 날씨 보정 값 줄을 보여 준다."""
        if not hasattr(self, "weather_bonus_var"):
            return
        for row in getattr(self, "_weather_rows", []):
            if self.weather_bonus_var.get():
                row.pack(fill="x", pady=2)
            else:
                row.pack_forget()

    # ---------------------------------------------------------------- 프리셋
    def _preset_files(self):
        """[(이름, 경로, 팀 여부)] 팀 프리셋(presets/default) 먼저, 그다음 개인 프리셋(presets/user)."""
        out = []
        for folder, team in ((DEFAULT_PRESET_DIR, True), (USER_PRESET_DIR, False)):
            if os.path.isdir(folder):
                for f in sorted(os.listdir(folder)):
                    if f.endswith(".json"):
                        out.append((f[:-5], os.path.join(folder, f), team))
        # 팀 기본 프리셋은 맨 앞
        out.sort(key=lambda x: (not x[2], x[0] != "기본", x[0]))
        return out

    def _refresh_preset_buttons(self):
        for w in self.preset_box.winfo_children():
            w.destroy()
        files = self._preset_files()
        for i, (name, path, team) in enumerate(files):
            b = ttk.Button(self.preset_box, text=("" if team else "★ ") + name,
                           command=lambda p=path, n=name: self.apply_preset(p, n))
            b.grid(row=i // 3, column=i % 3, sticky="ew", padx=3, pady=2)
            ToolTip(b, ("팀 공유 프리셋" if team else "내 개인 프리셋") + f" — {os.path.relpath(path, ROOT)}")
        for c in range(3):
            self.preset_box.columnconfigure(c, weight=1)
        if hasattr(self, "preset_combo"):
            users = [n for n, _, team in files if not team]
            self.preset_combo["values"] = users
            self.preset_combo.set(users[0] if users else "")

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
        """내 설정(config.json)을 불러온다. 아직 없으면(처음 받은 상태) 팀 기본 프리셋으로 시작."""
        try:
            c = read_user_config(CONFIG_PATH)
        except Exception:
            c = {}
        merged = {**DEFAULT_CONFIG, **c}
        self._loaded_config = merged.copy()
        self.set_values(merged)
        if hasattr(self, "status"):
            self.status.config(text="내 설정(config.json)을 불러왔습니다." if os.path.exists(CONFIG_PATH)
                               else "config.json이 없어 팀 기본 프리셋으로 시작합니다. [적용하기]를 누르면 내 설정으로 저장됩니다.")

    def save_config_only(self):
        c = self.current()
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        self._loaded_config = c.copy()
        self.status.config(text="설정을 적용했습니다. (config.json 저장 완료)")

    def _prepare_run_config(self, label):
        """실행 버튼용: 화면 값을 results/run_configs/<label>_<시각>.json에 저장하고 이후 실행이 그 파일을 쓰게 한다.
        config.json은 [적용하기]를 눌렀을 때만 바뀐다. 반환: 화면 값 dict"""
        c = self.current()
        self._run_config_path = write_run_config(c, label)
        self.status.config(text=f"이번 실행 설정: {os.path.relpath(self._run_config_path, ROOT)} "
                                f"(config.json은 [적용하기]로만 바뀜)")
        return c

    def apply_preset(self, path, name):
        """프리셋을 화면에 적용. 일부 값만 있는 프리셋(시나리오)은 그 값만 바뀌고 나머지는 지금 화면 그대로."""
        with open(path, encoding="utf-8") as f:
            preset = json.load(f)
        c = self.current()
        c.update(preset)
        self._loaded_config = {**self._loaded_config, **preset}  # 화면에 없는 키도 저장·실행에 반영
        self.set_values(c)
        self.status.config(text=f"프리셋 '{name}' 적용 (저장하려면 [적용하기], 그대로 실행해도 됨)")

    def save_preset(self):
        """지금 화면 값을 개인 프리셋(presets/user/)으로 저장."""
        name = simpledialog.askstring("개인 프리셋 저장", "프리셋 이름:")
        if not name:
            return
        name = name.strip().replace("/", "_").replace("\\", "_")
        os.makedirs(USER_PRESET_DIR, exist_ok=True)
        path = os.path.join(USER_PRESET_DIR, name + ".json")
        if os.path.exists(path) and not messagebox.askyesno("확인", f"'{name}' 프리셋이 이미 있습니다. 덮어쓸까요?"):
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.current(), f, ensure_ascii=False, indent=2)
        self._refresh_preset_buttons()
        self.status.config(text=f"개인 프리셋 '{name}' 저장 (기본 탭 버튼에 추가됨)")

    def delete_preset(self):
        """개인 프리셋만 삭제 (팀 프리셋은 GUI에서 지우지 않음)."""
        n = self.preset_combo.get()
        if n and messagebox.askyesno("확인", f"개인 프리셋 '{n}'을 삭제할까요?"):
            os.remove(os.path.join(USER_PRESET_DIR, n + ".json"))
            self._refresh_preset_buttons()
            self.status.config(text=f"개인 프리셋 '{n}' 삭제")

    def _env(self):
        e = os.environ.copy()
        e["PYTHONIOENCODING"] = "utf-8"
        e["PYTHONUTF8"] = "1"
        e["PYTHONHASHSEED"] = "0"  # 실행 재현성 (reproducibility.py 참고)
        # 실행 버튼이 만든 실행용 설정을 자식 프로세스가 읽게 함 (config_loader의 MOBILITY_CONFIG)
        if getattr(self, "_run_config_path", None):
            e["MOBILITY_CONFIG"] = self._run_config_path
        return e

    def _run(self, script, args=(), blocking=False, env_extra=None):
        path = os.path.join(ROOT, script)
        try:
            env = self._env()
            if env_extra:
                env.update({str(k): str(v) for k, v in env_extra.items()})
            if blocking:
                return subprocess.run([PYTHON, path, *args], cwd=ROOT, env=env, check=True)
            if os.name == "nt":
                inner_cmd = subprocess.list2cmdline([PYTHON, path, *args])
                full_cmd = f'cmd /k call {inner_cmd}'
                subprocess.Popen(full_cmd, cwd=ROOT, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
                return True
            subprocess.Popen([PYTHON, path, *args], cwd=ROOT, env=env)
            return True
        except Exception as e:
            messagebox.showerror("실행 오류", str(e))
            return False

    def run_main(self):
        self._prepare_run_config("play")
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
                    [PYTHON, os.path.join(ROOT, "scripts", "train_dispatch_model.py"), calls, "--external", ext],
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
                [PYTHON, os.path.join(ROOT, "scripts", "convert_v2_to_category.py"), "--check-cells"],
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
        c = self._prepare_run_config("train")

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
            self._run(os.path.join("module3_prediction", "train.py"), blocking=True)

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

            self._prepare_run_config("unity")
            cmd_args = ["--strategies"] + selected_strats + ["--algorithm", algo_var.get(), "--stride", stride_var.get()]
            if copy_var.get() and has_unity:
                cmd_args += ["--unity-dir", unity_default_path]

            win.destroy()
            self._run(os.path.join("tools", "export_unity_replay.py"), cmd_args)

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
            from module1_simulation.holiday_utils import is_holiday_date
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
            self._prepare_run_config("ab")
            model_path = dict(models)[model_var.get()]
            self._run(os.path.join("scripts", "multi_factor_compare.py"), [json.dumps(combos, ensure_ascii=False)],
                      env_extra={"AI_CATEGORY_MODEL_PATH": model_path})

        ttk.Button(win, text="실행 (조합별로 새 창에서 병렬 측정)", command=run_compare).pack(pady=15, fill="x", padx=15)

    def _build_log_picker(self, parent, persist=True):
        """학습 팝업 안에 들어가는 '학습에 쓸 로그' 선택 목록. 반환값 save()를 호출하면 selected_logs.json에 저장.
        로그가 없으면 None."""
        import module2_preprocessing.log_selection as ls
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
                   command=lambda: (win.destroy(), self._run(os.path.join("tools", "analyzer_server.py")))).pack(side="right")
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
            self._run(os.path.join("tools", "analyze_demand_logs.py"), ["--logs", *paths])

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
        import module2_preprocessing.log_selection as _training_log_selection
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
