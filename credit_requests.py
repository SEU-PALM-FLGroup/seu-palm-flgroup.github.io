from collections.abc import Iterable
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Context, Decimal, localcontext
from itertools import groupby
import re
import sqlite3


MAX_BATCH_ITEMS = 42
_MAX_COEFFICIENT_CHARS = 16 * 1024
COEFFICIENT_PATTERN = r'[+]?(?:[0-9]+(?:[.][0-9]*)?|[.][0-9]+)'
_COEFFICIENT_INPUT = re.compile(COEFFICIENT_PATTERN)
_DECIMAL_INPUT = re.compile(r'[+]?(?:[0-9]+(?:\.[0-9]{0,4})?|\.[0-9]{1,4})')
_REQUEST_TOKEN = re.compile(r'[A-Za-z0-9_-]{1,128}')
_ITEM_KEY = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
_ACTIVITY_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
_POINTS_PLACES = 4
_MAX_POINTS = Decimal('1000000')
_MAX_REQUEST_ID = 2**63 - 1
_REQUEST_COLUMNS = (
    'id, member_name, requested_units, reason, submitted_at, '
    'coefficient_text, review_reason, reviewed_by, reviewed_at, item_key, item_label, start_date, end_date'
)
_OPTIONAL_COLUMNS = ('item_key', 'item_label', 'batch_token', 'start_date', 'end_date', 'deleted_at', 'deleted_by')
_TABLE_SCHEMA = '''
    CREATE TABLE {table_name} (
        id INTEGER PRIMARY KEY,
        member_name TEXT NOT NULL,
        requested_units INTEGER NOT NULL CHECK (
            typeof(requested_units) = 'integer' AND requested_units BETWEEN 1 AND 10000000000
        ),
        reason TEXT NOT NULL,
        request_token TEXT NOT NULL,
        submitted_at TEXT NOT NULL,
        coefficient_text TEXT,
        review_reason TEXT,
        reviewed_by TEXT,
        reviewed_at TEXT,
        item_key TEXT,
        item_label TEXT,
        batch_token TEXT,
        start_date TEXT,
        end_date TEXT,
        deleted_at TEXT,
        deleted_by TEXT CHECK ((deleted_at IS NULL) = (deleted_by IS NULL)),
        UNIQUE (member_name, request_token),
        CHECK (
            (coefficient_text IS NULL AND review_reason IS NULL AND reviewed_by IS NULL AND reviewed_at IS NULL)
            OR (coefficient_text IS NOT NULL AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
        )
    )
'''


@dataclass(frozen=True)
class CreditRequest:
    id: int
    member_name: str
    requested_points: Decimal
    reason: str
    submitted_at: str
    coefficient: Decimal | None
    review_reason: str | None
    reviewed_by: str | None
    reviewed_at: str | None
    item_key: str | None = None
    item_label: str | None = None
    start_date: str | None = None
    end_date: str | None = None

    @property
    def approved_points(self) -> Decimal | None:
        if self.coefficient is None:
            return None
        return _product_points(self.requested_points, self.coefficient)


def _product_points(points, coefficient):
    precision = len(points.as_tuple().digits) + len(coefficient.as_tuple().digits)
    with localcontext(Context(prec=precision)):
        return points * coefficient


def sum_points(values: Iterable[Decimal]) -> Decimal:
    total = Decimal(0)
    for value in values:
        exponent = min(total.as_tuple().exponent, value.as_tuple().exponent)
        # Align both operands and allow one more digit for a carry.
        precision = max(total.adjusted(), value.adjusted()) - exponent + 2
        with localcontext(Context(prec=precision)):
            total += value
    return total


def _fixed_units(value, label, minimum, maximum):
    if isinstance(value, str):
        value = value.strip()
        if len(value) > 32 or not _DECIMAL_INPUT.fullmatch(value):
            raise ValueError(f'{label} must have at most four decimal places.')
        number = Decimal(value)
    elif type(value) is int:
        if not minimum <= value <= maximum:
            raise ValueError(f'{label} must be between {minimum} and {maximum}.')
        number = Decimal(value)
    elif isinstance(value, Decimal):
        number = value
    else:
        raise ValueError(f'{label} must be an exact decimal.')

    if not number.is_finite() or not minimum <= number <= maximum:
        raise ValueError(f'{label} must be between {minimum} and {maximum}.')
    _, digits, exponent = number.as_tuple()
    if exponent < -_POINTS_PLACES:
        raise ValueError(f'{label} must have at most four decimal places.')
    if not number:
        return 0

    significand = int(''.join(str(digit) for digit in digits))
    return significand * 10 ** (exponent + _POINTS_PLACES)


