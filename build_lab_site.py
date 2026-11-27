import argparse
from collections import Counter
from html import escape, unescape
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from lab_accounts import Accounts


W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
M = '{http://schemas.openxmlformats.org/officeDocument/2006/math}'
REPO = Path(__file__).resolve().parent
DEFAULT_SOURCE_DIR = Path.home() / 'Documents'
DEFAULT_DATA_DIR = REPO / '.lab-data'

# Paragraph indexes refer to the supplied files, including blank paragraphs.
SOURCES = {
    'members': {
        'file': '课题组成员与当前事项.docx',
        'title': 1,
        'metadata': {0: 'source-title', 2: 'source-date'},
        'headings': {
            3: ('phd-students', 3, '博士生'),
            5: ('masters-students', 3, '硕士生'),
            7: ('group-projects', 3, '课题组项目'),
        },
        'tables': {4: 'phd-students', 6: 'masters-students'},
    },
    'policy': {
        'file': '课题组管理细则.docx',
        'title': 0,
        'metadata': {},
        'headings': {
            1: ('early-graduation', 3, '提前毕业要求'),
            5: ('service-credits', 3, '公共服务所需积分'),
            11: ('service-standards', 3, '公共服务范围及计分标准'),
            12: ('service-overview', 4, '服务范围总览'),
            14: ('scoring-rules', 4, '各类服务评分细则'),
            15: ('project-participation', 5, '组内项目参与'),
            25: ('resource-maintenance', 5, '公共资源维护'),
            27: ('newcomer-mentoring', 5, '新人培训'),
            32: ('academic-events', 5, '学术活动'),
            37: ('project-applications', 5, '项目申请材料撰写'),
            39: ('outreach', 5, '宣传与资源获取'),
            42: ('group-operations', 5, '组务轮值与运行协调'),
            45: ('publication-support', 3, '论文奖励或报销'),
        },
        'tables': {6: 'service-credits', 13: 'service-overview', 17: 'project-participation'},
    },
    'writing': {
        'file': '英文论文写作注意事项.docx',
        'title': 0,
        'metadata': {1: 'source-subtitle', 2: 'source-byline'},
        'headings': {
            4: ('paper-title', 3, 'Title'),
            8: ('paper-abstract', 3, 'Abstract'),
            15: ('paper-introduction', 3, 'Introduction'),
            20: ('paper-main-text', 3, 'Main text'),
            32: ('paper-experiments', 3, 'Experiments'),
            37: ('paper-references', 3, 'References'),
        },
        'tables': {},
    },
}

TONES = {'6B7785': 'slate', '24476B': 'navy', '24676C': 'teal', '245E8A': 'blue'}


def value(element, name):
    child = element.find(name)
    return None if child is None else child.get(W + 'val', '')


def text_of(element):
    return ''.join(
        (node.text or '') if node.tag in (W + 't', M + 't') else '\n'
        for node in element.iter()
        if node.tag in (W + 't', M + 't', W + 'br')
    )


def inline(paragraph):
    output = []
    for element in paragraph:
        if element.tag == M + 'oMath':
            # These three source equations contain only inline text runs.
            assert all(child.tag == M + 'r' for child in element)
            output.append(f'<span class="word-math">{escape(text_of(element))}</span>')
        elif element.tag == W + 'r':
            run = escape(text_of(element)).replace('\n', '<br>')
            if not run:
                continue
            bold = value(element, W + 'rPr/' + W + 'b')
            underline = value(element, W + 'rPr/' + W + 'u')
            tone = TONES.get(value(element, W + 'rPr/' + W + 'color'))
            if bold not in (None, '0', 'false', 'off'):
                run = f'<strong>{run}</strong>'
            if underline not in (None, 'none', '0'):
                run = f'<span class="word-underline">{run}</span>'
            if tone:
                run = f'<span class="word-tone-{tone}">{run}</span>'
            output.append(run)
    rendered = ''.join(output)
    assert unescape(re.sub('<[^>]+>', '', rendered.replace('<br>', '\n'))) == text_of(paragraph)
    return rendered


