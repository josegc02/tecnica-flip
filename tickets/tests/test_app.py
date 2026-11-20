import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import app
from store import SQLiteStore


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / 'test.db')
        app.STORE = SQLiteStore(self.db_path)
        self.cookie = ''
        self.account = {'name': 'Ana', 'email': 'ana@example.com', 'password': 'MiClave123!'}
        self.clock = 1800000000000
        self.clock_patch = patch.object(app, 'now_ms', side_effect=lambda: self.clock)
        self.clock_patch.start()

    def tearDown(self):
        self.clock_patch.stop()
        app.STORE = None
        self.temp.cleanup()

    def request(self, path, method='GET', data=None, expected=200, cookie=None, headers=None):
        result = app.handler({'rawPath': path, 'requestContext': {'http': {'method': method}}, 'headers': {'host': 'localhost', 'content-type': 'application/json', **(headers or {})}, 'cookies': [self.cookie if cookie is None else cookie], 'body': json.dumps(data) if data is not None else None})
        self.assertEqual(result['statusCode'], expected, result['body'])
        if result.get('cookies'):
            self.cookie = result['cookies'][0].split(';')[0]
        return json.loads(result['body'])

    def register(self, **overrides):
        return self.request('/api/register', 'POST', {**self.account, **overrides}, 201)

    def task(self, **overrides):
        task_id = str(uuid.uuid4())
        data = {'title': 'Estudiar álgebra', 'description': 'Capítulo 2', 'deadline': '2030-11-20T18:00:00-05:00', 'status': 'pendiente', 'category': 'examen', 'priority': 'alta', **overrides}
        return self.request('/api/tasks/' + task_id, 'PUT', data, 201)['task'], data

    def update(self, task, **changes):
        return self.request('/api/tasks/' + task['id'], 'PATCH', {**task, **changes})['task']

    def test_registration_login_logout_and_password_storage(self):
        user = self.register()['user']
        self.assertEqual(set(user), {'id', 'email', 'name'})
        self.assertEqual(self.request('/api/me')['user'], user)
        pk = 'USER#' + app.hashlib.sha256(self.account['email'].encode()).hexdigest()
        saved = app.STORE.get(pk, 'PROFILE')
        self.assertNotIn(self.account['password'], json.dumps(saved))
        token = self.cookie
        self.request('/api/logout', 'POST', {})
        self.request('/api/me', expected=401, cookie=token)
        self.request('/api/login', 'POST', {**self.account, 'password': 'incorrecta'}, 401)
        self.request('/api/login', 'POST', self.account)
        self.assertEqual(self.request('/api/me')['user'], user)

    def test_email_normalization_and_duplicate_registration(self):
        self.register(email='  ANA@EXAMPLE.COM  ')
        self.request('/api/register', 'POST', self.account, 409)
        self.request('/api/login', 'POST', self.account)

    def test_persistence_after_new_store_and_new_session(self):
        self.register()
        task, _ = self.task()
        app.STORE = SQLiteStore(self.db_path)
        self.assertEqual(self.request('/api/tasks')['tasks'][0]['id'], task['id'])
        self.request('/api/logout', 'POST', {})
        self.request('/api/login', 'POST', self.account)
        self.assertEqual(len(self.request('/api/tasks')['tasks']), 1)

    def test_users_cannot_read_or_change_each_others_tasks(self):
        self.register()
        task, _ = self.task()
        first_cookie = self.cookie
        self.register(name='Beto', email='beto@example.com')
        self.assertEqual(self.request('/api/tasks')['tasks'], [])
        self.request('/api/tasks/' + task['id'], 'PATCH', {**task, 'title': 'Ajena'}, 404)
        self.assertEqual(self.request('/api/tasks', cookie=first_cookie)['tasks'][0]['title'], task['title'])
        self.request('/api/tasks', cookie='', expected=401)

    def test_timing_pause_reopen_and_resolution(self):
        self.register()
        task, _ = self.task()
        created = self.clock
        self.clock += 60000
        task = self.update(task, status='en progreso')
        self.clock += 120000
        task = self.update(task, status='pendiente')
        self.assertEqual(task['active_ms'], 120000)
        self.clock += 300000
        task = self.update(task, status='en progreso')
        self.clock += 180000
        task = self.update(task, status='resuelto')
        self.assertEqual(task['active_ms'], 300000)
        self.assertEqual(task['elapsed_ms'], self.clock - created)
        finished = task['resolved_at']
        self.clock += 60000
        task = self.update(task, title='Nuevo título')
        self.assertEqual(task['resolved_at'], finished)
        self.assertEqual(task['active_ms'], 300000)
        task = self.update(task, status='en progreso')
        self.assertIsNone(task['resolved_at'])
        self.clock += 60000
        task = self.update(task, status='resuelto')
        self.assertEqual(task['active_ms'], 360000)

    def test_concurrent_edit_does_not_overwrite(self):
        self.register()
        task, _ = self.task()
        current = self.update(task, title='Edición nueva')
        self.request('/api/tasks/' + task['id'], 'PATCH', {**task, 'title': 'Edición vieja'}, 409)
        self.assertEqual(self.request('/api/tasks')['tasks'][0]['title'], current['title'])

    def test_create_retry_is_idempotent(self):
        self.register()
        task, original = self.task()
        self.clock += 60000
        task = self.update(task, status='en progreso')
        retry = self.request('/api/tasks/' + task['id'], 'PUT', original)['task']
        self.assertEqual(retry['status'], 'en progreso')
        self.assertEqual(retry['created_at'], task['created_at'])
        self.assertEqual(len(self.request('/api/tasks')['tasks']), 1)

    def test_analytics_use_all_six_categories_and_active_time(self):
        self.register()
        first, _ = self.task(category='trabajo', status='en progreso')
        self.clock += 60000
        self.update(first, status='resuelto')
        second, _ = self.task(category='vida social', status='en progreso')
        self.clock += 120000
        result = self.request('/api/tasks')['analytics']
        rows = {row['category']: row for row in result['categories']}
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows['trabajo']['active_ms'], 60000)
        self.assertEqual(rows['vida social']['active_ms'], 120000)
        self.assertEqual(result['average_resolution_ms'], 60000)
        self.assertEqual(result['resolved'], 1)

    def test_task_validation(self):
        self.register()
        _, valid = self.task()
        for field, value in [('title', ''), ('title', 'a' * 121), ('category', 'otro'), ('status', 'hecho'), ('priority', 'urgente'), ('deadline', 'no es fecha'), ('deadline', '2030-01-01'), ('description', 'x' * 3001)]:
            with self.subTest(field=field, value=str(value)[:20]):
                self.request('/api/tasks/' + str(uuid.uuid4()), 'PUT', {**valid, field: value}, 400)

    def test_cross_origin_requests_and_non_json_are_rejected(self):
        self.register()
        self.request('/api/logout', 'POST', {}, 403, headers={'origin': 'https://evil.example'})
        self.request('/api/logout', 'POST', {}, 415, headers={'content-type': 'text/plain'})
        self.request('/api/me')

    def test_expired_sessions_are_rejected_before_ttl_cleanup(self):
        self.register()
        with patch.object(app.time, 'time', return_value=app.time.time() + app.SESSION_SECONDS + 1):
            self.request('/api/me', expected=401)

    def test_static_assets_and_unknown_paths(self):
        for path in ('/', '/app.js', '/styles.css', '/favicon.svg'):
            result = app.handler({'rawPath': path})
            self.assertEqual(result['statusCode'], 200)
            self.assertIn('content-security-policy', result['headers'])
            self.assertTrue(result['body'])
        self.request('/../app.py', expected=404)

    def test_secure_cookie_in_aws(self):
        with patch.dict(os.environ, {'TABLE_NAME': 'test'}):
            self.assertIn('; Secure', app.cookie('example'))
            self.assertIn('; HttpOnly', app.cookie('example'))
            self.assertIn('SameSite=Strict', app.cookie('example'))


if __name__ == '__main__':
    unittest.main()
