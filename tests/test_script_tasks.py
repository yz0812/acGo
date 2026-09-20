"""Run with: python -m unittest discover -s tests -v (isolated temporary databases)."""
import importlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta
from src.execution import engine
from src.http_client import Response

from src import models, scheduler
from src.script_runner import run_script


class RunnerTests(unittest.TestCase):
    def test_python_unicode_and_stderr(self):
        result = run_script('python', "import sys\nprint('签到成功')\nprint('提示', file=sys.stderr)")
        self.assertTrue(result['success'], result)
        self.assertEqual(result['exit_code'], 0)
        self.assertIn('签到成功', result['output'])
        self.assertIn('[stderr]\n提示', result['output'])

    @unittest.skipUnless(shutil.which('node'), 'Node.js unavailable')
    def test_javascript_success_and_failure(self):
        for source, code in [("console.log('签到成功')", 0), ("console.error('失败'); process.exitCode = 2", 2)]:
            with self.subTest(code=code):
                result = run_script('javascript', source)
                self.assertEqual(result['exit_code'], code, result)
                self.assertEqual(result['success'], code == 0)

    def test_python_failure(self):
        result = run_script('python', "raise RuntimeError('业务失败')")
        self.assertFalse(result['success'])
        self.assertIn('业务失败', result['output'])
        self.assertNotEqual(result['exit_code'], 0)

    def test_timeout_preserves_output(self):
        result = run_script('python', "import time\nprint('started')\ntime.sleep(20)", timeout=1)
        self.assertFalse(result['success'])
        self.assertIn('超时', result['error'])
        self.assertIn('started', result['output'])

    def test_large_output_is_bounded(self):
        result = run_script('python', "print('x' * 200000)")
        self.assertFalse(result['success'])
        self.assertIn('输出超过', result['error'])
        self.assertLessEqual(len(result['output']), 5000)

    def test_missing_node(self):
        with patch('src.script_runner.shutil.which', return_value=None):
            result = run_script('javascript', 'console.log(1)')
        self.assertFalse(result['success'])
        self.assertIsNone(result['exit_code'])
        self.assertIn('Node.js', result['error'])

    @unittest.skipUnless(shutil.which('node'), 'Node.js unavailable')
    def test_ui_demos_against_local_http_endpoint(self):
        class EchoHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"success":true}')

            def log_message(self, *args):
                pass

        editor = Path(__file__).resolve().parents[1] / 'static/js/task-editor.js'
        extract = "const fs=require('fs'),vm=require('vm'); const c={}; vm.createContext(c); vm.runInContext(fs.readFileSync(process.argv[1],'utf8')+';this.demos=TASK_DEMOS',c); console.log(JSON.stringify(c.demos));"
        demos = json.loads(subprocess.check_output([shutil.which('node'), '-e', extract, str(editor)], encoding='utf-8'))
        server = ThreadingHTTPServer(('127.0.0.1', 0), EchoHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for task_type, source in demos.items():
                with self.subTest(task_type=task_type):
                    source = source.replace('https://httpbin.org/get', f'http://127.0.0.1:{server.server_port}/get')
                    if task_type == 'curl':
                        params = scheduler.parse_curl_command(source)
                        result = scheduler.requests.request(**params, timeout=5)
                        self.assertEqual(result.status_code, 200)
                    else:
                        result = run_script(task_type, source)
                        self.assertTrue(result['success'], result)
                        self.assertIn('"success":true', result['output'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class AccountTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='acgo-tests-')
        cls.original_database = models.db.database
        models.db.close()
        models.db.init(str(Path(cls.folder.name) / 'test.db'))
        with patch('src.models.init_config'):
            models.init_db()
            cls.module = importlib.import_module('src.app')
        cls.module.app.config.update(TESTING=True)

    @classmethod
    def tearDownClass(cls):
        models.db.close()
        models.db.init(cls.original_database)
        cls.folder.cleanup()

    def setUp(self):
        models.db.connect(reuse_if_open=True)
        models.CheckinLog.delete().execute()
        models.Account.delete().execute()
        models.db.close()
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session['logged_in'] = True
        self.notifications = patch('src.notifier.send_all_notifications').start()
        self.addCleanup(patch.stopall)

    def create(self, task_type='python', source="print('签到成功')", **extra):
        payload = dict(name='测试账号', task_type=task_type, script_content=source,
                       cron_expr='0 8 * * *', enabled=False, retry_count=0)
        payload.update(extra)
        response = self.client.post('/api/accounts', json=payload)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json['data']['id']

    def execute(self, account_id):
        response = self.client.post(f'/api/checkin/{account_id}')
        self.assertEqual(response.status_code, 202, response.json)
        execution_id = response.json['data']['id']
        for _ in range(12):
            item = models.Execution.get_by_id(execution_id)
            if item.state not in models.ACTIVE_STATES:
                break
            engine.run_one(item.kind, now=datetime.now() + timedelta(days=2))
        item = self.client.get(f'/api/executions/{execution_id}').json['data']
        return {'success': item['state'] == 'success', 'data': item['result']}

    def test_python_full_flow_and_historical_preview(self):
        account_id = self.create()
        self.assertEqual(scheduler.execute_checkin(account_id)['status'], 'skipped')
        result = self.execute(account_id)
        self.assertTrue(result['success'], result)
        log_id = result['data']['log_id']
        log = self.client.get('/api/logs').json['data'][0]
        self.assertEqual(log['task_type'], 'python')
        self.assertEqual(log['exit_code'], 0)
        self.assertIsNone(log['response_code'])
        self.assertIn('签到成功', log['response_body'])
        response = self.client.put(f'/api/accounts/{account_id}', json={
            'task_type': 'curl', 'curl_command': 'curl https://example.com',
        })
        self.assertEqual(response.status_code, 200)
        historical = self.client.get(f'/api/logs/{log_id}/preview').json['data']
        self.assertEqual(historical['task_type'], 'python')
        self.assertIn('print', historical['script_content'])
        current = self.client.get(f'/api/accounts/{account_id}/preview').json['data']
        self.assertEqual(current['url'], 'https://example.com')

    @unittest.skipUnless(shutil.which('node'), 'Node.js unavailable')
    def test_javascript_execution_and_export_import(self):
        account_id = self.create('javascript', "console.log('JS完成')")
        result = self.execute(account_id)
        self.assertTrue(result['success'], result)
        exported = self.client.get('/api/accounts/export').json['data']
        response = self.client.post('/api/accounts/import', json={'accounts': exported})
        self.assertTrue(response.json['success'], response.json)
        accounts = self.client.get('/api/accounts').json['data']
        self.assertEqual(len(accounts), 2)
        self.assertTrue(all(a['task_type'] == 'javascript' for a in accounts))
        self.assertTrue(all(self.client.get(f"/api/accounts/{a['id']}").json['data']['script_content'] == "console.log('JS完成')" for a in accounts))

    def test_legacy_curl_payload_and_execution(self):
        account_id = self.create(task_type='curl', curl_command='curl https://example.com')
        response = self.client.post('/api/accounts/import', json={'accounts': [
            {'name': '旧账号', 'curl_command': 'curl https://example.com', 'enabled': False},
        ]})
        self.assertTrue(response.json['success'], response.json)
        with patch('src.scheduler.requests.request') as request:
            request.return_value = Response(200, 'ok')
            result = self.execute(account_id)
        self.assertTrue(result['success'])
        log = self.client.get('/api/logs').json['data'][0]
        self.assertEqual(log['response_code'], 200)
        self.assertEqual(log['task_type'], 'curl')

    def test_failed_scripts_retry_and_notify_once(self):
        account_id = self.create(source="raise RuntimeError('failed')", retry_count=1, retry_interval=1)
        result = self.execute(account_id)
        self.assertFalse(result['success'])
        logs = self.client.get('/api/logs').json['data']
        self.assertEqual(len(logs), 2)
        self.assertTrue(all(log['status'] == 'failed' for log in logs))
        self.notifications.assert_called_once()

    def test_validation_rejects_unknown_empty_and_invalid_retry(self):
        for fields in [{'task_type': 'shell'}, {'script_content': '  '},
                       {'script_content': 123}, {'retry_count': -1}, {'retry_interval': 0}]:
            payload = dict(name='invalid', task_type='python', script_content='print(1)', cron_expr='0 8 * * *')
            payload.update(fields)
            response = self.client.post('/api/accounts', json=payload)
            self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.client.get('/api/accounts').json['data'], [])

    def test_authentication_required(self):
        self.assertNotEqual(self.module.app.test_client().post('/api/accounts', json={}).status_code, 200)

    def test_edit_preserves_script_when_content_omitted(self):
        account_id = self.create()
        response = self.client.put(f'/api/accounts/{account_id}', json={'name': '改名'})
        self.assertEqual(response.status_code, 200)
        preview = self.client.get(f'/api/accounts/{account_id}/preview').json['data']
        self.assertEqual(preview['script_content'], "print('签到成功')")

    def test_enabled_script_uses_scheduled_execution_path(self):
        account_id = self.create(enabled=True)
        try:
            job = scheduler.scheduler.get_job(f'account_{account_id}')
            self.assertIsNotNone(job)
            result = job.func(*job.args)
            self.assertEqual(result['status'], 'accepted')
            engine.run_one('script')
            item = models.Execution.get_by_id(result['execution']['id'])
            self.assertEqual(item.state, 'success')
        finally:
            scheduler.remove_job(account_id)


class MigrationTests(unittest.TestCase):
    def test_legacy_database_migration_is_repeatable(self):
        original = models.db.database
        with tempfile.TemporaryDirectory(prefix='acgo-migration-') as folder:
            models.db.close()
            models.db.init(str(Path(folder) / 'legacy.db'))
            try:
                models.db.execute_sql('CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT, curl_command TEXT, created_at DATETIME)')
                models.db.execute_sql("INSERT INTO accounts VALUES (1, 'legacy', 'curl https://example.com', '2026-01-01')")
                models.db.execute_sql('CREATE TABLE checkin_logs (id INTEGER PRIMARY KEY, status TEXT, executed_at DATETIME)')
                models.migrate_database()
                models.migrate_database()
                row = models.db.execute_sql('SELECT task_type, script_content, curl_command FROM accounts').fetchone()
                self.assertEqual(row, ('curl', '', 'curl https://example.com'))
                self.assertIn('exit_code', {c.name for c in models.db.get_columns('checkin_logs')})
            finally:
                models.db.close()
                models.db.init(original)


if __name__ == '__main__':
    unittest.main()
