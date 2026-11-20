import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

from store import Conflict, DynamoStore, SQLiteStore, json_default

STATUSES = ('pendiente', 'en progreso', 'resuelto')
CATEGORIES = ('tarea', 'examen', 'trabajo', 'proyecto personal', 'quehaceres', 'vida social')
PRIORITIES = ('baja', 'media', 'alta')
SESSION_SECONDS = 30 * 24 * 60 * 60
STATIC = Path(__file__).parent / 'web'
STORE = None
logger = logging.getLogger(__name__)


def database():
    global STORE
    if STORE is None:
        STORE = DynamoStore(os.environ['TABLE_NAME']) if os.environ.get('TABLE_NAME') else SQLiteStore(os.environ.get('LOCAL_DB', '.local/tickets.db'))
    return STORE


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def now_ms():
    return int(time.time() * 1000)


def reply(status, data, cookies=None):
    result = {'statusCode': status, 'headers': {
        'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store',
        'x-content-type-options': 'nosniff', 'referrer-policy': 'same-origin',
    }, 'body': json.dumps(data, default=json_default, ensure_ascii=False)}
    if cookies:
        result['cookies'] = cookies
    return result


def cookie(value, age=SESSION_SECONDS):
    secure = '; Secure' if os.environ.get('TABLE_NAME') else ''
    return f'ticket_session={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}{secure}'


def text_field(data, name, maximum, minimum=1):
    value = data.get(name)
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise APIError(400, f'El campo {name} debe tener entre {minimum} y {maximum} caracteres.')
    return value.strip()


def public_user(user):
    return {k: user[k] for k in ('id', 'name', 'email')}


def public_task(task, clock=None):
    result = {k: v for k, v in task.items() if k not in ('pk', 'sk')}
    end = task.get('resolved_at') or clock or now_ms()
    result['elapsed_ms'] = max(0, end - task['created_at'])
    result['active_ms'] = task['tracked_ms'] + (max(0, (clock or now_ms()) - task['active_since']) if task.get('active_since') else 0)
    return result


def session_token(event):
    parsed = SimpleCookie()
    try:
        parsed.load('; '.join(event.get('cookies') or [event.get('headers', {}).get('cookie', '')]))
    except Exception:
        return ''
    return parsed['ticket_session'].value if 'ticket_session' in parsed else ''


def require_user(event):
    token = session_token(event)
    if not token:
        raise APIError(401, 'Inicia sesión para continuar.')
    key = hashlib.sha256(token.encode()).hexdigest()
    session = database().get('SESSION#' + key, 'SESSION')
    if not session or session['ttl'] <= int(time.time()):
        raise APIError(401, 'Tu sesión terminó. Vuelve a ingresar.')
    user = database().get(session['user_pk'], 'PROFILE')
    if not user:
        raise APIError(401, 'Usuario no encontrado.')
    return user


def authenticate(data, register):
    email = text_field(data, 'email', 254).lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise APIError(400, 'Ingresa un correo válido.')
    password = data.get('password')
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise APIError(400, 'La contraseña debe tener entre 8 y 128 caracteres.')
    pk = 'USER#' + hashlib.sha256(email.encode()).hexdigest()
    user = database().get(pk, 'PROFILE')
    if register:
        if user:
            raise APIError(409, 'Este correo ya está registrado. Ingresa con tu contraseña.')
        name = text_field(data, 'name', 60)
        salt = secrets.token_hex(16)
        user = {'pk': pk, 'sk': 'PROFILE', 'id': uuid.uuid4().hex, 'name': name, 'email': email, 'salt': salt,
                'password_hash': hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 310000).hex(), 'created_at': now_ms()}
        try:
            database().put(user, create=True)
        except Conflict:
            raise APIError(409, 'Este correo ya está registrado.')
    else:
        salt = user['salt'] if user else '0' * 32
        candidate = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 310000).hex()
        if not user or not hmac.compare_digest(candidate, user['password_hash']):
            raise APIError(401, 'Correo o contraseña incorrectos.')
    token = secrets.token_urlsafe(32)
    database().put({'pk': 'SESSION#' + hashlib.sha256(token.encode()).hexdigest(), 'sk': 'SESSION', 'user_pk': pk, 'ttl': int(time.time()) + SESSION_SECONDS})
    return reply(201 if register else 200, {'user': public_user(user)}, [cookie(token)])


def validated_task(data):
    result = {'title': text_field(data, 'title', 120), 'description': text_field(data, 'description', 3000, 0)}
    for name, choices in [('status', STATUSES), ('category', CATEGORIES), ('priority', PRIORITIES)]:
        if data.get(name) not in choices:
            raise APIError(400, f'{name}: selecciona una opción válida.')
        result[name] = data[name]
    deadline = data.get('deadline')
    try:
        if not isinstance(deadline, str) or len(deadline) > 40:
            raise ValueError()
        parsed = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        result['deadline'] = parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        raise APIError(400, 'Selecciona una fecha límite válida con zona horaria.')
    return result


