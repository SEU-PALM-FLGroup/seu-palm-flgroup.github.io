from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from html import escape
from html.parser import HTMLParser
import re


ZERO = Decimal(0)
ONE = Decimal(1)
POINTS = re.compile(r'\s*([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*分?\s*')
SUMMARY_COUNTS = re.compile(r'(本次(?:计入|纳入)\s*)\d+(\s*人[，,]\s*(?:当前\s*)?)\d+(\s*人(?:已)?达标)')
MEMBER_GRADE = re.compile(r'[0-9]{4}\s*级')
MEMBER_PROGRAMS = {'硕士生', '硕士研究生', '博士生', '博士研究生', '普博生', '直博生', '硕博贯通', '硕博连读'}
REMAINING_HEADING = '还需积分'
LEGACY_REMAINING_HEADING = re.compile(r'还需积分\s*[/／与]\s*状态')
LEGACY_REMAINING_STATUS = re.compile(r'[\s·/／]*(?:待登记新增分|未达标|进行中|已达标|未纳入本次)\s*$')
MEMBER_HEADER = '''<article class="document service-credit-progress" id="service-credit-progress" aria-labelledby="service-credit-heading">
  <header class="document-head">
    <h2 id="service-credit-heading">我的公共服务积分进度</h2>
    <p class="document-summary">当前积分包含折算起始分和已审核通过的新增积分。</p>
  </header>
  <div class="document-body">'''
NO_MEMBER_PROGRESS = (MEMBER_HEADER + '\n    <p>暂无个人积分记录。</p>\n  </div>\n</article>').encode('utf-8')


@dataclass
class TextSpan:
    start: int
    end: int = 0
    parts: list[str] = field(default_factory=list)

    @property
    def text(self):
        return ''.join(self.parts)


class ProgressParser(HTMLParser):
    """Find the editable cells while preserving source markup outside them."""

    def __init__(self, source):
        super().__init__()
        self.line_starts = [0, *(match.end() for match in re.finditer('\n', source))]
        self.article_depth = 0
        self.article_count = 0
        self.table_depth = 0
        self.table_count = 0
        self.malformed_table = False
        self.rows = []
        self.summaries = []
        self.row = None
        self.cell = None
        self.cell_tag = None
        self.summary = None

    def source_offset(self):
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def after_start_tag(self):
        return self.source_offset() + len(self.get_starttag_text())

    def handle_starttag(self, tag, attributes):
        if tag == 'article' and (self.article_depth or dict(attributes).get('id') == 'service-credit-progress'):
            if not self.article_depth:
                self.article_count += 1
            self.article_depth += 1
        if not self.article_depth:
            return
        if tag == 'table':
            self.table_count += self.table_depth == 0
            self.table_depth += 1
        elif tag == 'tr' and self.table_depth == 1:
            self.malformed_table |= self.row is not None
            self.row = []
        elif tag in {'td', 'th'} and self.table_depth == 1 and self.row is not None:
            self.malformed_table |= self.cell is not None
            self.cell = TextSpan(self.after_start_tag())
            self.cell_tag = tag
        elif tag == 'p' and 'document-summary' in (dict(attributes).get('class') or '').split():
            self.summary = TextSpan(self.after_start_tag())

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.parts.append(data)
        if self.summary is not None:
            self.summary.parts.append(data)

    def handle_endtag(self, tag):
        if not self.article_depth:
            return
        if tag in {'td', 'th'} and self.table_depth == 1 and self.cell is not None:
            self.malformed_table |= self.cell_tag != tag
            self.cell.end = self.source_offset()
            self.row.append(self.cell)
            self.cell = None
        elif tag == 'tr' and self.table_depth == 1 and self.row is not None:
            self.malformed_table |= self.cell is not None
            self.rows.append(self.row)
            self.row = None
            self.cell = None
        elif tag == 'table':
            self.table_depth -= 1
        elif tag == 'p' and self.summary is not None:
            self.summary.end = self.source_offset()
            self.summaries.append(self.summary)
            self.summary = None
        elif tag == 'article':
            self.article_depth -= 1


def read_points(cell):
    match = POINTS.fullmatch(cell.text)
    return Decimal(match[1].replace(',', '')) if match else None


def baseline_remaining(cell):
    text = LEGACY_REMAINING_STATUS.sub('', cell.text).strip()
    match = POINTS.fullmatch(text)
    return text if match and Decimal(match[1].replace(',', '')) >= ZERO else None


def number_text(value):
    integer, separator, fraction = format(value, 'f').partition('.')
    fraction = fraction.rstrip('0')
    return integer + separator + fraction if fraction else integer


