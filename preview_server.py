import argparse
from collections import deque
from contextlib import closing
from dataclasses import dataclass
from getpass import getpass
from html import escape
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
import re
import secrets
from threading import Lock
from time import monotonic
from urllib.parse import parse_qs, urlsplit
from wsgiref.simple_server import WSGIRequestHandler, make_server

from lab_accounts import Accounts


ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / '.lab-data'
COOKIE = 'lab_preview_session'
SESSION_SECONDS = 30 * 60
LOGIN_PAGE = '/#login'
MAX_FORM_BYTES = 4096
TEAM_NAME = 'Secure & Trustworthy Agents (STAGE)'
TEAM_NAME_HTML = '<strong>S</strong>ecure &amp; <strong>T</strong>rustworthy <strong>Age</strong>nts (STAGE)'
LOGOUT_FORM = ('<form method="post" action="/logout">'
               '<button class="logout-button" type="submit">退出登录</button>'
               '</form>').encode('utf-8')
ADMIN_LINK = '<a class="logout-button admin-link" href="/admin">管理账号</a>'.encode('utf-8')


def replace_login_section(site, section):
    start = site.index(b'<!-- LAB_LOGIN_START -->')
    end_marker = b'<!-- LAB_LOGIN_END -->'
    end = site.index(end_marker, start) + len(end_marker)
    return site[:start] + section + site[end:]


def authenticated_homepage(site, documents, role, watermark=None, credit_progress=b''):
    site = replace_login_section(site, b'')
    controls = ADMIN_LINK + LOGOUT_FORM if role == 'admin' else LOGOUT_FORM
    resources_link = '<a class="lab-nav-link" href="#lab-resources">组内资料</a>'
    if credit_progress:
        resources_link += '\n        <a href="#service-credit-progress">积分进度</a>'
    overrides = (
        ('<!-- LAB_HEAD -->', '<meta name="robots" content="noindex, nofollow">\n  <script src="search.js" defer></script>'),
        ('<a class="lab-nav-link" href="#login">组内登录</a>', resources_link),
        ('</title>', ' · 组内资料</title>'),
        ('<template id="lab-resources">', '<div id="lab-resources">'),
        ('</template><!-- LAB_RESOURCES_END -->', '</div>'),
    )
    for placeholder, content in overrides:
        site = site.replace(placeholder.encode('utf-8'), content.encode('utf-8'), 1)
    site = site.replace(b'<!-- AUTH_FORM -->', b'<div class="account-controls">' + controls + b'</div>', 1)
    if watermark:
        labels = f'<span>{escape(watermark)}</span>' * 30
        layer = f'<div class="member-watermark" aria-hidden="true">{labels}</div>\n'.encode('utf-8')
        site = site.replace(b'</body>', layer + b'</body>', 1)
    team_name = f'<span class="team-name" lang="en">{TEAM_NAME_HTML}</span>'.encode('utf-8')
    former_name = '冯磊课题组'.encode('utf-8')
    documents = re.sub(rb'(?:SEU-PALM )?' + former_name, team_name, documents)
    return site.replace(b'<!-- LAB_DOCUMENTS -->', credit_progress + documents, 1)


@dataclass(frozen=True)
class Session:
    role: str
    student_key: bytes | None
    expires_at: float
    watermark: str | None = None


def login_section(notice):
    return f'''<section class="login-section login-section-active wrap" id="login" aria-labelledby="login-title">
    <div class="login-context">
      <h2 id="login-title">组内登录</h2>
      <p>组员使用登记的姓名和一卡通号，管理员使用姓名和管理密码。</p>
    </div>
    <form method="post" action="/login" class="login-form">
      <label for="name">姓名</label>
      <input id="name" name="name" autocomplete="username" maxlength="40" required>
      <label for="credential">密码</label>
      <input id="credential" name="credential" type="password" autocomplete="current-password" maxlength="128" required>
      <button type="submit">登录</button>
      <p class="login-notice" role="status">{escape(notice)}</p>
    </form>
  </section>'''.encode('utf-8')


def admin_page(students):
    rows = []
    for key, name, enabled, source in students:
        safe_name = escape(name)
        controls = []
        if source in {'pending', 'manual'}:
            card_action = '补填' if source == 'pending' else '更换'
            controls.append(f'''<form class="admin-card-form" action="/admin/students" method="post">
              <input type="hidden" name="account" value="{key.hex()}">
              <input type="password" name="card" aria-label="{card_action}{safe_name}的一卡通号" placeholder="一卡通号" inputmode="numeric" autocomplete="off" pattern="[0-9]+" maxlength="24" required>
              <button class="logout-button" type="submit">{card_action}</button>
            </form>''')
        if source == 'pending':
            state = '待补充一卡通号'
        else:
            state = '已启用' if enabled else '已停用'
            action = '停用' if enabled else '启用'
            controls.append(f'''<form action="/admin/students" method="post">
              <input type="hidden" name="account" value="{key.hex()}">
              <button class="logout-button" type="submit" name="enabled" value="{int(not enabled)}" aria-label="{action}{safe_name}的账号">{action}</button>
            </form>''')
        rows.append(f'<tr><th scope="row">{safe_name}</th><td>{state}</td><td>{"".join(controls)}</td></tr>')
    active_count = sum(enabled for _, _, enabled, _ in students)
    pending_count = sum(source == 'pending' for _, _, _, source in students)
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>组员账号管理 · {escape(TEAM_NAME)}</title>
  <link rel="stylesheet" href="/site.css">