def _from_fixed_units(units):
    return Decimal(f'{units}e-{_POINTS_PLACES}')


def _coefficient_text(value):
    error = 'Coefficient must be a finite nonnegative plain decimal within the review form size limit.'
    if isinstance(value, str):
        value = value.strip()
        if len(value) <= _MAX_COEFFICIENT_CHARS and _COEFFICIENT_INPUT.fullmatch(value):
            return value
        raise ValueError(error)
    if type(value) is int and value.bit_length() <= _MAX_COEFFICIENT_CHARS * 4:
        value = Decimal(value)
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(error)
    if value.is_zero():
        return '0'
    _, digits, exponent = value.as_tuple()
    if len(digits) + max(exponent, 0) > _MAX_COEFFICIENT_CHARS or -exponent > _MAX_COEFFICIENT_CHARS:
        raise ValueError(error)
    text = format(value, 'f')
    if len(text) > _MAX_COEFFICIENT_CHARS:
        raise ValueError(error)
    return text


def _required_text(value, label, limit):
    if not isinstance(value, str):
        raise ValueError(f'{label} must contain 1–{limit} characters.')
    value = value.strip()
    if not 1 <= len(value) <= limit or '\x00' in value:
        raise ValueError(f'{label} must contain 1–{limit} characters.')
    return value


def _validate_request_token(token):
    if not isinstance(token, str) or not _REQUEST_TOKEN.fullmatch(token):
        raise ValueError('Invalid request token.')


def _submission_values(requested_points, reason, item_key, item_label):
    units = _fixed_units(requested_points, 'Requested points', Decimal('0.0001'), _MAX_POINTS)
    reason = _required_text(reason, 'Request reason', 1000)
    if item_key is not None or item_label is not None:
        if not isinstance(item_key, str) or len(item_key) > 128 or not _ITEM_KEY.fullmatch(item_key):
            raise ValueError('Invalid credit item key.')
        item_label = _required_text(item_label, 'Credit item label', 300)
    return units, reason, item_label


def validate_credit_dates(start_date, end_date) -> tuple[str, str]:
    if not all(isinstance(value, str) and _ACTIVITY_DATE.fullmatch(value) for value in (start_date, end_date)):
        raise ValueError('Both activity dates must use YYYY-MM-DD format.')
    try:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    except ValueError as error:
        raise ValueError('Enter valid activity dates.') from error
    if end < start:
        raise ValueError('The activity end date must be on or after the start date.')
    return start_date, end_date


def _submission_dates(start_date=None, end_date=None):
    if start_date is None and end_date is None:
        return None, None
    return validate_credit_dates(start_date, end_date)


def _utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _credit_request(row):
    coefficient = row['coefficient_text']
    return CreditRequest(
        id=row['id'],
        member_name=row['member_name'],
        requested_points=_from_fixed_units(row['requested_units']),
        reason=row['reason'],
        submitted_at=row['submitted_at'],
        coefficient=None if coefficient is None else Decimal(coefficient),
        review_reason=row['review_reason'],
        reviewed_by=row['reviewed_by'],
        reviewed_at=row['reviewed_at'],
        item_key=row['item_key'],
        item_label=row['item_label'],
        start_date=row['start_date'],
        end_date=row['end_date'],
    )


def _migrate_coefficients(db, columns):
    db.execute(_TABLE_SCHEMA.format(table_name='credit_requests_migrated'))
    metadata = ', '.join(name if name in columns else 'NULL' for name in _OPTIONAL_COLUMNS)
    db.execute(f'''
        INSERT INTO credit_requests_migrated (
            id, member_name, requested_units, reason, request_token, submitted_at,
            coefficient_text, review_reason, reviewed_by, reviewed_at, {', '.join(_OPTIONAL_COLUMNS)}
        )
        SELECT id, member_name, requested_units, reason, request_token, submitted_at,
               CASE WHEN coefficient_units IS NOT NULL
                    THEN printf('%d.%04d', coefficient_units / 10000, coefficient_units % 10000)
               END,
               review_reason, reviewed_by, reviewed_at, {metadata}
        FROM credit_requests
    ''')
    db.execute('DROP TABLE credit_requests')
    db.execute('ALTER TABLE credit_requests_migrated RENAME TO credit_requests')