def list_styles(numbering):
    formats = {
        abstract.get(W + 'abstractNumId'): value(abstract, W + 'lvl/' + W + 'numFmt')
        for abstract in numbering.findall(W + 'abstractNum')
    }
    return {
        number.get(W + 'numId'): formats[value(number, W + 'abstractNumId')]
        for number in numbering.findall(W + 'num')
    }


def read_roster_table(table, roster):
    rows = iter(table.findall(W + 'tr'))
    header = next(rows, None)
    columns = [] if header is None else header.findall(W + 'tc')
    if len(columns) < 2 or '一卡通' not in text_of(columns[0]) or '姓名' not in text_of(columns[1]):
        raise ValueError('The member tables must begin with card number and name columns.')
    for row in rows:
        cells = row.findall(W + 'tc')
        if len(cells) != len(columns):
            raise ValueError('The member roster contains a row with missing or extra columns.')
        card_number, name = (text_of(cell).strip() for cell in cells[:2])
        if not name or name in roster:
            raise ValueError('The member roster contains a missing or duplicate name.')
        roster[name] = card_number if re.fullmatch(r'[0-9]{1,24}', card_number) else None


def render_table(table, document_id, position, label, metrics):
    html_rows = []
    for row_index, row in enumerate(table.findall(W + 'tr')):
        cells = []
        for column_index, cell in enumerate(row.findall(W + 'tc')):
            assert not any(node.tag in (W + 'gridSpan', W + 'vMerge', W + 'tbl') for node in cell.iter())
            if document_id == 'members' and column_index == 0:
                continue
            paragraph_html = []
            for paragraph_index, paragraph in enumerate(cell.findall(W + 'p')):
                key = f'{document_id}-{position}-{row_index}-{column_index}-{paragraph_index}'
                paragraph_html.append(f'<p data-source="{key}">{inline(paragraph)}</p>')
                metrics['cells'] += 1
            if row_index == 0:
                tag = 'th scope="col"'
            elif column_index == (1 if document_id == 'members' else 0):
                tag = 'th scope="row"'
            else:
                tag = 'td'
            cells.append(f'<{tag}>{"".join(paragraph_html)}</{tag.split()[0]}>')
        html_rows.append(f'<tr data-search-item>{"".join(cells)}</tr>')
        metrics['rows'] += 1
    metrics['tables'] += 1
    head, *body = html_rows
    return (
        f'<div class="table-wrap" role="region" aria-labelledby="{label}" tabindex="0">\n'
        f'<table class="{document_id}-table" aria-labelledby="{label}">\n'
        f'<thead>{head}</thead>\n<tbody>\n' + '\n'.join(body) + '\n</tbody>\n</table>\n</div>'
    )


