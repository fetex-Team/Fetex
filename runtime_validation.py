"""런타임 스케줄 진단. SUMO 없이도 설정의 활성 구간을 검사한다."""

def source_fingerprint():
    """코드가 변경된 뒤 과거 실험을 재사용하지 않도록 버전을 기록한다."""
    import hashlib
    from pathlib import Path
    root = Path(__file__).resolve().parent
    names = ['config_loader.py', 'passenger_manager.py', 'passenger_spawn_manager.py',
             'taxi_manager.py', 'measure_wait_time.py', 'module1_simulation/build_env.py',
             'module4_dispatch/hungarian_dispatcher.py', 'module4_dispatch/forecast_dispatcher.py']
    return hashlib.sha256(b''.join((root / name).read_bytes() for name in names)).hexdigest()


def schedule_status(cfg, zones, spawn=None):
    start, end = cfg['sim_start_hour'], cfg['sim_end_hour']
    rows = []
    definitions = [
        ('residential_out', cfg.get('residential_out_hour', 7), None, 'static'),
        ('residential_evening_in', cfg.get('residential_evening_in_hour', 19), None, 'static'),
        ('residential_late_in', cfg.get('residential_late_in_hour', 22), None, 'static'),
        ('school_morning', cfg.get('school_start_hour', 7), cfg.get('school_end_hour', 8), 'dynamic'),
        ('company_morning', cfg.get('company_start_hour', 8), cfg.get('company_end_hour', 10), 'dynamic'),
        ('school_afternoon', cfg.get('school_afternoon_start_hour', 16), cfg.get('school_afternoon_end_hour', 17), 'absorbed'),
        ('evening', cfg.get('evening_start_hour', 18), cfg.get('late_evening_end_hour', 24), 'absorbed'),
        ('residential_gacha', start, end, 'dynamic'),
    ]
    for kind, a, b, mode in definitions:
        overlap = start <= a < end if b is None else max(start, a) < min(end, b)
        active = cfg.get('passenger_mode') == 'legacy' if mode == 'static' else cfg.get('dynamic_passengers', True)
        count = spawn.spawn_counts.get(kind, 0) if spawn else None
        expected_static = None
        if mode == 'static':
            probability = cfg.get({'residential_out': 'residential_out_probability',
                                   'residential_evening_in': 'residential_evening_in_probability',
                                   'residential_late_in': 'residential_late_in_probability'}[kind], .2 if kind.endswith('late_in') else .15)
            edges = zones.get('residential') or [e for values in zones.values() for e in values]
            population = max(1, len(edges)) * cfg.get('residential_base_pop_per_edge', 200) + cfg.get('num_passengers', 0)
            expected_static = max(1, int(population * probability * cfg.get('residential_schedule_scale', .1))) if active and overlap and edges else 0
            count = None  # 정적 생성 이론값을 실제 계측과 구분한다.
        reason = 'eligible'
        if not active:
            reason = 'disabled_mode'
        elif not overlap:
            reason = 'outside_interval'
        elif not any(zones.values()):
            reason = 'empty_zones'
        elif spawn and mode != 'static':
            if count:
                reason = 'generated'
            elif spawn.failed_counts.get(kind):
                reason = 'spawn_failed'
            elif mode == 'absorbed' and not spawn.absorbed.get('school' if kind == 'school_afternoon' else 'company'):
                reason = 'no_absorbed_population'
            elif not spawn.attempt_counts.get(kind):
                reason = 'zero_attempts'
            else:
                reason = 'probability_zero_result'
        rows.append({'kind': kind, 'enabled': active, 'overlaps': overlap, 'reason': reason,
                     'overlap_start_hour': max(start, a) if overlap else None,
                     'overlap_end_hour': min(end, b) if overlap and b is not None else None,
                     'spawned': count, 'expected_static': expected_static})
    return rows
