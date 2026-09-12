"""6구간 XGBoost 예측, 시간 경계 검증, 선택적 실제 CNN-LSTM 시퀀스 학습."""
import argparse
import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import ParameterGrid
from xgboost import XGBRegressor
from config_loader import CFG, ROOT
from data.generate import generate_data, save_data
from evaluate import calculate_metrics, save_evaluation
from module2_preprocessing.time_series_prep import TimeSeriesPreprocessor, make_supervised, chronological_split
from module2_preprocessing.external_data_merge import merge_external_data

MODEL_PATH = Path(ROOT) / 'saved_models/demand_v2.joblib'


def prepare_dataset(meta, days=None):
    end = pd.Timestamp(meta['config']['sim_date'])
    days = days or CFG['training_days']
    start = end - pd.Timedelta(days=days)
    calls, external = generate_data(meta, start, days, seed=CFG['train_random_state'])
    save_data(calls, external, Path(ROOT) / 'data/generated')
    prep = TimeSeriesPreprocessor()
    panel = prep.aggregate_demands(calls, cells=set(meta['edge_cells'].values()), start=start, end=end)
    return prep.create_features(merge_external_data(panel, external)), calls


def sequence_arrays(frame, features, targets, length):
    """지역별 연속된 과거 length개 행을 입력으로 사용한다."""
    xs, ys, rows = [], [], []
    if length < 2:
        raise ValueError('시퀀스 길이는 2 이상이어야 합니다.')
    for _, group in frame.groupby('h3_index', sort=True):
        group = group.sort_values('time_bucket')
        if len(group) < length:
            continue
        values = group[features].to_numpy(dtype=np.float32)
        windows = np.lib.stride_tricks.sliding_window_view(values, length, axis=0).transpose(0, 2, 1)
        stamps = group.time_bucket.to_numpy()
        valid = stamps[length - 1:] - stamps[:len(group) - length + 1] == np.timedelta64(5 * (length - 1), 'm')
        xs.append(windows[valid]); ys.append(group[targets].to_numpy(dtype=np.float32)[length - 1:][valid])
        rows.append(group.iloc[length - 1:].loc[valid])
    if not xs or not any(len(x) for x in xs):
        raise ValueError('CNN-LSTM 입력 시퀀스가 부족합니다.')
    return np.concatenate(xs), np.concatenate(ys), pd.concat(rows, ignore_index=True)



def train_cnn(parts, features, targets, output_dir):
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset
    from module3_prediction.models import CNNLSTMModel
    # XGBoost/PyTorch의 서로 다른 OpenMP 런타임이 충돌한 macOS 환경에서 단일 CPU 스레드를 사용한다.
    torch.manual_seed(CFG['train_random_state']); torch.set_num_threads(1)
    arrays = [sequence_arrays(p, features, targets, CFG['sequence_length']) for p in parts]
    mean = arrays[0][0].mean(axis=(0, 1)); scale = arrays[0][0].std(axis=(0, 1)); scale[scale < 1e-6] = 1
    xs = [torch.from_numpy((a[0] - mean) / scale) for a in arrays]
    model = CNNLSTMModel(len(features), output_dim=6)
    optimizer = torch.optim.Adam(model.parameters(), lr=CFG['cnn_lr'])
    loader = DataLoader(TensorDataset(xs[0], torch.from_numpy(arrays[0][1])), batch_size=CFG['cnn_batch_size'], shuffle=True)
    loss_fn = nn.MSELoss(); best = float('inf'); best_state = None
    for epoch in range(CFG['cnn_epochs']):
        model.train()
        for x, y in loader:
            optimizer.zero_grad(); loss_fn(model(x), y).backward(); optimizer.step()
        model.eval()
        with torch.no_grad(): loss = loss_fn(model(xs[1]), torch.from_numpy(arrays[1][1])).item()
        if loss < best:
            best = loss; best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        print(f'CNN epoch {epoch + 1}: validation MSE={loss:.4f}')
    model.load_state_dict(best_state)
    with torch.no_grad(): predictions = model(xs[2]).numpy().clip(0)
    torch.save({'state_dict': best_state, 'input_dim': len(features), 'output_dim': 6,
                'feature_cols': features, 'sequence_length': CFG['sequence_length'],
                'mean': mean.tolist(), 'scale': scale.tolist(),
                'hidden_dim': CFG['cnn_hidden_dim'], 'num_layers': CFG['cnn_num_layers'],
                'kernel_size': CFG['cnn_kernel_size']}, MODEL_PATH.with_name('cnn_lstm_v2.pt'))
    save_evaluation(arrays[2][2], targets, {'cnn_lstm': predictions}, output_dir / 'cnn')