def updated_cells(required, current, approved):
    # Align the operands without losing digits when an approved total has a larger scale.
    with localcontext() as context:
        values = (required, current, approved)
        scale = min(value.as_tuple().exponent for value in values)
        context.prec = max(context.prec, max(value.adjusted() for value in values) - scale + 2)
        current += approved
        ratio = max(ZERO, min(ONE, current / required))
        remaining = max(ZERO, required - current)
        percent = f'{ratio * 100:.1f}'.removesuffix('.0') + '%'

    progress = (
        f'<progress value="{number_text(ratio)}" max="1" aria-label="完成进度" '
        f'aria-valuetext="{percent}">{percent}</progress> <span aria-hidden="true">{percent}</span>'
    )
    return current, (f'{number_text(current)} 分', progress, f'{number_text(remaining)} 分')


def render_progress(fragment: bytes, approved_totals: Mapping[str, Decimal]) -> bytes:
    try:
        source = fragment.decode('utf-8')
    except UnicodeDecodeError:
        return fragment

    parser = ProgressParser(source)
    parser.feed(source)
    parser.close()
    rows = []
    changes = {}
    for cells in parser.rows:
        if len(cells) != 7 or not (name := cells[0].text.strip()):
            continue
        remaining = cells[6]
        text = remaining.text.strip()
        if LEGACY_REMAINING_HEADING.fullmatch(text):
            changes[remaining.start, remaining.end] = REMAINING_HEADING
        elif LEGACY_REMAINING_STATUS.search(text):
            number = baseline_remaining(remaining)
            if number is not None or LEGACY_REMAINING_STATUS.fullmatch(text):
                changes[remaining.start, remaining.end] = escape(number or '—')
        required, current = read_points(cells[3]), read_points(cells[4])
        if required is not None and required > ZERO and current is not None:
            rows.append((name, cells, required, current))

    counts = Counter(name for name, _, _, _ in rows)
    scores_changed = False
    complete = 0
    for name, cells, required, current in rows:
        approved = approved_totals.get(name, ZERO)
        if counts[name] == 1 and approved.is_finite() and approved:
            current, contents = updated_cells(required, current, approved)
            changes.update({(cell.start, cell.end): text for cell, text in zip(cells[4:], contents)})
            scores_changed = True
        complete += current >= required
    if not changes:
        return fragment

    def replace_counts(match):
        return f'{match[1]}{len(rows)}{match[2]}{complete}{match[3]}'

    if scores_changed:
        for summary in parser.summaries:
            original = source[summary.start:summary.end]
            revised, count = SUMMARY_COUNTS.subn(replace_counts, original, count=1)
            if not count:
                revised, count = SUMMARY_COUNTS.subn(replace_counts, summary.text, count=1)
                revised = escape(revised)
            if count and revised != original:
                changes[summary.start, summary.end] = revised

    output = []
    previous_end = 0
    for (start, end), text in sorted(changes.items()):
        output.extend((source[previous_end:start], text))
        previous_end = end
    output.append(source[previous_end:])
    return ''.join(output).encode('utf-8')


def member_progress(fragment: bytes, member_name: str, approved_total: Decimal = ZERO) -> bytes:
    """Render an account's record from validated scalar fields, without copying source HTML."""
    if not fragment or not member_name:
        return NO_MEMBER_PROGRESS
    try:
        source = fragment.decode('utf-8')
        parser = ProgressParser(source)
        parser.feed(source)
        parser.close()
    except (UnicodeDecodeError, AssertionError):
        return NO_MEMBER_PROGRESS
    if (parser.article_count != 1 or parser.table_count != 1 or parser.malformed_table
            or parser.article_depth or parser.table_depth or parser.row is not None):
        return NO_MEMBER_PROGRESS

    matching = [cells for cells in parser.rows if cells and cells[0].text.strip() == member_name]
    if len(matching) != 1 or len(matching[0]) != 7:
        return NO_MEMBER_PROGRESS
    cells = matching[0]
    grade, program = (cell.text.strip() for cell in cells[1:3])
    required, current = read_points(cells[3]), read_points(cells[4])
    if not MEMBER_GRADE.fullmatch(grade) or program not in MEMBER_PROGRAMS or required is None or required <= ZERO or current is None:
        return NO_MEMBER_PROGRESS

    approved = approved_total if approved_total.is_finite() else ZERO
    _, (current_html, progress, remaining_html) = updated_cells(required, current, approved)
    if not approved:
        current_html = escape(cells[4].text.strip())
        baseline = baseline_remaining(cells[6])
        if baseline is not None:
            remaining_html = escape(baseline)

    labels = ('姓名', '入学年级', '培养类别', '所需总分', '当前积分', '完成进度', REMAINING_HEADING)
    headers = ''.join(f'<th scope="col">{label}</th>' for label in labels)
    values = (escape(grade), escape(program), escape(cells[3].text.strip()), current_html, progress, remaining_html)
    value_cells = ''.join(f'<td>{value}</td>' for value in values)
    return (MEMBER_HEADER + f'''
    <div class="table-wrap" role="region" aria-labelledby="service-credit-heading" tabindex="0">
      <table aria-labelledby="service-credit-heading">
        <thead><tr>{headers}</tr></thead>
        <tbody><tr data-search-item><th scope="row">{escape(member_name)}</th>{value_cells}</tr></tbody>
      </table>
    </div>
  </div>
</article>''').encode('utf-8')
