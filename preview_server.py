import argparse
from collections import deque
from dataclasses import dataclass
from getpass import getpass
from html import escape
from html.parser import HTMLParser
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
from threading import Lock
from time import monotonic
from urllib.parse import parse_qs, urlsplit

from lab_accounts import Accounts


ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / '.lab-data'
COOKIE = 'lab_preview_session'
SESSION_SECONDS = 30 * 60
SESSIONS = {}
LOGIN_ROLES = {'/login': 'student', '/admin/login': 'admin'}
LOGIN_PAGES = {'student': '/#login', 'admin': '/?role=admin#login'}
FAILED_ATTEMPTS = {role: deque() for role in LOGIN_ROLES.values()}
STATE_LOCK = Lock()
LOGOUT_FORM = ('<form method="post" action="/logout">'
               '<button class="logout-button" type="submit">退出登录</button>'
               '</form>').encode('utf-8')
ADMIN_LINK = '<a class="logout-button admin-link" href="/admin">管理账号</a>'.encode('utf-8')


def replace_login_section(site, section):
    start = site.index(b'<!-- LAB_LOGIN_START -->')
    end_marker = b'<!-- LAB_LOGIN_END -->'
    end = site.index(end_marker, start) + len(end_marker)
    return site[:start] + section + site[end:]


def authenticated_homepage(site, documents, role):
    site = replace_login_section(site, b'')
    controls = ADMIN_LINK + LOGOUT_FORM if role == 'admin' else LOGOUT_FORM
    overrides = (
        ('<!-- LAB_HEAD -->', '<meta name="robots" content="noindex, nofollow">\n  <script src="search.js" defer></script>'),
        ('<a class="lab-nav-link" href="#login">组内登录</a>', '<a class="lab-nav-link" href="#lab-resources">组内资料</a>'),
        ('<title>冯磊课题组</title>', '<title>冯磊课题组 · 组内资料</title>'),
        ('<template id="lab-resources">', '<div id="lab-resources">'),
        ('</template><!-- LAB_RESOURCES_END -->', '</div>'),
    )
    for placeholder, content in overrides:
        site = site.replace(placeholder.encode('utf-8'), content.encode('utf-8'), 1)
    site = site.replace(b'<!-- AUTH_FORM -->', b'<div class="account-controls">' + controls + b'</div>', 1)
    return site.replace(b'<!-- LAB_DOCUMENTS -->', documents, 1)


@dataclass(frozen=True)
class Session:
    role: str
    student_key: bytes | None
    expires_at: float


class RosterReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.roster_tables = 0
        self.collecting = False
        self.in_body = False
        self.cell = None
        self.row = []
        self.members = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'table' and dict(attrs).get('class') == 'members-table':
            self.roster_tables += 1
            self.collecting = True
        if self.collecting and tag == 'tbody':
            self.in_body = True
        if self.collecting and tag == 'tr':
            self.row = []
        if self.collecting and tag in {'td', 'th'}:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if self.collecting and tag in {'td', 'th'}:
            self.row.append(''.join(self.cell).strip())
            self.cell = None
        if self.collecting and self.in_body and tag == 'tr' and len(self.row) >= 2:
            number, name = self.row[:2]
            if not name or name in self.members:
                raise ValueError('The member roster contains a missing or duplicate name.')
            self.members[name] = number if re.fullmatch(r'[0-9]{1,24}', number) else None
        if tag == 'tbody':
            self.in_body = False
        if tag == 'table':
            self.collecting = False


