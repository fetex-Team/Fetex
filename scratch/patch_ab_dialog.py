import os

path = 'config_gui.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# open_ab_compare_dialog 전체를 교체/개선
old_block_start = '    def open_ab_compare_dialog(self):'
old_block_end = '    def _build_log_picker(self, parent, persist=True, allow_copy=False):'

idx1 = content.find(old_block_start)
idx2 = content.find(old_block_end)

assert idx1 != -1 and idx2 != -1, "Target blocks not found!"

new_dialog_code = '''    def open_ab_compare_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("A/B 다중비교 설정")
        win.geometry("460x730")
        win.resizable(False, False)

        ttk.Label(
            win,
            text="비교할 인자를 체크하세요. 선택된 인자의 모든 조합(직교 곱)을\\n"
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
                        f"선택하신 조합이 총 {len(combos)}개입니다.\\n"
                        f"PC 리소스에 따라 시간이 많이 걸릴 수 있습니다.\\n\\n"
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
'''

new_content = content[:idx1] + new_dialog_code + "\n" + content[idx2:]
with open(path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Successfully replaced open_ab_compare_dialog!")
