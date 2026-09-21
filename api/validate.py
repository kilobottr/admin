# Vercel Python serverless function — License key validation
# Called by exe: GET /api/validate?k=KEY&m=MACHINE_ID
# Returns: {"ok": true, "name": "SSR TELECOM"} or {"ok": false, "msg": "..."}

from http.server import BaseHTTPRequestHandler
import json, os, urllib.request, urllib.parse, ssl, hashlib
from datetime import datetime


# ── Upstash Redis helpers ────────────────────────────────────────────────────
def _redis(method: str, *args):
    """Call Upstash Redis REST API."""
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


def update_key_fields(k, **fields):
    args = []
    for f, v in fields.items():
        args += [f, str(v)]
    _redis('HSET', f'key:{k}', *args)


def log_event(k, machine, ip, action):
    entry = json.dumps({'k': k, 'm': machine, 'ip': ip, 'a': action,
                        't': datetime.utcnow().strftime('%Y-%m-%d %H:%M')})
    _redis('LPUSH', 'log', entry)
    _redis('LTRIM', 'log', '0', '199')   # keep latest 200


def rate_ok(ip: str) -> bool:
    key = f'rate:{hashlib.md5(ip.encode()).hexdigest()}'
    count = _redis('INCR', key)
    if count == 1:
        _redis('EXPIRE', key, '3600')
    return count <= 15


# ── Vercel handler ───────────────────────────────────────────────────────────
class handler(BaseHTTPRequestHandler):

    def _json(self, data: dict, code: int = 200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        k       = params.get('k', [''])[0].strip()
        machine = params.get('m', [''])[0].strip()
        ip      = self.headers.get('x-forwarded-for', self.client_address[0])

        if len(k) < 4 or len(k) > 100:
            return self._json({'ok': False, 'msg': 'Invalid request'})

        if not rate_ok(ip):
            return self._json({'ok': False, 'msg': 'Too many attempts. Try again in 1 hour.'})

        try:
            entry = get_key(k)
        except Exception as e:
            return self._json({'ok': False, 'msg': f'Server error: {e}'})

        if not entry:
            log_event(k, machine, ip, 'invalid')
            return self._json({'ok': False, 'msg': 'Invalid license key.'})

        if entry.get('active', '1') == '0':
            log_event(k, machine, ip, 'disabled')
            return self._json({'ok': False, 'msg': 'License disabled. Contact admin.'})

        expiry = entry.get('expiry', '')
        if expiry and expiry < datetime.utcnow().strftime('%Y-%m-%d'):
            log_event(k, machine, ip, 'expired')
            return self._json({'ok': False, 'msg': f'License expired on {expiry}.'})

        # Valid — update stats
        update_key_fields(k,
            last_seen=datetime.utcnow().strftime('%Y-%m-%d %H:%M'),
            last_machine=machine,
            count=int(entry.get('count', 0)) + 1
        )
        log_event(k, machine, ip, 'ok')
        return self._json({'ok': True, 'name': entry.get('name', '')})

    def log_message(self, *args):
        pass  # suppress access log

