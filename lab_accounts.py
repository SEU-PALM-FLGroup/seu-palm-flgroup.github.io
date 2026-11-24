from contextlib import closing, contextmanager
from hashlib import scrypt
from hmac import compare_digest, digest
import secrets
import sqlite3


class Accounts:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (
                    name TEXT PRIMARY KEY,
                    value BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS students (
                    account_key BLOB PRIMARY KEY,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    source TEXT NOT NULL DEFAULT 'document' CHECK (source IN ('document', 'pending', 'manual'))
                );
                CREATE TABLE IF NOT EXISTS administrator (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    name TEXT NOT NULL,
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL
                );
            ''')
            columns = {column[1] for column in db.execute('PRAGMA table_info(students)')}
            if 'source' not in columns:
                db.execute("ALTER TABLE students ADD COLUMN source TEXT NOT NULL DEFAULT 'document' "
                           "CHECK (source IN ('document', 'pending', 'manual'))")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('member_key', ?)", (secrets.token_bytes(32),))
            self.member_key = db.execute("SELECT value FROM settings WHERE name = 'member_key'").fetchone()[0]

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            yield db

    def student_key(self, name, card_number):
        return digest(self.member_key, (name + '\0' + card_number).encode('utf-8'), 'sha256')

    def sync_roster(self, roster, documents=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = {
                name: (key, enabled, source)
                for key, name, enabled, source in db.execute('SELECT account_key, name, enabled, source FROM students')
            }
            students = []
            for name, number in roster.items():
                key, enabled, source = existing.get(name, (None, 0, 'pending'))
                if number is not None:
                    active = 1 if source == 'pending' else enabled
                    students.append((self.student_key(name, number), name, active, 'document'))
                elif source == 'manual':
                    students.append((key, name, enabled, 'manual'))
                else:
                    pending_key = digest(self.member_key, ('pending\0' + name).encode('utf-8'), 'sha256')
                    students.append((pending_key, name, 0, 'pending'))
            db.executemany('INSERT OR IGNORE INTO students VALUES (?, ?, ?, ?)', students)
            db.executemany('UPDATE students SET source = ? WHERE account_key = ?', [(source, key) for key, _, _, source in students])
            live_keys = {key for key, _, _, _ in students}
            obsolete = [(key,) for (key,) in db.execute('SELECT account_key FROM students') if key not in live_keys]
            db.executemany('DELETE FROM students WHERE account_key = ?', obsolete)
            if documents is not None:
                db.execute("INSERT INTO settings VALUES ('documents', ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                           (documents,))

    def documents(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE name = 'documents'").fetchone()
        return row[0] if row else None

    def student_enabled(self, account_key):
        with self.connect() as db:
            result = db.execute('SELECT enabled FROM students WHERE account_key = ?', (account_key,)).fetchone()
        return bool(result and result[0])

    def students(self):
        with self.connect() as db:
            return db.execute('SELECT account_key, name, enabled, source FROM students ORDER BY name').fetchall()

    def change_student_access(self, account_key, enabled):
        with self.connect() as db:
            return db.execute("UPDATE students SET enabled = ? WHERE account_key = ? AND source != 'pending'", (enabled, account_key)).rowcount == 1

    def fill_student_card(self, account_key, card_number):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT name FROM students WHERE account_key = ? AND source IN ('pending', 'manual')", (account_key,)).fetchone()
            if not row:
                return False
            key = self.student_key(row[0], card_number)
            return db.execute("UPDATE students SET account_key = ?, enabled = CASE WHEN source = 'pending' THEN 1 ELSE enabled END, "
                              "source = 'manual' WHERE account_key = ?",
                              (key, account_key)).rowcount == 1

    @staticmethod
    def password_hash(password, salt):
        return scrypt(password.encode('utf-8'), salt=salt, n=2**16, r=8, p=1, maxmem=96 * 1024 * 1024, dklen=32)

    def set_admin(self, name, password):
        salt = secrets.token_bytes(16)
        password_hash = self.password_hash(password, salt)
        with self.connect() as db:
            db.execute('INSERT INTO administrator VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET '
                       'name = excluded.name, salt = excluded.salt, password_hash = excluded.password_hash',
                       (name, salt, password_hash))

    def verify_admin(self, name, password):
        with self.connect() as db:
            account = db.execute('SELECT name, salt, password_hash FROM administrator WHERE id = 1').fetchone()
        if account is None or account[0] != name:
            return False
        _, salt, stored_hash = account
        return compare_digest(self.password_hash(password, salt), stored_hash)
