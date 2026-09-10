"""0 수요를 명시적으로 다루는 예측 평가 및 제출용 결과 저장."""
from pathlib import Path
import json
import numpy as np
import pandas as pd


def calculate_metrics(y_true, y_pred):
    truth, pred = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    if truth.shape != pred.shape or not truth.size or not np.isfinite(truth).all() or not np.isfinite(pred).all():
        raise ValueError('평가 배열의 크기·유한값을 확인하세요.')
    error = np.abs(truth - pred)
    positive = truth > 0
    return {'RMSE': float(np.sqrt(np.mean(error**2))), 'MAE': float(error.mean()),
            'MAPE (%)': float(np.mean(error[positive] / truth[positive]) * 100) if positive.any() else None,
            'MAPE_valid_count': int(positive.sum()), 'zero_target_count': int((truth == 0).sum()),
            'WAPE (%)': float(error.sum() / np.abs(truth).sum() * 100) if np.abs(truth).sum() else None}


def save_evaluation(frame, targets, predictions, directory):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    rows, scores = [], {}
    for name, predicted in predictions.items():
        scores[name] = calculate_metrics(frame[targets].to_numpy(), predicted)
        for step, target in enumerate(targets):
            rows.append(pd.DataFrame({'model': name, 'origin_time': frame.time_bucket.to_numpy(),
                'target_time': frame.time_bucket.to_numpy() + np.timedelta64((step + 1) * 5, 'm'),
                'h3_index': frame.h3_index.to_numpy(), 'horizon': step + 1,
                'actual': frame[target].to_numpy(), 'predicted': predicted[:, step]}))
    results = pd.concat(rows, ignore_index=True)
    results['hour'] = pd.to_datetime(results.target_time).dt.hour
    results.to_csv(directory / 'predictions.csv', index=False)
    with (directory / 'metrics.json').open('w') as f: json.dump(scores, f, indent=2, allow_nan=False)
    for key in ('hour', 'h3_index', 'horizon'):
        records = [{'model': name, key: value, **calculate_metrics(g.actual, g.predicted)}
                   for (name, value), g in results.groupby(['model', key])]
        pd.DataFrame(records).to_csv(directory / f'metrics_by_{key}.csv', index=False)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    sample = results[(results.h3_index == results.h3_index.iloc[0]) & (results.horizon == 1)]
    fig, ax = plt.subplots(figsize=(12, 4))
    for name, group in sample.groupby('model'):
        group = group.head(288)
        ax.plot(group.target_time, group.predicted, label=name, alpha=.7)
    actual = sample.drop_duplicates('target_time').head(288)
    ax.plot(actual.target_time, actual.actual, label='actual', color='black', alpha=.6)
    ax.set(ylabel='Calls / 5 min', title='Held-out test: one cell, horizon 1'); ax.legend()
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(directory / 'forecast.png'); plt.close(fig)
    return scores