def login_section(role, notice):
    admin = role == 'admin'
    title = '管理员登录' if admin else '组员登录'
    description = '登录后可管理组员账号和查阅组内资料。' if admin else '使用登记的姓名和一卡通号访问组内资料。'
    alternative = 'student' if admin else 'admin'
    alternative_title = '切换到组员登录' if admin else '切换到管理员登录'
    field = (
        '<label for="password">密码</label>'
        '<input id="password" name="password" type="password" autocomplete="current-password" maxlength="128" required>'
        if admin else
        '<label for="card">一卡通号</label>'
        '<input id="card" name="card" type="password" inputmode="numeric" autocomplete="current-password" pattern="[0-9]+" maxlength="24" required>'
    )
    return f'''<section class="login-section login-section-active wrap" id="login" aria-labelledby="login-title">
    <div class="login-context">
      <h2 id="login-title">{title}</h2>
      <p>{description}</p>
      <a class="login-alternative" href="{LOGIN_PAGES[alternative]}">{alternative_title}</a>
    </div>
    <form method="post" action="{LOGIN_PAGES[role]}" class="login-form">
      <label for="name">姓名</label>
      <input id="name" name="name" autocomplete="username" maxlength="40" required>
      {field}
      <button type="submit">登录</button>
      <p class="login-notice" role="status">{notice}</p>
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
  <title>组员账号管理 · 冯磊课题组</title>
  <link rel="stylesheet" href="/site.css">
</head>
<body>
  <header class="site-header">
    <div class="wrap header-inner">
      <a href="/" class="brand"><span class="brand-mark" aria-hidden="true"></span><span class="brand-name">冯磊课题组</span></a>
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


class PreviewHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def answer(self, status, content=b'', content_type='text/html', redirect=None, cookie=None):
        self.send_response(status)
        self.send_header('Content-Type', f'{content_type}; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if redirect:
            self.send_header('Location', redirect)
        if cookie is not None:
            max_age = SESSION_SECONDS if cookie else 0
            self.send_header('Set-Cookie', f'{COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(content)

    def safe_host(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

    def token(self):
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie', ''))
        except CookieError:
            return ''
        return jar[COOKIE].value if COOKIE in jar else ''

    def current_session(self):
        token = self.token()
        with STATE_LOCK:
            session = SESSIONS.get(token)
            if not session:
                return None
            if session.expires_at <= monotonic() or (
                session.role == 'student' and not self.server.accounts.student_enabled(session.student_key)
            ):
                SESSIONS.pop(token, None)
                return None
            return session

    def homepage(self, session=None, role='student', status=HTTPStatus.OK, notice=''):
        site = (ROOT / 'index.html').read_bytes()
        if session:
            site = authenticated_homepage(site, self.server.documents, session.role)
        else:
            site = replace_login_section(site, login_section(role, notice))
        return self.answer(status, site)

    def do_GET(self):
        if not self.safe_host():
            return self.answer(HTTPStatus.BAD_REQUEST)
        url = urlsplit(self.path)
        path = url.path
        if path == '/site.css':
            return self.answer(HTTPStatus.OK, (ROOT / 'site.css').read_bytes(), 'text/css')
        if path == '/index.html':
            return self.answer(HTTPStatus.SEE_OTHER, redirect='/')
        if path not in {'/', '/search.js', '/admin', *LOGIN_ROLES}:
            return self.answer(HTTPStatus.NOT_FOUND)
        session = self.current_session()
        if path in LOGIN_ROLES:
            role = LOGIN_ROLES[path]
            if session and session.role == role:
                destination = '/admin' if role == 'admin' else '/'
                return self.answer(HTTPStatus.SEE_OTHER, redirect=destination)
            return self.answer(HTTPStatus.SEE_OTHER, redirect=LOGIN_PAGES[role])
        if path == '/admin':
            if not session:
                return self.answer(HTTPStatus.SEE_OTHER, redirect=LOGIN_PAGES['admin'])
            if session.role != 'admin':
                return self.answer(HTTPStatus.FORBIDDEN)
            return self.answer(HTTPStatus.OK, admin_page(self.server.accounts.students()))
        if path == '/':
            if url.query == 'role=admin':
                if session and session.role == 'admin':
                    return self.answer(HTTPStatus.SEE_OTHER, redirect='/admin')
                return self.homepage(role='admin')
            return self.homepage(session)
        if not session:
            return self.answer(HTTPStatus.SEE_OTHER, redirect=LOGIN_PAGES['student'])
        return self.answer(HTTPStatus.OK, (ROOT / 'search.js').read_bytes(), 'text/javascript')

    do_HEAD = do_GET

    def do_POST(self):
        origin = f'http://127.0.0.1:{self.server.server_port}'
        if not self.safe_host() or self.headers.get('Origin') != origin:
            return self.answer(HTTPStatus.FORBIDDEN)
        url = urlsplit(self.path)
        path = url.path
        if path == '/logout':
            with STATE_LOCK:
                SESSIONS.pop(self.token(), None)
            return self.answer(HTTPStatus.SEE_OTHER, redirect=LOGIN_PAGES['student'], cookie='')
        if path not in {'/', '/admin/students', *LOGIN_ROLES}:
            return self.answer(HTTPStatus.NOT_FOUND)
        size = self.headers.get('Content-Length', '')
        if not size.isascii() or not size.isdecimal() or len(size) > 4 or not 0 < int(size) <= 1024:
            return self.answer(HTTPStatus.BAD_REQUEST)
        if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
            return self.answer(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        try:
            fields = parse_qs(self.rfile.read(int(size)).decode('ascii'), max_num_fields=4, errors='strict')
        except (ValueError, UnicodeError):
            return self.answer(HTTPStatus.BAD_REQUEST)
        if path == '/admin/students':
            return self.change_student_access(fields)
        if path == '/':
            return self.login('admin' if url.query == 'role=admin' else 'student', fields)
        return self.login(LOGIN_ROLES[path], fields)

    def change_student_access(self, fields):
        session = self.current_session()
        if not session or session.role != 'admin':
            return self.answer(HTTPStatus.FORBIDDEN)
        account = fields.get('account', [''])[0]
        enabled = fields.get('enabled', [''])[0]
        number = fields.get('card', [''])[0].strip()
        if not re.fullmatch(r'[a-f0-9]{64}', account):
            return self.answer(HTTPStatus.BAD_REQUEST)
        key = bytes.fromhex(account)
        with STATE_LOCK:
            if 'card' in fields and 'enabled' not in fields and re.fullmatch(r'[0-9]{1,24}', number):
                changed = self.server.accounts.fill_student_card(key, number)
                revoke = True
            elif 'card' not in fields and enabled in {'0', '1'}:
                changed = self.server.accounts.change_student_access(key, int(enabled))
                revoke = enabled == '0'
            else:
                changed = None
                revoke = False
            if changed and revoke:
                for token in [token for token, session in SESSIONS.items() if session.student_key == key]:
                    del SESSIONS[token]
        if changed is None:
            return self.answer(HTTPStatus.BAD_REQUEST)
        if not changed:
            return self.answer(HTTPStatus.NOT_FOUND)
        return self.answer(HTTPStatus.SEE_OTHER, redirect='/admin')

    def login(self, role, fields):
        name = fields.get('name', [''])[0].strip()
        attempts = FAILED_ATTEMPTS[role]
        status = HTTPStatus.SEE_OTHER
        notice = ''
        token = None
        student_key = None
        with STATE_LOCK:
            now = monotonic()
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if len(attempts) >= 8:
                status, notice = HTTPStatus.TOO_MANY_REQUESTS, '尝试次数过多，请稍后再试。'
            else:
                if role == 'admin':
                    password = fields.get('password', [''])[0]
                    valid = 0 < len(name) <= 40 and 0 < len(password) <= 128 and self.server.accounts.verify_admin(name, password)
                    failure = '姓名或密码不匹配。'
                else:
                    number = fields.get('card', [''])[0].strip()
                    if 0 < len(name) <= 40 and re.fullmatch(r'[0-9]{1,24}', number):
                        student_key = self.server.accounts.student_key(name, number)
                    valid = student_key is not None and self.server.accounts.student_enabled(student_key)
                    failure = '姓名或一卡通号不匹配。'
                if not valid:
                    attempts.append(now)
                    status, notice = HTTPStatus.UNAUTHORIZED, failure
                else:
                    for old_token in [key for key, session in SESSIONS.items() if session.expires_at <= now]:
                        del SESSIONS[old_token]
                    SESSIONS.pop(self.token(), None)
                    token = secrets.token_urlsafe(32)
                    SESSIONS[token] = Session(role, student_key, now + SESSION_SECONDS)
        if notice:
            return self.homepage(role=role, status=status, notice=notice)
        return self.answer(status, redirect='/admin' if role == 'admin' else '/', cookie=token)


def main():
    cli = argparse.ArgumentParser(description='Serve the lab website preview on this computer only.')
    cli.add_argument('--port', default=8765, type=int)
    cli.add_argument('--data-dir', default=DEFAULT_DATA, type=Path, help='Directory containing the generated documents and local account database')
    cli.add_argument('--set-admin', metavar='NAME')
    args = cli.parse_args()
    data_dir = args.data_dir.resolve()
    if data_dir.is_relative_to(ROOT) and not data_dir.is_relative_to(DEFAULT_DATA):
        cli.error('--data-dir must be inside .lab-data or outside this repository.')
    if args.set_admin:
        name = args.set_admin.strip()
        if not 0 < len(name) <= 40:
            cli.error('Administrator name must contain 1–40 characters.')
        password = getpass('Administrator password: ')
        if not 6 <= len(password) <= 128:
            cli.error('Administrator password must contain 6–128 characters.')
        data_dir.mkdir(parents=True, exist_ok=True)
        accounts = Accounts(data_dir / 'lab_accounts.sqlite3')
        accounts.set_admin(name, password)
        if not accounts.verify_admin(name, password):
            cli.error('Administrator verification failed.')
        print('Administrator configured and verified.', flush=True)
        return
    documents_file = data_dir / 'documents.html'
    if not documents_file.is_file():
        cli.error('Run build_lab_site.py to generate documents.html first.')
    documents = documents_file.read_bytes()
    roster = RosterReader()
    try:
        roster.feed(documents.decode('utf-8'))
    except ValueError as error:
        cli.error(str(error))
    if roster.roster_tables != 2 or not roster.members:
        cli.error('Could not load the member list from the local website.')
    accounts = Accounts(data_dir / 'lab_accounts.sqlite3')
    accounts.sync_roster(roster.members)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), PreviewHandler)
    server.accounts = accounts
    server.documents = documents
    print(f'Local site: http://127.0.0.1:{server.server_port}/ ({len(roster.members)} enrolled)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
