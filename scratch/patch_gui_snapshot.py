import os

path = 'config_gui.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. snapshot_current_models 와 _list_model_snapshots 메서드 정의
methods_code = '''
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
            f"현재 RL 및 수요예측 모델을 보관할 폴더명을 입력하세요:\\n(saved_models/<폴더명> 에 저장됩니다)",
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
            f"현재 모델 스냅샷이 성공적으로 저장되었습니다!\\n\\n"
            f"📁 위치: saved_models/{folder_name}\\n"
            f"📄 포함 파일: {len(copied_files)}개 (RL zip, 수요예측 pkl 등)\\n\\n"
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
'''

# 2. 버튼 추가 (quality_bar에 스냅샷 저장 버튼 배치)
btn_target = 'ttk.Button(quality_bar, text="📊 실측 수요 데이터 품질 검사", command=self.open_data_quality_checker).pack(\n            side="left", fill="x", expand=True, padx=3\n        )'
btn_replacement = '''ttk.Button(quality_bar, text="📊 실측 수요 데이터 품질 검사", command=self.open_data_quality_checker).pack(
            side="left", fill="x", expand=True, padx=3
        )
        ttk.Button(quality_bar, text="📦 현재 모델 스냅샷 저장 (날짜+택시계수)", command=self.snapshot_current_models).pack(
            side="left", fill="x", expand=True, padx=3
        )'''

print("Checking targets...")
if "def snapshot_current_models" not in content:
    # _backup_saved_models 뒤에 메서드 삽입
    idx = content.find("def _backup_saved_models")
    assert idx != -1, "_backup_saved_models not found"
    content = content[:idx] + methods_code + "\n" + content[idx:]
    print("Inserted snapshot methods successfully!")

if "snapshot_current_models" not in content.split("quality_bar")[1]:
    # 버튼 추가
    assert btn_target in content, "btn_target not found"
    content = content.replace(btn_target, btn_replacement, 1)
    print("Inserted snapshot button successfully!")

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Finished updating config_gui.py stage 1!")
