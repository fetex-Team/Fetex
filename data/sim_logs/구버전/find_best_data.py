import os
import sys
import glob
import re
import pandas as pd

def rank_all_simulation_logs(log_dir: str):
    csv_files = glob.glob(os.path.join(log_dir, "*.csv"))
    
    if not csv_files:
        print(f"❌ '{log_dir}' 경로에서 CSV 파일을 찾지 못했습니다.")
        return

    print(f"🔍 총 {len(csv_files)}개의 시뮬레이션 로그 파일 분석을 시작합니다...\n")
    
    results = []

    for file_path in csv_files:
        filename = os.path.basename(file_path)
        
        # 자기 자신 및 백업(.bak) 파일 제외
        if filename.endswith(".bak") or filename == "find_best_data.py":
            continue

        try:
            df = pd.read_csv(file_path)
            total_rows = len(df)
            
            if total_rows == 0:
                continue

            # 대기시간 컬럼 체크 (없으면 즉시 탈락)
            wt_cols = [c for c in df.columns if any(k in c.lower() for k in ['wait', 'delay', 'duration'])]
            if not wt_cols:
                continue
                
            wt_data = pd.to_numeric(df[wt_cols[0]], errors='coerce').dropna()
            if len(wt_data) == 0:
                continue

            mean_wt = wt_data.mean()
            std_wt = wt_data.std()

            # 상태(status) 컬럼 탐색
            st_cols = [c for c in df.columns if any(k in c.lower() for k in ['status', 'state', 'result', 'cancel'])]

            # 타임아웃/소멸 비율 계산
            timeout_ratio = 0.0
            if st_cols:
                st_series = df[st_cols[0]].astype(str).str.lower()
                timeouts = st_series.value_counts().get('timeout', 0) + st_series.value_counts().get('cancelled', 0)
                timeout_ratio = (timeouts / total_rows) * 100

            # 파일명에서 날짜 코드 추출 (예: 261008, 261007 등)
            date_match = re.search(r'(\d{6})', filename)
            date_tag = date_match.group(1) if date_match else "날짜미상"

            # ------------------------------------------------------------
            # 🎯 학습 적합도 점수(Fitness Score) 계산
            # ------------------------------------------------------------
            score = 100.0
            
            # 1. 타임아웃 감점 (5%~25% 사이가 가장 이상적)
            if timeout_ratio > 30.0:
                score -= (timeout_ratio - 30.0) * 2
            elif timeout_ratio < 3.0:
                score -= 20.0

            # 2. 표준편차(데이터 변동성) 평가
            if pd.isna(std_wt) or std_wt < 5.0:
                score -= 30.0

            results.append({
                'file_name': filename,
                'date_tag': date_tag,
                'total_passengers': total_rows,
                'mean_wait_sec': round(float(mean_wt), 1),
                'std_wait_sec': round(float(std_wt) if not pd.isna(std_wt) else 0.0, 1),
                'timeout_ratio': round(float(timeout_ratio), 1),
                'fitness_score': round(float(score), 1)
            })

        except Exception:
            continue

    if not results:
        print("❌ 유효한 대기시간(wait_time) 데이터를 가진 로그를 찾지 못했습니다.")
        return

    # 점수 기준 내림차순 정렬
    ranked_df = pd.DataFrame(results).sort_values(by='fitness_score', ascending=False)

    print("==========================================================================")
    print(f"🏆 [학습 적합도 랭킹 TOP 10 - 총 {len(ranked_df)}개 검증 통과]")
    print("==========================================================================")
    
    top_10 = ranked_df.head(10)
    for idx, row in top_10.reset_index(drop=True).iterrows():
        rank_icon = "🥇" if idx == 0 else ("🥈" if idx == 1 else ("🥉" if idx == 2 else "🔹"))
        print(f"{rank_icon} {idx+1:2d}위 [{row['date_tag']}]: {row['file_name']}")
        print(f"    └ 점수: {row['fitness_score']}점 | 승객: {row['total_passengers']:,}명 | 평균대기: {row['mean_wait_sec']}초 | 타임아웃: {row['timeout_ratio']}% | 표준편차: {row['std_wait_sec']}")
        print("--------------------------------------------------------------------------")

    print("\n💡 [날짜별 분리 가이드]")
    print("👉 서로 다른 날짜([YYMMDD])끼리 학습(Train)과 검증(Test) 데이터셋을 조합하세요!")

if __name__ == "__main__":
    LOG_DIR = sys.argv[1] if len(sys.argv) > 1 else "."
    rank_all_simulation_logs(LOG_DIR)