def save_task(user, task_id, data, create):
    try:
        if str(uuid.UUID(task_id)) != task_id:
            raise ValueError()
    except ValueError:
        raise APIError(400, 'Identificador de tarea inválido.')
    pk, sk = user['pk'], 'TASK#' + task_id
    existing = database().get(pk, sk)
    fields = validated_task(data)
    if create and existing:
        # A retry of the same PUT never creates a second ticket or resets its timing.
        return reply(200, {'task': public_task(existing)})
    if not create and not existing:
        raise APIError(404, 'No se encontró esta tarea.')
    if not create and (type(data.get('version')) is not int or data['version'] != existing['version']):
        raise APIError(409, 'La tarea cambió en otra ventana. Actualiza e inténtalo de nuevo.')
    clock = now_ms()
    task = existing.copy() if existing else {'pk': pk, 'sk': sk, 'id': task_id, 'created_at': clock, 'tracked_ms': 0, 'active_since': None, 'resolved_at': None, 'version': 0}
    previous = task.get('status')
    status = fields['status']
    if previous == 'en progreso' and status != previous:
        task['tracked_ms'] += max(0, clock - task['active_since'])
        task['active_since'] = None
    elif previous != 'en progreso' and status == 'en progreso':
        task['active_since'] = clock
    if status == 'resuelto' and previous != status:
        task['resolved_at'] = clock
    elif status != 'resuelto':
        task['resolved_at'] = None
    task.update(fields, updated_at=clock, version=task['version'] + 1)
    try:
        database().put(task, create=create, version=None if create else data['version'])
    except Conflict:
        if create:
            return reply(200, {'task': public_task(database().get(pk, sk))})
        raise APIError(409, 'La tarea cambió en otra ventana. Actualiza e inténtalo de nuevo.')
    return reply(201 if create else 200, {'task': public_task(task)})


def analytics(tasks):
    clock = now_ms()
    rows = []
    for category in CATEGORIES:
        group = [public_task(t, clock) for t in tasks if t['category'] == category]
        rows.append({'category': category, 'count': len(group), 'resolved': sum(t['status'] == 'resuelto' for t in group), 'active_ms': sum(t['active_ms'] for t in group)})
    done = [public_task(t, clock) for t in tasks if t['status'] == 'resuelto']
    return {'categories': rows, 'total': len(tasks), 'resolved': len(done), 'average_resolution_ms': sum(t['elapsed_ms'] for t in done) // len(done) if done else 0, 'server_time': clock}


def handler(event, context=None):
    try:
        headers = {k.lower(): v for k, v in event.get('headers', {}).items()}
        event['headers'] = headers
        path = event.get('rawPath', '/')
        method = event.get('requestContext', {}).get('http', {}).get('method', 'GET')
        if method == 'GET' and path in ('/', '/index.html', '/styles.css', '/app.js', '/favicon.svg'):
            filename = 'index.html' if path == '/' else path[1:]
            mime = {'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml'}[Path(filename).suffix]
            return {'statusCode': 200, 'headers': {'content-type': mime + '; charset=utf-8', 'cache-control': 'no-cache', 'x-content-type-options': 'nosniff', 'referrer-policy': 'same-origin', 'content-security-policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"}, 'body': (STATIC / filename).read_text(encoding='utf-8')}
        if path == '/api/health' and method == 'GET':
            return reply(200, {'ok': True})
        if not path.startswith('/api/'):
            raise APIError(404, 'Página no encontrada.')
        if method not in ('GET', 'HEAD'):
            origin = headers.get('origin')
            host = headers.get('host') or event.get('requestContext', {}).get('domainName')
            if origin and urlsplit(origin).netloc != host:
                raise APIError(403, 'Origen no permitido.')
            if not headers.get('content-type', '').lower().startswith('application/json'):
                raise APIError(415, 'Envía los datos como application/json.')
        raw = event.get('body') or '{}'
        if len(raw) > 20000:
            raise APIError(413, 'La solicitud es demasiado grande.')
        if event.get('isBase64Encoded'):
            raw = base64.b64decode(raw).decode('utf-8')
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
        except (ValueError, TypeError):
            raise APIError(400, 'El formato de los datos no es válido.')
        if path in ('/api/register', '/api/login') and method == 'POST':
            return authenticate(data, path == '/api/register')
        user = require_user(event)
        if path == '/api/me' and method == 'GET':
            return reply(200, {'user': public_user(user)})
        if path == '/api/logout' and method == 'POST':
            database().delete('SESSION#' + hashlib.sha256(session_token(event).encode()).hexdigest(), 'SESSION')
            return reply(200, {'ok': True}, [cookie('', 0)])
        if path == '/api/tasks' and method == 'GET':
            tasks = database().query(user['pk'], 'TASK#')
            return reply(200, {'tasks': [public_task(t) for t in sorted(tasks, key=lambda t: t['created_at'], reverse=True)], 'analytics': analytics(tasks)})
        match = re.fullmatch(r'/api/tasks/([a-f0-9-]+)', path)
        if match and method in ('PUT', 'PATCH'):
            return save_task(user, match[1], data, method == 'PUT')
        raise APIError(404, 'Ruta no encontrada.')
    except APIError as exc:
        return reply(exc.status, {'error': exc.message})
    except Exception:
        logger.exception('Request failed')
        return reply(503, {'error': 'No pudimos completar la solicitud. Tus datos guardados siguen intactos; inténtalo de nuevo.'})