</head>
<body>
  <header class="site-header">
    <div class="wrap header-inner">
      <a href="/" class="brand"><span class="brand-mark" aria-hidden="true"></span><span class="brand-name team-name" lang="en">{TEAM_NAME_HTML}</span></a>
      {LOGOUT_FORM.decode('utf-8')}
    </div>
  </header>
  <main class="wrap document admin-panel">
    <header class="document-head">
      <h1>组员账号管理</h1>
      <p class="document-summary">已登记 {len(students)} 位组员，{active_count} 位已启用，{pending_count} 位待补充一卡通号。</p>
    </header>
    <div class="table-wrap" tabindex="0" role="region" aria-label="组员账号">
      <table aria-label="组员账号">
        <thead><tr><th scope="col">姓名</th><th scope="col">登录状态</th><th scope="col">操作</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
  </main>
</body>
</html>'''.encode('utf-8')



class LabSite:
    def __init__(self, accounts, documents, origin, credit_progress=b''):
        self.accounts = accounts
        self.documents = documents
        self.credit_progress = credit_progress
        self.origin = origin
        self.host = urlsplit(origin).netloc
        self.secure = origin.startswith('https://')
        self.sessions = {}
        self.attempts = {}
        self.purge_attempts_at = 0
        self.state_lock = Lock()

    def __call__(self, environ, start_response):
        status, content, headers = self.dispatch(environ)
        headers.append(('Content-Length', str(len(content))))
        if self.secure:
            headers.append(('Strict-Transport-Security', 'max-age=31536000'))
        start_response(f'{status.value} {status.phrase}', headers)
        return [b'' if environ['REQUEST_METHOD'] == 'HEAD' else content]

    def answer(self, status, content=b'', content_type='text/html', redirect=None, cookie=None, extra=()):
        headers = [
            ('Content-Type', f'{content_type}; charset=utf-8'),
            ('Cache-Control', 'no-store'),
            ('X-Content-Type-Options', 'nosniff'),
            ('Referrer-Policy', 'same-origin'),
            ('Content-Security-Policy', "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
            *extra,
        ]
        if redirect:
            headers.append(('Location', redirect))
        if cookie is not None:
            max_age = SESSION_SECONDS if cookie else 0
            secure = '; Secure' if self.secure else ''
            headers.append(('Set-Cookie', f'{COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}{secure}'))
        return status, content, headers

    @staticmethod
    def token(environ):
        jar = SimpleCookie()
        try:
            jar.load(environ.get('HTTP_COOKIE', ''))
        except CookieError:
            return ''
        return jar[COOKIE].value if COOKIE in jar else ''

    def current_session(self, environ):
        token = self.token(environ)
        with self.state_lock:
            session = self.sessions.get(token)
            if not session:
                return None
            if session.expires_at <= monotonic() or (
                session.role == 'student' and not self.accounts.student_enabled(session.student_key)
            ):
                self.sessions.pop(token, None)
                return None
            return session

    def homepage(self, session=None, status=HTTPStatus.OK, notice='', extra=()):
        site = (ROOT / 'index.html').read_bytes()
        if session:
            site = authenticated_homepage(
                site, self.documents, session.role, session.watermark, self.credit_progress
            )
        else:
            site = replace_login_section(site, login_section(notice))
        return self.answer(status, site, extra=extra)

    def dispatch(self, environ):
        if environ.get('HTTP_HOST', '').lower() != self.host or (self.secure and environ['wsgi.url_scheme'] != 'https'):
            return self.answer(HTTPStatus.BAD_REQUEST)
        method = environ['REQUEST_METHOD']
        if method in {'GET', 'HEAD'}:
            return self.get(environ)
        if method == 'POST':
            return self.post(environ)
        return self.answer(HTTPStatus.METHOD_NOT_ALLOWED, extra=(('Allow', 'GET, HEAD, POST'),))

    def get(self, environ):
        path = environ['PATH_INFO']
        if path == '/site.css':
            return self.answer(HTTPStatus.OK, (ROOT / 'site.css').read_bytes(), 'text/css')
        if path == '/index.html':
            return self.answer(HTTPStatus.SEE_OTHER, redirect='/')
        if path in {'/login', '/admin/login'}:
            return self.answer(HTTPStatus.SEE_OTHER, redirect=LOGIN_PAGE)
        if path not in {'/', '/search.js', '/admin'}:
            return self.answer(HTTPStatus.NOT_FOUND)
        session = self.current_session(environ)
        if path == '/':
            return self.homepage(session)
        if not session:
            return self.answer(HTTPStatus.SEE_OTHER, redirect=LOGIN_PAGE)
        if path == '/admin':
            if session.role != 'admin':
                return self.answer(HTTPStatus.FORBIDDEN)
            return self.answer(HTTPStatus.OK, admin_page(self.accounts.students()))
        return self.answer(HTTPStatus.OK, (ROOT / 'search.js').read_bytes(), 'text/javascript')

    def post(self, environ):
        if environ.get('HTTP_ORIGIN') != self.origin:
            return self.answer(HTTPStatus.FORBIDDEN)
        path = environ['PATH_INFO']
        if path == '/logout':
            with self.state_lock:
                self.sessions.pop(self.token(environ), None)
            return self.answer(HTTPStatus.SEE_OTHER, redirect=LOGIN_PAGE, cookie='')
        if path not in {'/login', '/admin/students'}:
            return self.answer(HTTPStatus.NOT_FOUND)
        size = environ.get('CONTENT_LENGTH') or ''
        if not re.fullmatch(r'[0-9]{1,4}', size) or not 0 < int(size) <= MAX_FORM_BYTES:
            return self.answer(HTTPStatus.BAD_REQUEST)
        content_type = environ.get('CONTENT_TYPE', '').partition(';')[0].strip().lower()
        if content_type != 'application/x-www-form-urlencoded':
            return self.answer(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        try:
            body = environ['wsgi.input'].read(int(size)).decode('ascii')
            values = parse_qs(body, max_num_fields=4, keep_blank_values=True, errors='strict')
            if any(len(items) != 1 for items in values.values()):
                return self.answer(HTTPStatus.BAD_REQUEST)
            fields = {name: items[0] for name, items in values.items()}
        except (ValueError, UnicodeError):
            return self.answer(HTTPStatus.BAD_REQUEST)
        if path == '/admin/students':
            return self.change_student_access(environ, fields)
        return self.login(environ, fields)

    def change_student_access(self, environ, fields):
        session = self.current_session(environ)
        if not session or session.role != 'admin':
            return self.answer(HTTPStatus.FORBIDDEN)
        account = fields.get('account', '')
        enabled = fields.get('enabled', '')
        number = fields.get('card', '').strip()
        if not re.fullmatch(r'[a-f0-9]{64}', account):
            return self.answer(HTTPStatus.BAD_REQUEST)
        key = bytes.fromhex(account)
        with self.state_lock:
            if 'card' in fields and 'enabled' not in fields and re.fullmatch(r'[0-9]{1,24}', number):
                changed = self.accounts.fill_student_card(key, number)
                revoke = True
            elif 'card' not in fields and enabled in {'0', '1'}:
                changed = self.accounts.change_student_access(key, int(enabled))
                revoke = enabled == '0'
            else:
                return self.answer(HTTPStatus.BAD_REQUEST)
            if changed and revoke:
                obsolete = [token for token, session in self.sessions.items() if session.student_key == key]
                for token in obsolete:
                    del self.sessions[token]
        if not changed:
            return self.answer(HTTPStatus.NOT_FOUND)
        return self.answer(HTTPStatus.SEE_OTHER, redirect='/admin')

    def allow_login(self, ip, name):
        now = monotonic()
        with self.state_lock:
            if now >= self.purge_attempts_at:
                self.attempts = {
                    key: queue for key, queue in self.attempts.items()
                    if queue and queue[-1] > now - key[2]
                }
                self.purge_attempts_at = now + 60

            queues = []
            for scope, value, window, limit in (
                ('site', '', 60, 240),
                ('ip', ip, 60, 12),
                ('name', name, 300, 12),
            ):
                key = (scope, value, window)
                queue = self.attempts.get(key)
                if queue is None:
                    queue = deque()
                while queue and queue[0] <= now - window:
                    queue.popleft()
                if len(queue) >= limit:
                    return False, max(1, int(queue[0] + window - now) + 1)
                queues.append((key, queue))
            for key, queue in queues:
                self.attempts[key] = queue
                queue.append(now)
        return True, 0

    def login(self, environ, fields):
        name = fields.get('name', '').strip()
        credential = fields.get('credential', '')
        allowed, retry_after = self.allow_login(environ.get('REMOTE_ADDR', ''), name[:40])
        if not allowed:
            return self.homepage(
                status=HTTPStatus.TOO_MANY_REQUESTS,
                notice='尝试次数过多，请稍后再试。',
                extra=(('Retry-After', str(retry_after)),),
            )

        role = None
        student_key = None
        watermark = None
        card_number = credential.strip()
        if 0 < len(name) <= 40 and 0 < len(credential) <= 128:
            if self.accounts.verify_admin(name, credential):
                role = 'admin'
            elif re.fullmatch(r'[0-9]{1,24}', card_number):
                student_key = self.accounts.student_key(name, card_number)

        with self.state_lock:
            if student_key is not None and self.accounts.student_enabled(student_key):
                role = 'student'
                watermark = name
                if len(card_number) >= 7:
                    watermark += f' · 一卡通尾号 {card_number[-2:]}'
            if role is None:
                return self.homepage(status=HTTPStatus.UNAUTHORIZED, notice='姓名或登录凭据不匹配。')
            now = monotonic()
            expired = [token for token, session in self.sessions.items() if session.expires_at <= now]
            for token in expired:
                del self.sessions[token]
            self.sessions.pop(self.token(environ), None)
            token = secrets.token_urlsafe(32)
            self.sessions[token] = Session(role, student_key, now + SESSION_SECONDS, watermark)
        return self.answer(HTTPStatus.SEE_OTHER, redirect='/admin' if role == 'admin' else '/', cookie=token)


class LocalRequestHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def public_origin(value):
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError as error:
        raise argparse.ArgumentTypeError('Expected an HTTPS origin, such as https://lab.example.edu.') from error
    if (url.scheme != 'https' or not re.fullmatch(r'[a-z0-9.-]+(?::[0-9]+)?', url.netloc, re.IGNORECASE)
            or url.path not in {'', '/'} or url.query or url.fragment or port == 0):
        raise argparse.ArgumentTypeError('Expected an HTTPS origin, such as https://lab.example.edu.')
    suffix = f':{port}' if port not in {None, 443} else ''
    return f'https://{url.hostname}{suffix}'


def main():
    cli = argparse.ArgumentParser(description='Serve the lab site locally or behind an HTTPS reverse proxy.')
    cli.add_argument('--port', default=8765, type=int)
    cli.add_argument('--data-dir', default=DEFAULT_DATA, type=Path, help='Private directory containing the account database')
    cli.add_argument('--public-origin', type=public_origin, help='HTTPS origin used by a local reverse proxy')
    cli.add_argument('--set-admin', metavar='NAME')
    args = cli.parse_args()
    if not 0 <= args.port <= 65535 or (args.public_origin and not args.port):
        cli.error('Choose an available TCP port.')
    data_dir = args.data_dir.resolve()
    if data_dir.is_relative_to(ROOT) and not data_dir.is_relative_to(DEFAULT_DATA):
        cli.error('--data-dir must be inside .lab-data or outside this repository.')
    database = data_dir / 'lab_accounts.sqlite3'
    if args.set_admin:
        name = args.set_admin.strip()
        if not 0 < len(name) <= 40:
            cli.error('Administrator name must contain 1–40 characters.')
        password = getpass('Administrator password: ')
        if not 12 <= len(password) <= 128 or not password.strip() or password.strip().isdecimal():
            cli.error('Use 12–128 characters and a password other than a numeric card number.')
        if password != getpass('Confirm administrator password: '):
            cli.error('The passwords do not match.')
        data_dir.mkdir(parents=True, exist_ok=True)
        Accounts(database).set_admin(name, password)
        print('Administrator configured. Restart the site to end existing sessions.', flush=True)
        return

    if not database.is_file():
        cli.error('Run build_lab_site.py to import the private documents and roster first.')
    accounts = Accounts(database)
    documents = accounts.documents()
    if not documents:
        cli.error('Run build_lab_site.py to rebuild the private documents from the Word files.')
    progress_file = data_dir / 'service-credit-progress.html'
    credit_progress = progress_file.read_bytes() if progress_file.is_file() else b''
    if args.public_origin:
        try:
            from waitress import serve
        except ImportError:
            cli.error('Install the dependencies with python -m pip install -r requirements.txt.')
        print(f'Lab site: {args.public_origin}', flush=True)
        serve(
            LabSite(accounts, documents, args.public_origin, credit_progress),
            host='127.0.0.1',
            port=args.port,
            trusted_proxy='127.0.0.1',
            trusted_proxy_headers={'x-forwarded-for', 'x-forwarded-proto'},
            max_request_header_size=16 * 1024,
            max_request_body_size=MAX_FORM_BYTES,
        )
        return

    with closing(make_server('127.0.0.1', args.port, None, handler_class=LocalRequestHandler)) as server:
        origin = f'http://127.0.0.1:{server.server_port}'
        server.set_app(LabSite(accounts, documents, origin, credit_progress))
        print(f'Local site: {origin}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