def train(meta_path=None, days=None, cnn=False):
    path = Path(meta_path or Path(ROOT) / 'module1_simulation/sumo_config/runtime_meta.json')
    meta = json.loads(path.read_text())
    if 'config' in meta:
        CFG.clear(); CFG.update(meta['config'])
    if 'edge_cells' not in meta:
        raise ValueError('python module1_simulation/build_env.py로 환경을 먼저 다시 생성하세요.')
    panel, calls = prepare_dataset(meta, days)
    frame, features, targets = make_supervised(panel)
    parts = chronological_split(frame, CFG['validation_size'], CFG['test_size'])
    training, validation, test = parts
    params = {'max_depth': sorted({max(1, CFG['xgb_max_depth'] - 2), CFG['xgb_max_depth']}),
              'n_estimators': sorted({max(10, CFG['xgb_n_estimators'] // 2), CFG['xgb_n_estimators']})}
    best_score = float('inf'); best_model = None; trials = []
    for candidate in ParameterGrid(params):
        model = XGBRegressor(**candidate, learning_rate=CFG['xgb_learning_rate'], random_state=CFG['xgb_random_state'], n_jobs=2)
        model.fit(training[features], training[targets])
        score = calculate_metrics(validation[targets].to_numpy(), model.predict(validation[features]).clip(0))['RMSE']
        trials.append({**candidate, 'validation_RMSE': score})
        if score < best_score: best_model, best_score = model, score
    MODEL_PATH.parent.mkdir(exist_ok=True)
    # 원점 t는 완료된 5분 구간이며 모델 파일에 피처·격자·학습 범위를 함께 저장한다.
    artifact = {'version': 2, 'model': best_model, 'feature_cols': features, 'horizon': 6,
                'freq': '5min', 'cells': sorted(frame.h3_index.unique()), 'max_lag': CFG['max_lag'],
                'rolling_short': CFG['rolling_short'], 'rolling_long': CFG['rolling_long'],
                'trained_until': str(training.time_bucket.max() + pd.Timedelta(minutes=30)),
                'data_until': str(panel.time_bucket.max() + pd.Timedelta(minutes=5)),
                'source': 'synthetic', 'seed': CFG['train_random_state']}
    joblib.dump(artifact, MODEL_PATH)
    directory = Path(ROOT) / 'results/prediction'
    scores = save_evaluation(test, targets, {'xgboost': best_model.predict(test[features]).clip(0),
        'persistence': np.repeat(test[['observed_demand']].to_numpy(), 6, axis=1)}, directory)
    metadata = {'source': 'synthetic', 'config': meta['config'], 'calls': len(calls), 'cells': len(artifact['cells']), 'trials': trials,
                'splits': {name: {'rows': len(part), 'start': str(part.time_bucket.min()), 'end': str(part.time_bucket.max())}
                           for name, part in zip(('train', 'validation', 'test'), parts)}}
    (directory / 'experiment.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    # EDA는 실제로 생성한 호출의 시간/지역 분포를 저장한다.
    panel.groupby(['hour', 'h3_index']).demand.mean().unstack().to_csv(directory / 'eda_hour_cell.csv')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    heat = panel.groupby(['hour', 'h3_index']).demand.mean().unstack()
    fig, ax = plt.subplots(figsize=(8, 5)); im = ax.imshow(heat, aspect='auto'); fig.colorbar(im, label='Mean calls / 5 min')
    ax.set(xlabel='H3 cell index', ylabel='Hour', title='Synthetic demand by hour and cell'); fig.tight_layout()
    fig.savefig(directory / 'eda.png'); plt.close(fig)
    if cnn: train_cnn(parts, features, targets, directory)
    print(json.dumps(scores, ensure_ascii=False, indent=2)); print(f'모델 저장: {MODEL_PATH}')
    return artifact


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--meta'); parser.add_argument('--days', type=int)
    parser.add_argument('--cnn', action='store_true', help='XGBoost와 함께 CNN-LSTM도 학습')
    args = parser.parse_args(); train(args.meta, args.days, args.cnn)
