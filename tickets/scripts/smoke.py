"""Real end-to-end HTTP checks. Creates an isolated test account and its tickets."""
import http.cookiejar
import json
import secrets
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone


def run(endpoint):
    endpoint = endpoint.rstrip('/')
    jar = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def request(path, method='GET', data=None, expected=200):
        req = urllib.request.Request(endpoint + '/api' + path, method=method, data=json.dumps(data).encode() if data is not None else None, headers={'Content-Type': 'application/json'})
        try:
            response = client.open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = json.load(response)
            if response.status != expected:
                raise RuntimeError(f'{method} {path}: esperado {expected}, recibido {response.status}: {body}')
            return body

    account = {'name': 'Prueba de despliegue', 'email': f'qa-{uuid.uuid4().hex}@example.invalid', 'password': secrets.token_urlsafe(24)}
    request('/register', 'POST', account, 201)
    request('/me')
    task_id = str(uuid.uuid4())
    data = {'title': 'Verificación automática de despliegue', 'description': 'Cuenta aislada de pruebas.', 'deadline': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), 'priority': 'media', 'category': 'tarea', 'status': 'pendiente'}
    task = request('/tasks/' + task_id, 'PUT', data, 201)['task']
    duplicate = request('/tasks/' + task_id, 'PUT', data)['task']
    assert duplicate['id'] == task['id']
    task = request('/tasks/' + task_id, 'PATCH', {**task, 'status': 'en progreso'})['task']
    task = request('/tasks/' + task_id, 'PATCH', {**task, 'status': 'resuelto'})['task']
    assert task['resolved_at'] is not None
    saved = request('/tasks')
    assert len(saved['tasks']) == 1 and saved['analytics']['resolved'] == 1
    request('/logout', 'POST', {})
    request('/me', expected=401)
    request('/login', 'POST', account)
    persisted = request('/tasks')['tasks']
    assert len(persisted) == 1 and persisted[0]['id'] == task_id and persisted[0]['status'] == 'resuelto'
    request('/logout', 'POST', {})
    print('OK: registro, sesión, creación sin duplicados, estados, historial, logout y persistencia al reingresar.')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Uso: python scripts/smoke.py https://TU-API.execute-api.us-east-1.amazonaws.com')
    run(sys.argv[1])
