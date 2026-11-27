from contextlib import closing, contextmanager
from dataclasses import dataclass
from hashlib import sha256
import re
import sqlite3
from time import time


_COOKIE_TOKEN = re.compile(r'[A-Za-z0-9_-]{1,128}')
_MAX_EXPIRY = 2**63 - 1


@dataclass(frozen=True)
class Session:
    role: str
    student_key: bytes | None
    name: str
    watermark: str | None
    expires_at: int


def _token_hash(token):
    if not isinstance(token, str) or not _COOKIE_TOKEN.fullmatch(token):
        return None
    return sha256(token.encode('ascii')).digest()


def _is_student_key(key):
    return isinstance(key, bytes) and len(key) == 32


class SessionStore:
    def __init__(self, database_path):
        self.database_path = database_path
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS lab_sessions (
                    token_hash BLOB PRIMARY KEY NOT NULL CHECK (
                        typeof(token_hash) = 'blob' AND length(token_hash) = 32
                    ),
                    role TEXT NOT NULL CHECK (role IN ('student', 'admin')),
                    student_key BLOB,
                    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
                    watermark TEXT CHECK (watermark IS NULL OR length(watermark) <= 256),
                    expires_at INTEGER NOT NULL CHECK (
                        typeof(expires_at) = 'integer' AND expires_at > 0
                    ),
                    CHECK (
                        (role = 'admin' AND student_key IS NULL)
                        OR (role = 'student' AND typeof(student_key) = 'blob' AND length(student_key) = 32)
                    )
                );
                CREATE INDEX IF NOT EXISTS lab_sessions_expiry_idx ON lab_sessions(expires_at);
                CREATE INDEX IF NOT EXISTS lab_sessions_student_idx ON lab_sessions(student_key);
            ''')

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.database_path)) as db, db:
            yield db

    def enable_account_revocation(self):
        with self._connect() as db:
            db.executescript('''
                BEGIN;
                CREATE TRIGGER IF NOT EXISTS lab_sessions_live_student_insert
                    BEFORE INSERT ON lab_sessions
                    WHEN NEW.role = 'student' AND NOT EXISTS (
                        SELECT 1 FROM students
                        WHERE account_key = NEW.student_key AND name = NEW.name AND enabled = 1
                    )
                    BEGIN
                        SELECT RAISE(IGNORE);
                    END;
                CREATE TRIGGER IF NOT EXISTS lab_sessions_student_disable
                    AFTER UPDATE OF enabled ON students WHEN NEW.enabled = 0
                    BEGIN
                        DELETE FROM lab_sessions WHERE student_key = OLD.account_key;
                    END;
                CREATE TRIGGER IF NOT EXISTS lab_sessions_student_key_update
                    AFTER UPDATE OF account_key ON students
                    BEGIN
                        DELETE FROM lab_sessions WHERE student_key = OLD.account_key;
                    END;
                CREATE TRIGGER IF NOT EXISTS lab_sessions_student_delete
                    AFTER DELETE ON students
                    BEGIN
                        DELETE FROM lab_sessions WHERE student_key = OLD.account_key;
                    END;
                CREATE TRIGGER IF NOT EXISTS lab_sessions_admin_update
                    AFTER UPDATE ON administrator
                    BEGIN
                        DELETE FROM lab_sessions WHERE role = 'admin';
                    END;
                CREATE TRIGGER IF NOT EXISTS lab_sessions_admin_delete
                    AFTER DELETE ON administrator
                    BEGIN
                        DELETE FROM lab_sessions WHERE role = 'admin';
                    END;
                COMMIT;
            ''')

    def create(self, token, role, student_key, name, watermark, expires_at, *, admin_salt=None) -> Session | None:
        token_hash = _token_hash(token)
        if token_hash is None:
            raise ValueError('Invalid session token.')
        if not (
            (role == 'admin' and student_key is None)
            or (role == 'student' and _is_student_key(student_key))
        ):
            raise ValueError('Invalid session role or student key.')
        if admin_salt is not None and (role != 'admin' or not isinstance(admin_salt, bytes)):
            raise ValueError('Invalid administrator credential version.')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40 or '\x00' in name:
            raise ValueError('Invalid session name.')
        if watermark is not None and (
            not isinstance(watermark, str) or len(watermark) > 256 or '\x00' in watermark
        ):
            raise ValueError('Invalid session watermark.')
        now = int(time())
        if type(expires_at) is not int or not now < expires_at <= _MAX_EXPIRY:
            raise ValueError('Invalid session expiration.')

        name = name.strip()
        insert = '''
            INSERT INTO lab_sessions (token_hash, role, student_key, name, watermark, expires_at)
            SELECT ?, ?, ?, ?, ?, ?
        '''
        values = (token_hash, role, student_key, name, watermark, expires_at)
        if admin_salt is not None:
            insert += 'WHERE EXISTS (SELECT 1 FROM administrator WHERE id = 1 AND name = ? AND salt = ?)'
            values += (name, admin_salt)

        with self._connect() as db:
            db.execute('DELETE FROM lab_sessions WHERE expires_at <= ?', (now,))
            if not db.execute(insert, values).rowcount:
                return None
        return Session(role, student_key, name, watermark, expires_at)

    def get(self, token) -> Session | None:
        token_hash = _token_hash(token)
        if token_hash is None:
            return None
        with self._connect() as db:
            row = db.execute('''
                SELECT role, student_key, name, watermark, expires_at
                FROM lab_sessions
                WHERE token_hash = ? AND expires_at > ?
            ''', (token_hash, int(time()))).fetchone()
        return Session(*row) if row is not None else None

    def delete(self, token) -> bool:
        token_hash = _token_hash(token)
        if token_hash is None:
            return False
        with self._connect() as db:
            return db.execute('DELETE FROM lab_sessions WHERE token_hash = ?', (token_hash,)).rowcount == 1
