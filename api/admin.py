# Vercel Python serverless function — Admin operations
# Called by admin.html JavaScript to manage license keys

from http.server import BaseHTTPRequestHandler
import json, os, urllib.request, urllib.parse, ssl, hashlib
from datetime import datetime

# ── Upstash Redis helpers (same as validate.py) ──────────────────────────────
def _redis(method: str, *args):
    url   = os.environ['UPSTASH_URL']
    token = os.environ['UPSTASH_TOKEN']
    body  = json.dumps([[method] + list(args)]).encode()
    ctx   = ssl._create_unverified_context()
    req   = urllib.request.Request(
        url + '/pipeline',
        data=body,
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, context=ctx, timeout=8) as r:
        return json.loads(r.read())[0]['result']


def get_key(k):
    raw = _redis('HGETALL', f'key:{k}')
    if not raw: return None
    it = iter(raw)
    return dict(zip(it, it))


def all_key_ids():
    return _redis('SMEMBERS', 'all_keys') or []


def generate_key():
    import random, string
    parts = [''.join(random.choices(string.ascii_uppercase + string.digits, k=4)) for _ in range(4)]
    return '-'.join(parts)


# ── CORS headers ──────────────────────────────────────────────────────────────
CORS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, X-Admin-Password',
}

# ── Vercel handler ────────────────────────────────────────────────────────────
class handler(BaseHTTPRequestHandler):

    def _json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        for h, v in CORS.items():
            self.send_header(h, v)
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def _check_admin(self):
        pwd = self.headers.get('X-Admin-Password', '')
        return pwd == os.environ.get('ADMIN_PASSWORD', 'RoyelAdmin@2026')

    def do_OPTIONS(self):
        self.send_response(200)
        for h, v in CORS.items():
            self.send_header(h, v)
        self.end_headers()

    def do_GET(self):
        """List all keys and activity log."""
        if not self._check_admin():
            return self._json({'error': 'Unauthorized'}, 401)

        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        action = params.get('action', ['list'])[0]

        if action == 'list':
            key_ids = all_key_ids()
            keys = {}
            for kid in key_ids:
                entry = get_key(kid)
                if entry:
                    keys[kid] = entry
            return self._json({'ok': True, 'keys': keys})

        elif action == 'log':
            raw = _redis('LRANGE', 'log', '0', '99') or []
            log = [json.loads(x) for x in raw]
            return self._json({'ok': True, 'log': log})

        elif action == 'stats':
            key_ids = all_key_ids()
            total = len(key_ids)
            active = 0
            for kid in key_ids:
                entry = get_key(kid)
                if entry and entry.get('active', '1') != '0':
                    active += 1
            return self._json({'ok': True, 'total': total, 'active': active})

        return self._json({'error': 'Unknown action'}, 400)

    def do_POST(self):
        """Add, generate, toggle, delete keys."""
        if not self._check_admin():
            return self._json({'error': 'Unauthorized'}, 401)

        length = int(self.headers.get('Content-Length', 0))
        body   = json.loads(self.rfile.read(length) or b'{}')
        action = body.get('action', '')

        if action == 'generate':
            k      = generate_key()
            name   = body.get('name', '')
            expiry = body.get('expiry', '')
            _redis('SADD', 'all_keys', k)
            _redis('HSET', f'key:{k}',
                   'name', name, 'active', '1',
                   'expiry', expiry, 'count', '0',
                   'created', datetime.utcnow().strftime('%Y-%m-%d %H:%M'))
            return self._json({'ok': True, 'key': k, 'msg': f'Key generated: {k}'})

        elif action == 'add':
            k      = body.get('key', '').upper().strip()
            name   = body.get('name', '')
            expiry = body.get('expiry', '')
            if not k:
                return self._json({'ok': False, 'msg': 'No key provided'})
            if get_key(k):
                return self._json({'ok': False, 'msg': 'Key already exists'})
            _redis('SADD', 'all_keys', k)
            _redis('HSET', f'key:{k}',
                   'name', name, 'active', '1',
                   'expiry', expiry, 'count', '0',
                   'created', datetime.utcnow().strftime('%Y-%m-%d %H:%M'))
            return self._json({'ok': True, 'key': k, 'msg': f'Key added: {k}'})

        elif action == 'toggle':
            k     = body.get('key', '')
            entry = get_key(k)
            if not entry:
                return self._json({'ok': False, 'msg': 'Key not found'})
            new_state = '0' if entry.get('active', '1') == '1' else '1'
            _redis('HSET', f'key:{k}', 'active', new_state)
            state_label = 'ENABLED' if new_state == '1' else 'DISABLED'
            return self._json({'ok': True, 'msg': f'Key {state_label}: {k}'})

        elif action == 'delete':
            k = body.get('key', '')
            _redis('SREM', 'all_keys', k)
            _redis('DEL', f'key:{k}')
            return self._json({'ok': True, 'msg': f'Key deleted: {k}'})

        elif action == 'clearlog':
            _redis('DEL', 'log')
            return self._json({'ok': True, 'msg': 'Log cleared'})

        return self._json({'error': 'Unknown action'}, 400)

    def log_message(self, *args):
        pass