def render_document(document_id, config, source_path, roster=None):
    with ZipFile(source_path) as package:
        body = ET.fromstring(package.read('word/document.xml')).find(W + 'body')
        numbering = list_styles(ET.fromstring(package.read('word/numbering.xml')))

    head = []
    content = []
    metrics = Counter()
    current_list = None
    for index, element in enumerate(body):
        key = f'{document_id}-{index}'
        if element.tag == W + 'sectPr' or (element.tag == W + 'p' and not text_of(element).strip()):
            continue
        number = value(element, W + 'pPr/' + W + 'numPr/' + W + 'numId')
        if number is not None:
            assert value(element, W + 'pPr/' + W + 'numPr/' + W + 'ilvl') in (None, '0')
            kind = {'decimal': 'ol', 'bullet': 'ul'}[numbering[number]]
            next_list = (kind, number if kind == 'ol' else None)
        else:
            next_list = None
        if current_list != next_list:
            if current_list:
                content.append(f'</{current_list[0]}>')
            if next_list:
                content.append(f'<{next_list[0]} class="word-list">')
            current_list = next_list

        if element.tag == W + 'tbl':
            if roster is not None:
                read_roster_table(element, roster)
            content.append(render_table(element, document_id, index, config['tables'][index], metrics))
            continue
        assert element.tag == W + 'p'
        metrics['paragraphs'] += 1
        metrics['formulas'] += len(list(element.iter(M + 'oMath')))
        metrics['line_breaks'] += len(list(element.iter(W + 'br')))
        rendered = inline(element)
        source = f'data-source="{key}"'
        if index == config['title']:
            head.append(f'<h2 id="{document_id}-title" {source}>{rendered}</h2>')
        elif index in config['metadata']:
            head.append(f'<p class="{config["metadata"][index]}" {source}>{rendered}</p>')
        elif index in config['headings']:
            anchor, level, _ = config['headings'][index]
            language = ' lang="en"' if document_id == 'writing' else ''
            content.append(f'<h{level} id="{anchor}" {source}{language}>{rendered}</h{level}>')
        elif current_list:
            content.append(f'<li {source} data-search-item>{rendered}</li>')
            metrics['list_items'] += 1
        else:
            note = ' class="source-note"' if document_id == 'writing' else ''
            content.append(f'<p{note} {source} data-search-item>{rendered}</p>')
    if current_list:
        content.append(f'</{current_list[0]}>')

    article = '\n'.join([
        f'<article class="document" id="{document_id}" aria-labelledby="{document_id}-title">',
        '<header class="document-head">', *head, '</header>',
        '<div class="document-body">', *content, '</div>', '</article>',
    ])
    return article, metrics


def table_of_contents():
    links = []
    for key, name in [('members', '成员与当前事项'), ('policy', '管理细则'), ('writing', '英文论文写作')]:
        links.append(f'<a class="toc-group" href="#{key}">{name}</a>')
        links.extend(
            f'<a href="#{anchor}">{label}</a>'
            for anchor, level, label in SOURCES[key]['headings'].values() if level == 3
        )
    return '\n'.join(links)


def document_fragment(articles):
    return '\n'.join([
        '<div class="content-grid">',
        '<aside class="contents-sidebar">',
        '<nav class="toc" aria-labelledby="toc-label">',
        '<p class="toc-label" id="toc-label">本页目录</p>',
        table_of_contents(),
        '</nav>',
        '</aside>',
        '<div class="document-stack">',
        *articles,
        '</div>',
        '</div>',
        '',
    ])


def main():
    parser = argparse.ArgumentParser(description='Import private lab documents and member accounts from Word files.')
    parser.add_argument('--source-dir', type=Path, default=DEFAULT_SOURCE_DIR,
                        help='directory containing the three original Word files')
    parser.add_argument('--roster-file', type=Path,
                        help='replacement for the member roster Word file in --source-dir')
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_DATA_DIR,
                        help='private SQLite storage directory (default: .lab-data in this repository)')
    args = parser.parse_args()
    source_dir = args.source_dir.resolve()
    data_dir = args.data_dir.resolve()
    if data_dir.is_relative_to(REPO) and not data_dir.is_relative_to(DEFAULT_DATA_DIR):
        parser.error('--data-dir must be inside .lab-data or outside this repository')

    rendered = []
    roster = {}
    for key, config in SOURCES.items():
        source_path = args.roster_file if key == 'members' and args.roster_file else source_dir / config['file']
        if not source_path.is_file():
            parser.error(f'missing source document: {source_path}')
        try:
            article, metrics = render_document(key, config, source_path, roster if key == 'members' else None)
        except ValueError as error:
            parser.error(str(error))
        if key == 'members' and (not roster or metrics['tables'] != len(config['tables'])):
            parser.error('Could not load both member tables from the roster Word file.')
        rendered.append(article)
        print(f'{key}: {dict(metrics)}')

    data_dir.mkdir(parents=True, exist_ok=True)
    Accounts(data_dir / 'lab_accounts.sqlite3').sync_roster(roster, document_fragment(rendered).encode('utf-8'))
    missing = sum(card_number is None for card_number in roster.values())
    print(f'Private resources imported for {len(roster)} members ({missing} card numbers missing from Word).')


if __name__ == '__main__':
    main()
