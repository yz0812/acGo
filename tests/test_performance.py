"""Resource and concurrency regression tests; all DB/network traffic is local."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import socket
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from src import models, scheduler, notifier, settings, http_client
from src.execution import ExecutionEngine, submit, engine
from src.script_runner import run_script


class QueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = models.db.database
        cls.folder = tempfile.TemporaryDirectory(prefix='acgo-queue-tests-')
        models.db.close()
        models.db.init(str(Path(cls.folder.name) / 'test.db'))
        models.init_db()
        cls.app = importlib.import_module('src.app').app
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        models.db.close()
        models.db.init(cls.original)
        cls.folder.cleanup()

    def setUp(self):
        models.Account.delete().execute()
        models.Notification.delete().execute()
        models.Config.delete().where(models.Config.key != 'admin_password').execute()
        models.db.close()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['logged_in'] = True

    def tearDown(self):
        models.db.close()

    def account(self, **values):
        data = dict(name='test', task_type='python', script_content="print('ok')", enabled=True, retry_count=0)
        data.update(values)
        with models.connection():
            return models.Account.create(**data)

    def test_concurrent_submissions_deduplicate_and_bound_capacity(self):
        account = self.account()
        with ThreadPoolExecutor(12) as pool:
            results = list(pool.map(lambda _: submit(account.id, manual=True), range(24)))
        self.assertEqual(len({r['execution']['id'] for r in results}), 1)
        self.assertEqual(sum(not r['duplicate'] for r in results), 1)
        with patch.object(settings, 'QUEUE_LIMIT', 2):
            second = self.account()
            self.assertEqual(submit(second.id)['status'], 'accepted')
            third = self.account()
            response = self.client.post(f'/api/checkin/{third.id}')
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers['Retry-After'], '5')
            self.assertEqual(submit(third.id)['status'], 'busy')
        self.assertEqual(models.Execution.select().count(), 2)
        self.assertIn('队列已满', models.CheckinLog.get().error_message)

    def test_random_and_retry_waits_release_workers(self):
        future = self.account()
        random_result = scheduler.execute_checkin_with_random_delay(future.id, 1800)
        models.Execution.update(due_at=datetime.now() + timedelta(minutes=20)).where(
            models.Execution.id == random_result['execution']['id']).execute()
        retry = self.account(retry_count=1, retry_interval=3600)
        other = self.account()
        execution = submit(retry.id)['execution']['id']
        submit(other.id)
        with patch('src.scheduler.execute_attempt', return_value={'status': 'failed', 'error': 'timeout'}) as attempt:
            self.assertTrue(engine.run_one('script'))
            self.assertEqual(models.Execution.get_by_id(execution).state, 'retry_wait')
            self.assertTrue(engine.run_one('script'))
            self.assertEqual(attempt.call_count, 2)
            self.assertFalse(engine.run_one('script'))
        self.assertEqual(models.Execution.get_by_id(random_result['execution']['id']).state, 'queued')

    def test_edit_invalidates_pending_work_and_bad_cron_is_not_saved(self):
        account = self.account()
        item = submit(account.id)['execution']['id']
        response = self.client.put(f'/api/accounts/{account.id}', json={'enabled': False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.Execution.get_by_id(item).state, 'cancelled')
        self.assertFalse(engine.run_one('script'))
        response = self.client.put(f'/api/accounts/{account.id}', json={'cron_expr': 'broken'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(models.Account.get_by_id(account.id).cron_expr, '0 8 * * *')

    def test_restart_recovers_waiting_but_never_replays_unknown_running(self):
        first, second = self.account(), self.account()
        running = submit(first.id)['execution']['id']
        queued = submit(second.id)['execution']['id']
        models.Execution.update(state='running').where(models.Execution.id == running).execute()
        ExecutionEngine().recover()
        self.assertEqual(models.Execution.get_by_id(running).state, 'interrupted')
        self.assertEqual(models.Execution.get_by_id(queued).state, 'queued')

    def test_worker_quotas_and_web_responsiveness_under_backlog(self):
        for kind in ('python', 'curl'):
            for _ in range(12):
                submit(self.account(task_type=kind, curl_command='curl http://localhost').id)
        release = threading.Event()
        full = threading.Event()
        lock = threading.Lock()
        active = {'python': 0, 'curl': 0}
        peaks = {'python': 0, 'curl': 0}

        def slow(account):
            with lock:
                active[account.task_type] += 1
                peaks[account.task_type] = max(peaks[account.task_type], active[account.task_type])
                if active == {'python': 1, 'curl': 2}:
                    full.set()
            release.wait(5)
            with lock:
                active[account.task_type] -= 1
            return {'status': 'success'}

        worker = ExecutionEngine()
        with patch('src.scheduler.execute_attempt', side_effect=slow):
            try:
                worker.start()
                self.assertTrue(full.wait(5))
                start = time.monotonic()
                self.assertEqual(self.client.get('/api/accounts').status_code, 200)
                self.assertLess(time.monotonic() - start, 1)
                self.assertEqual(peaks, {'python': 1, 'curl': 2})
            finally:
                release.set()
                worker.stop()

    def test_list_queries_are_bounded_and_details_remain_available(self):
        account = self.account(script_content='x' * 50000)
        log = models.CheckinLog.create(account=account, status='success', response_body='r' * 5000,
                                       request_data='p' * 50000, request_method='PYTHON')
        summary = self.client.get('/api/accounts').json
        self.assertNotIn('script_content', summary['data'][0])
        self.assertEqual(len(self.client.get(f'/api/accounts/{account.id}').json['data']['script_content']), 50000)
        logs = self.client.get('/api/logs').json['data']
        self.assertEqual(len(logs[0]['response_body']), 100)
        self.assertEqual(len(self.client.get(f'/api/logs/{log.id}/response').json['data']['response_body']), 5000)
        for route in ('/api/logs', '/api/accounts'):
            for query in ('page_size=100000', 'page=-1', 'page_size=0', 'page=oops'):
                self.assertEqual(self.client.get(route + '?' + query).status_code, 400)
        plan = models.db.execute_sql("EXPLAIN QUERY PLAN SELECT id FROM checkin_logs WHERE status='success' ORDER BY executed_at DESC,id DESC LIMIT 50").fetchall()
        self.assertIn('log_status_time', str(plan))
        self.assertNotIn('TEMP B-TREE', str(plan))
        self.assertEqual(models.db.execute_sql('PRAGMA journal_mode').fetchone()[0], 'wal')

    def test_notification_outbox_bounded_retry_and_channel_isolation(self):
        models.Config.insert_many([{'key': name + '_enabled', 'value': 'true'} for name in ('telegram', 'wecom')]).execute()
        with patch.object(settings, 'NOTIFY_QUEUE_LIMIT', 2):
            self.assertTrue(notifier.send_all_notifications('test', 'success'))
            self.assertFalse(notifier.send_all_notifications('test', 'success'))
        with patch('src.notifier._deliver', side_effect=[{'status_code': 503, 'text': 'busy'}, {'status_code': 200, 'text': 'ok'}]):
            self.assertTrue(notifier.outbox.run_one())
            self.assertTrue(notifier.outbox.run_one())
        self.assertEqual(models.Notification.get(models.Notification.channel == 'telegram').state, 'queued')
        self.assertEqual(models.Notification.get(models.Notification.channel == 'wecom').state, 'sent')
        with patch('src.notifier._deliver', return_value={'status_code': 200, 'text': 'ok'}) as deliver:
            notifier.outbox.run_one(datetime.now() + timedelta(hours=1))
            self.assertEqual(deliver.call_count, 1)
            self.assertEqual(deliver.call_args.args[0], 'telegram')

    def test_cleanup_batches_and_preserves_newest(self):
        account = self.account()
        models.Config.insert_many([{'key': 'auto_clean_logs', 'value': 'true'}, {'key': 'max_logs_count', 'value': '100'}]).execute()
        with models.db.atomic():
            models.CheckinLog.insert_many([{'account': account.id, 'status': 'success'} for _ in range(550)]).execute()
        for _ in range(3):
            scheduler.auto_clean_logs()
        self.assertEqual(models.CheckinLog.select().count(), 100)
        self.assertEqual(models.CheckinLog.select().order_by(models.CheckinLog.id).first().id, 451)

    def test_config_upserts_are_atomic_and_export_streams_full_details(self):
        self.account(script_content='x' * 50000)
        for enabled in (True, False):
            response = self.client.post('/api/notify/config', json={'telegram_enabled': enabled, 'telegram_user_id': '123'})
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(self.client.get('/api/notify/config').json['data']['telegram_enabled'], enabled)
        response = self.client.post('/api/webhook/config', json={'enabled': True, 'url': 'http://localhost/test'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(self.client.get('/api/webhook/config').json['data']['enabled'])
        self.client.post('/api/system/config', json={'auto_clean_logs': True, 'max_logs_count': 100})
        response = self.client.post('/api/system/config', json={'auto_clean_logs': False, 'max_logs_count': -1})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.client.get('/api/system/config').json['data']['auto_clean_logs'])
        exported = self.client.get('/api/accounts/export')
        self.assertEqual(len(exported.json['data'][0]['script_content']), 50000)

    def test_delete_and_recreate_does_not_receive_old_execution_result(self):
        old = self.account()
        execution = submit(old.id)['execution']['id']

        def delete_and_recreate(account):
            with models.connection():
                models.Account.delete_by_id(account.id)
                self.account(id=account.id, name='replacement')
            return {'status': 'success'}

        with patch('src.scheduler.execute_attempt', side_effect=delete_and_recreate), patch('src.notifier.send_all_notifications') as notify:
            engine.run_one('script')
            notify.assert_not_called()
        self.assertIsNone(models.Execution.get_or_none(models.Execution.id == execution))
        replacement = submit(old.id)['execution']['id']
        self.assertGreater(replacement, execution)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                try:
                    if self.path == '/slow-headers':
                        for byte in b'HTTP/1.1 200 OK\r\n':
                            self.connection.sendall(bytes([byte]))
                            time.sleep(0.1)
                        return
                    self.send_response(302 if self.path in ('/redirect', '/cookie-redirect') else 200)
                    if self.path == '/gzip':
                        self.send_header('Content-Encoding', 'gzip')
                    if self.path == '/redirect':
                        self.send_header('Location', '/ok')
                    if self.path == '/cookie-redirect':
                        self.send_header('Location', '/cookies')
                    self.end_headers()
                    if self.path == '/gzip':
                        self.wfile.write(gzip.compress(b'x' * 2 * 1024 * 1024))
                    elif self.path in ('/big', '/redirect'):
                        self.wfile.write(b'x' * 2 * 1024 * 1024)
                    elif self.path == '/drip':
                        for _ in range(100):
                            self.wfile.write(b'x')
                            self.wfile.flush()
                            time.sleep(0.1)
                    elif self.path == '/cookies':
                        self.wfile.write(self.headers.get('Cookie', '').encode())
                    else:
                        self.wfile.write(b'ok')
                except (OSError, ConnectionError):
                    pass

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_large_and_compressed_body_limits(self):
        for path in ('/big', '/gzip'):
            result = http_client.get(self.url + path, body_limit=1024)
            self.assertTrue(result.truncated)
            self.assertLess(len(result.text), 1100)

    def test_redirect_body_is_never_buffered(self):
        result = http_client.get(self.url + '/redirect', body_limit=1024)
        self.assertEqual(result.text, 'ok')
        self.assertFalse(result.truncated)

    def test_cookie_survives_same_host_redirect(self):
        result = http_client.get(self.url + '/cookie-redirect', cookies={'session': 'local-test'})
        self.assertIn('session=local-test', result.text)

    def test_http_proxy_uses_bounded_transport(self):
        result = http_client.get('http://unresolvable.invalid/proxy-test', proxies={'http': self.url})
        self.assertEqual(result.text, 'ok')

    @unittest.skipUnless(shutil.which('openssl'), 'Local TLS fixture requires openssl')
    def test_https_certificate_validation_and_timed_connection(self):
        with tempfile.TemporaryDirectory(prefix='acgo-tls-') as folder:
            cert, key = str(Path(folder) / 'cert.pem'), str(Path(folder) / 'key.pem')
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key,
                            '-out', cert, '-days', '1', '-subj', '/CN=localhost', '-addext',
                            'subjectAltName=DNS:localhost,IP:127.0.0.1'], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            server = ThreadingHTTPServer(('127.0.0.1', 0), self.server.RequestHandlerClass)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                response = http_client.get(f'https://127.0.0.1:{server.server_port}/ok', verify=cert)
                self.assertEqual(response.text, 'ok')
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_total_deadline_handles_slow_headers_and_drip_body(self):
        for path in ('/slow-headers', '/drip'):
            start = time.monotonic()
            with self.assertRaises(Exception):
                http_client.get(self.url + path, deadline_seconds=0.3)
            self.assertLess(time.monotonic() - start, 1.5)


class ResourceTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'linux', 'cgroup v2 is Linux-only')
    def test_invalid_cgroup_configuration_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix='acgo-invalid-cgroup-') as folder:
            with patch.dict(os.environ, {'SCRIPT_CGROUP_ROOT': folder}):
                result = run_script('python', "print('must-not-run')")
            self.assertFalse(result['success'], result)
            self.assertIn('资源限制不可用', result['error'])
            self.assertNotIn('must-not-run', result['output'])
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_script_memory_watchdog_and_pipe_output_limit(self):
        with patch('src.script_runner.SCRIPT_MEMORY_MB', 64):
            result = run_script('python', "import time\nx = bytearray(100 * 1024 * 1024)\ntime.sleep(5)")
        self.assertFalse(result['success'], result)
        self.assertIn('内存', result['error'])
        result = run_script('python', "import sys\nwhile True: sys.stdout.write('x' * 4096)", timeout=5)
        self.assertFalse(result['success'])
        self.assertIn('输出超过', result['error'])
        self.assertLessEqual(len(result['output']), 5000)

    @unittest.skipUnless(sys.platform == 'linux', 'POSIX process group cleanup')
    def test_descendants_are_killed_after_parent_exits(self):
        import psutil
        result = run_script('python', "import subprocess,sys\np=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\nprint(p.pid)")
        self.assertTrue(result['success'], result)
        pid = int(result['output'].strip())
        time.sleep(0.1)
        if psutil.pid_exists(pid):
            self.assertEqual(psutil.Process(pid).status(), psutil.STATUS_ZOMBIE)


@unittest.skipUnless(sys.platform == 'linux', 'Real SIGTERM lifecycle on Linux')
class RuntimeTests(unittest.TestCase):
    def test_production_server_single_instance_and_graceful_shutdown(self):
        import requests
        import psutil
        with tempfile.TemporaryDirectory(prefix='acgo-runtime-') as directory:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            env = dict(os.environ, DATA_DIR=directory, PORT=str(port), HOST='127.0.0.1', ADMIN_PASSWORD='runtime-test')
            root = Path(__file__).resolve().parents[1]
            with tempfile.TemporaryFile() as output:
                process = subprocess.Popen([sys.executable, 'run.py'], cwd=root, env=env, stdout=output, stderr=output)
                try:
                    url = f'http://127.0.0.1:{port}'
                    for _ in range(100):
                        if process.poll() is not None:
                            output.seek(0)
                            self.fail(output.read().decode())
                        try:
                            if requests.get(url + '/health', timeout=0.2).status_code == 200:
                                break
                        except requests.RequestException:
                            pass
                        time.sleep(0.05)
                    else:
                        self.fail('server startup timed out')
                    second = subprocess.run([sys.executable, 'run.py'], cwd=root, env=env, capture_output=True, timeout=10)
                    self.assertNotEqual(second.returncode, 0)
                    self.assertIn('已有服务运行', second.stderr.decode())
                    client = requests.Session()
                    try:
                        client.post(url + '/login', data={'password': 'runtime-test'}, timeout=2)
                        account = client.post(url + '/api/accounts', json=dict(name='runtime-script', task_type='python',
                            script_content="import time; time.sleep(0.2); print('runtime-ok')", enabled=False,
                            cron_expr='0 8 * * *', retry_count=0), timeout=2).json()['data']['id']
                        response = client.post(url + f'/api/checkin/{account}', timeout=2)
                        self.assertEqual(response.status_code, 202)
                        execution = response.json()['data']['id']
                        for _ in range(100):
                            result = client.get(url + f'/api/executions/{execution}', timeout=2).json()['data']
                            if result['state'] not in models.ACTIVE_STATES:
                                break
                            time.sleep(0.05)
                        self.assertEqual(result['state'], 'success', result)
                        print(f'Linux runtime RSS after script: {psutil.Process(process.pid).memory_info().rss / 1024**2:.1f} MiB')
                    finally:
                        client.close()
                    process.terminate()
                    self.assertEqual(process.wait(timeout=10), 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()


if __name__ == '__main__':
    unittest.main()
