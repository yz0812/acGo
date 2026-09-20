"""Local smoke profile: python tests/benchmark_profile.py (no production data)."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

import psutil
import requests


def main():
    class Endpoint(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(0.15)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *args):
            pass

    endpoint = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
    http_thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    http_thread.start()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='acgo-profile-') as directory, tempfile.TemporaryFile() as output:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, DATA_DIR=directory, PORT=str(port), HOST='127.0.0.1', ADMIN_PASSWORD='profile-only')
        process = subprocess.Popen([sys.executable, 'run.py'], cwd=root, env=env, stdout=output, stderr=output)
        parent = psutil.Process(process.pid)
        parent.cpu_affinity([parent.cpu_affinity()[0]])
        sample_stop = threading.Event()
        peaks = {'rss_bytes': 0, 'processes': 0}

        def sample():
            while not sample_stop.wait(0.02):
                try:
                    processes = [parent] + parent.children(recursive=True)
                    rss = sum(p.memory_info().rss for p in processes)
                    peaks['rss_bytes'] = max(peaks['rss_bytes'], rss)
                    peaks['processes'] = max(peaks['processes'], len(processes))
                except psutil.Error:
                    pass

        monitor = threading.Thread(target=sample, daemon=True)
        monitor.start()
        client = requests.Session()
        try:
            url = f'http://127.0.0.1:{port}'
            for _ in range(100):
                if process.poll() is not None:
                    output.seek(0)
                    raise RuntimeError(output.read().decode())
                try:
                    if client.get(url + '/health', timeout=0.2).ok:
                        break
                except requests.RequestException:
                    pass
                time.sleep(0.05)
            else:
                raise RuntimeError('startup timeout')
            client.post(url + '/login', data={'password': 'profile-only'}, timeout=2).raise_for_status()
            ids = []
            for i in range(24):
                kind = 'curl' if i < 16 else 'python'
                data = dict(name=f'profile-{i}', task_type=kind, enabled=False, cron_expr='0 8 * * *', retry_count=0,
                            curl_command=f'curl http://127.0.0.1:{endpoint.server_port}/',
                            script_content="import time\nb=bytearray(16*1024*1024)\ntime.sleep(0.2)\nprint('ok')")
                response = client.post(url + '/api/accounts', json=data, timeout=3)
                response.raise_for_status()
                ids.append(response.json()['data']['id'])
            cookies = client.cookies.get_dict()

            def submit(account_id):
                with requests.Session() as request_client:
                    response = request_client.post(url + f'/api/checkin/{account_id}', cookies=cookies, timeout=5)
                    if response.status_code != 202:
                        raise RuntimeError(response.text)
                    return response.json()['data']['id']

            start = time.monotonic()
            with ThreadPoolExecutor(12) as pool:
                executions = list(pool.map(submit, ids))
            durations = []
            pending = set(executions)
            while pending and time.monotonic() - start < 30:
                request_start = time.monotonic()
                response = client.get(url + '/api/accounts', timeout=3)
                response.raise_for_status()
                durations.append((time.monotonic() - request_start) * 1000)
                for execution_id in list(pending):
                    item = client.get(url + f'/api/executions/{execution_id}', timeout=3).json()['data']
                    if item['state'] not in ('queued', 'running', 'retry_wait'):
                        if item['state'] != 'success':
                            raise AssertionError(item)
                        pending.remove(execution_id)
                time.sleep(0.05)
            if pending:
                raise RuntimeError(f'incomplete executions: {pending}')
            print(json.dumps({'tasks': len(executions), 'cpu_affinity_count': 1,
                              'elapsed_seconds': round(time.monotonic() - start, 3),
                              'peak_process_tree_rss_mib': round(peaks['rss_bytes'] / 1024**2, 1),
                              'peak_process_count': peaks['processes'], 'api_samples': len(durations),
                              'accounts_api_median_ms': round(statistics.median(durations), 2),
                              'accounts_api_max_ms': round(max(durations), 2)}, indent=2))
        finally:
            sample_stop.set()
            monitor.join()
            client.close()
            process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            endpoint.shutdown()
            endpoint.server_close()
            http_thread.join()


if __name__ == '__main__':
    main()
