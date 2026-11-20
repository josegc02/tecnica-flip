"""Local development server. SQLite is local-only; deployment always uses DynamoDB."""
import argparse
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.pop('TABLE_NAME', None)
os.environ.setdefault('LOCAL_DB', str(ROOT / '.local' / 'tickets.db'))
import app


class Handler(BaseHTTPRequestHandler):
    def handle_request(self):
        size = int(self.headers.get('Content-Length', 0))
        if size > 20000:
            self.send_error(413)
            return
        event = {
            'rawPath': self.path.split('?')[0],
            'headers': dict(self.headers),
            'body': self.rfile.read(size).decode('utf-8') if size else None,
            'requestContext': {'http': {'method': self.command}},
        }
        result = app.handler(event)
        content = result.get('body', '').encode('utf-8')
        self.send_response(result['statusCode'])
        for key, value in result.get('headers', {}).items():
            self.send_header(key, value)
        for cookie in result.get('cookies', []):
            self.send_header('Set-Cookie', cookie)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_request


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Ritmo local: http://127.0.0.1:{args.port}', flush=True)
    print('Desarrollo local con SQLite. AWS usa DynamoDB.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