class CreditRequests:
    def __init__(self, database_path):
        self.database_path = database_path
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(credit_requests)')}
            if not columns:
                db.execute(_TABLE_SCHEMA.format(table_name='credit_requests'))
            elif 'coefficient_units' in columns:
                _migrate_coefficients(db, columns)
            else:
                if 'deleted_at' not in columns:
                    db.execute('ALTER TABLE credit_requests ADD COLUMN deleted_at TEXT')
                if 'deleted_by' not in columns:
                    db.execute('''
                        ALTER TABLE credit_requests ADD COLUMN deleted_by TEXT
                        CHECK ((deleted_at IS NULL) = (deleted_by IS NULL))
                    ''')
            db.execute('''
                CREATE INDEX IF NOT EXISTS credit_requests_member_id
                    ON credit_requests (member_name, id DESC)
            ''')
            db.execute('''
                CREATE INDEX IF NOT EXISTS credit_requests_member_batch
                    ON credit_requests (member_name, batch_token)
                    WHERE batch_token IS NOT NULL
            ''')

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.database_path)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    def submit(
        self, member_name, requested_points, reason, request_token, *,
        item_key=None, item_label=None, start_date=None, end_date=None,
    ) -> CreditRequest:
        member_name = _required_text(member_name, 'Member name', 40)
        units, reason, item_label = _submission_values(requested_points, reason, item_key, item_label)
        start_date, end_date = _submission_dates(start_date, end_date)
        _validate_request_token(request_token)

        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute(
                'SELECT 1 FROM credit_requests WHERE member_name = ? AND batch_token = ? LIMIT 1',
                (member_name, request_token),
            ).fetchone():
                raise ValueError('Request token is already associated with a different submission.')
            if start_date is not None:
                db.execute('''
                    INSERT INTO credit_requests (
                        member_name, requested_units, reason, request_token, submitted_at,
                        item_key, item_label, start_date, end_date
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (member_name, request_token) DO NOTHING
                ''', (member_name, units, reason, request_token, _utc_timestamp(),
                      item_key, item_label, start_date, end_date))
            row = db.execute(
                f'SELECT {_REQUEST_COLUMNS}, deleted_at FROM credit_requests '
                'WHERE member_name = ? AND request_token = ?',
                (member_name, request_token),
            ).fetchone()
            if row is None:
                raise ValueError('Both activity dates are required.')
            previous = (row['requested_units'], row['reason'], row['item_key'], row['start_date'], row['end_date'])
            if previous != (units, reason, item_key, start_date, end_date):
                raise ValueError('Request token is already associated with a different submission.')
            if row['deleted_at'] is not None:
                raise ValueError('This credit request was deleted.')
            return _credit_request(row)

    def submit_batch(self, member_name, entries, request_token) -> list[CreditRequest]:
        member_name = _required_text(member_name, 'Member name', 40)
        _validate_request_token(request_token)
        try:
            entries = iter(entries)
        except TypeError as error:
            raise ValueError('Credit entries must be iterable.') from error

        prepared = {}
        for entry in entries:
            if len(prepared) >= MAX_BATCH_ITEMS:
                raise ValueError(f'A credit submission may contain at most {MAX_BATCH_ITEMS} items.')
            if not isinstance(entry, (tuple, list)) or len(entry) not in (4, 6):
                raise ValueError('Each entry must contain a credit item, label, points, reason and activity dates.')
            item_key, item_label, requested_points, reason, *dates = entry
            start_date, end_date = _submission_dates(*dates)
            if item_key is None:
                raise ValueError('Each entry must identify a credit item.')
            units, reason, item_label = _submission_values(requested_points, reason, item_key, item_label)
            if item_key in prepared:
                raise ValueError('A credit item may only be submitted once per batch.')
            prepared[item_key] = (units, reason, item_label, start_date, end_date)
        if not prepared:
            raise ValueError('Select at least one credit item.')

        query = f'''
            SELECT {_REQUEST_COLUMNS}, deleted_at FROM credit_requests
            WHERE member_name = ? AND batch_token = ? ORDER BY id
        '''
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute(
                'SELECT 1 FROM credit_requests WHERE member_name = ? AND request_token = ?',
                (member_name, request_token),
            ).fetchone():
                raise ValueError('Request token is already associated with a different submission.')
            rows = db.execute(query, (member_name, request_token)).fetchall()
            if rows:
                previous = {
                    row['item_key']: (row['requested_units'], row['reason'], row['start_date'], row['end_date'])
                    for row in rows
                }
                current = {
                    key: (units, reason, start_date, end_date)
                    for key, (units, reason, _, start_date, end_date) in prepared.items()
                }
                if previous != current:
                    raise ValueError('Request token is already associated with a different submission.')
                return [_credit_request(row) for row in rows if row['deleted_at'] is None]

            if any(start_date is None for _, _, _, start_date, _ in prepared.values()):
                raise ValueError('Both activity dates are required for each credit item.')
            submitted_at = _utc_timestamp()
            db.executemany('''
                INSERT INTO credit_requests (
                    member_name, requested_units, reason, request_token, submitted_at,
                    item_key, item_label, batch_token, start_date, end_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                # A period keeps internal row tokens distinct from submitter-supplied tokens.
                (member_name, units, reason, f'{request_token}.{item_key}', submitted_at,
                 item_key, label, request_token, start_date, end_date)
                for item_key, (units, reason, label, start_date, end_date) in prepared.items()
            ))
            rows = db.execute(query, (member_name, request_token))
            return [_credit_request(row) for row in rows]

    def for_member(self, member_name) -> list[CreditRequest]:
        member_name = _required_text(member_name, 'Member name', 40)
        with self._connect() as db:
            rows = db.execute(
                f'SELECT {_REQUEST_COLUMNS} FROM credit_requests '
                'WHERE member_name = ? AND deleted_at IS NULL ORDER BY start_date DESC, id DESC',
                (member_name,),
            )
            return [_credit_request(row) for row in rows]

    def all(self) -> list[CreditRequest]:
        with self._connect() as db:
            rows = db.execute(f'''
                SELECT {_REQUEST_COLUMNS} FROM credit_requests
                WHERE deleted_at IS NULL ORDER BY start_date DESC, id DESC
            ''')
            return [_credit_request(row) for row in rows]

    def review(self, request_id, coefficient, reason='', reviewer_name=None) -> CreditRequest | None:
        if type(request_id) is not int or not 1 <= request_id <= _MAX_REQUEST_ID:
            raise ValueError('Invalid request ID.')
        coefficient = _coefficient_text(coefficient)
        if isinstance(reason, str) and not reason.strip():
            reason = None
        elif reason is not None:
            reason = _required_text(reason, 'Review reason', 1000)
        reviewer_name = _required_text(reviewer_name, 'Reviewer name', 40)

        with self._connect() as db:
            update = db.execute('''
                UPDATE credit_requests
                SET coefficient_text = ?, review_reason = ?, reviewed_by = ?, reviewed_at = ?
                WHERE id = ? AND coefficient_text IS NULL AND deleted_at IS NULL
            ''', (coefficient, reason, reviewer_name, _utc_timestamp(), request_id))
            if not update.rowcount:
                return None
            row = db.execute(
                f'SELECT {_REQUEST_COLUMNS} FROM credit_requests WHERE id = ?', (request_id,)
            ).fetchone()
            return _credit_request(row)

    def delete(self, request_id, reviewer_name) -> bool:
        if type(request_id) is not int or not 1 <= request_id <= _MAX_REQUEST_ID:
            raise ValueError('Invalid request ID.')
        reviewer_name = _required_text(reviewer_name, 'Reviewer name', 40)

        with self._connect() as db:
            update = db.execute('''
                UPDATE credit_requests
                SET deleted_at = ?, deleted_by = ?
                WHERE id = ? AND deleted_at IS NULL
            ''', (_utc_timestamp(), reviewer_name, request_id))
            return update.rowcount == 1

    def approved_totals(self) -> dict[str, Decimal]:
        totals = {}
        with self._connect() as db:
            rows = db.execute('''
                SELECT member_name, requested_units, coefficient_text
                FROM credit_requests
                WHERE coefficient_text IS NOT NULL AND deleted_at IS NULL
                ORDER BY member_name
            ''')
            for name, member_rows in groupby(rows, key=lambda row: row['member_name']):
                totals[name] = sum_points(
                    _product_points(_from_fixed_units(row['requested_units']), Decimal(row['coefficient_text']))
                    for row in member_rows
                )
        return totals
