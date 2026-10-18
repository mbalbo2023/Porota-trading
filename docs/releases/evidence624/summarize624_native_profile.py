"""Summarize measured counters without claiming matched-input native A/B."""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path


def delta(first, last):
    return {key: last[key] - first[key] for key in first.keys() & last.keys()
            if type(first[key]) is int and type(last[key]) is int}


def summary(path):
    raw = path.read_bytes()
    data = json.loads(raw)
    samples = data['samples']
    if data['status'] != 'OBSERVATION_COMPLETE' or len(samples) != 18:
        raise ValueError('INCOMPLETE_PASSIVE_OBSERVATION')
    first, last = samples[0], samples[-1]
    elapsed = last['monotonic'] - first['monotonic']
    if not 0 < elapsed < 220:
        raise ValueError('INVALID_PASSIVE_WINDOW')
    vm = delta(first['host']['vmstat'], last['host']['vmstat'])
    cpu = delta(first['host']['cpu_ticks'], last['host']['cpu_ticks'])
    total = sum(cpu[k] for k in ('user', 'nice', 'system', 'idle', 'iowait',
                                'irq', 'softirq', 'steal'))
    processes = []
    starts = {(p['pid'], p['stat']['start_ticks']): p for p in first['processes']}
    for end in last['processes']:
        start = starts.get((end['pid'], end['stat']['start_ticks']))
        if start is None:
            continue
        sched = delta(start['schedstat'], end['schedstat'])
        processes.append({'pid': end['pid'], 'role': end['role'],
                          'nice': end['stat']['nice'],
                          'cpu_seconds': sched['cpu_nanoseconds'] / 1e9,
                          'runqueue_wait_seconds': sched['runqueue_wait_nanoseconds'] / 1e9,
                          'major_faults_delta': end['stat']['major_faults'] - start['stat']['major_faults'],
                          'rss_start_bytes': start['status'].get('VmRSS'),
                          'rss_end_bytes': end['status'].get('VmRSS'),
                          'pss_start_bytes': start.get('smaps_rollup', {}).get('Pss'),
                          'pss_end_bytes': end.get('smaps_rollup', {}).get('Pss'),
                          'swap_end_bytes': end['status'].get('VmSwap')})
    generations = {}
    source_ages = []
    failures = []
    for row in samples:
        if row.get('generation_read_race_or_failure'):
            failures.append({'at': row['at'], 'error_class': row['generation_error_class'],
                             'error_code': row.get('generation_error_code')})
        if 'generation' not in row:
            continue
        generation = row['generation']
        generations[generation['generation_id']] = {
            **generation, 'source_as_of': row['source_as_of'],
            'worker_status': row['worker_status'], 'timing': row.get('timing'),
            'publication': row.get('publication')}
        age = (datetime.fromisoformat(row['at'].replace('Z', '+00:00'))
               - datetime.fromisoformat(row['source_as_of'].replace('Z', '+00:00'))).total_seconds()
        source_ages.append(age)
    memories = [r['memory']['MemAvailable'] for r in samples]
    result = {'schema': 'rc6.native624.measured-profile-summary.v1',
              'source_receipt_sha256': hashlib.sha256(raw).hexdigest(),
              'source_sha': data['source_sha'], 'source_tree': data['source_tree'],
              'purpose': data['purpose'], 'samples': len(samples),
              'elapsed_first_to_last_seconds': elapsed,
              'memory_min_bytes': min(memories), 'memory_max_bytes': max(memories),
              'memory_below_128mib_samples': sum(v < 128*1024**2 for v in memories),
              'host_swap_pages_in_delta': vm.get('pswpin'),
              'host_swap_pages_out_delta': vm.get('pswpout'),
              'page_size_bytes': data['page_size_bytes'],
              'host_major_faults_delta': vm.get('pgmajfault'),
              'host_cpu_percent': {k: round(100*v/total, 4) for k, v in cpu.items()
                                   if k not in {'guest', 'guest_nice'}},
              'owned_process_deltas': processes,
              'unique_generations': list(generations.values()),
              'generation_read_observations': failures,
              'source_age_min_seconds': min(source_ages) if source_ages else None,
              'source_age_max_seconds': max(source_ages) if source_ages else None,
              'ppi_watch_accessed': False,
              'real_orders_sent_all_integer_zero': all(
                  type(v['safety']['real_orders_sent']) is int
                  and v['safety']['real_orders_sent'] == 0
                  and v['safety']['mode'] == 'PRODUCTION_PAPER' for v in samples),
              'limitations': ['Passive observation is not matched-input native A/B.',
                              'Host swap counters do not attribute all swap IO to an individual process.',
                              'Major page faults are not synonymous with swap-in.',
                              '18 samples and the observed unique ticks do not establish P99.',
                              'Historical RSS highwater and cgroup peak are cumulative counters.']}
    disks = [r['free_disk_bytes'] for r in samples if 'free_disk_bytes' in r]
    if disks:
        result['disk_samples'] = len(disks)
        result['disk_min_bytes'] = min(disks)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = summary(args.profile)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items()
                      if k not in {'unique_generations', 'owned_process_deltas'}}, sort_keys=True))


if __name__ == '__main__':
    main()
