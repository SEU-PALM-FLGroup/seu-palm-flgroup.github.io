from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlencode

from credit_items import CREDIT_ITEMS, CREDIT_ITEMS_BY_KEY
from credit_requests import COEFFICIENT_PATTERN, sum_points


BEIJING = timezone(timedelta(hours=8))


def number(value):
    if value == 0:
        return '0'
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def time_tag(value):
    local = datetime.fromisoformat(value).astimezone(BEIJING)
    return f'<time datetime="{local.isoformat()}">{local:%Y-%m-%d %H:%M:%S}</time>'


def service_date_range(start, end):
    if not start and not end:
        return '原申请未填写日期'
    dates = [f'<time datetime="{escape(value)}">{escape(value)}</time>' if value else '未填写'
             for value in (start, end)]
    return dates[0] if start == end else ' 至 '.join(dates)


def request_status(approved_points):
    if approved_points is None:
        return '待审核'
    if not approved_points:
        return '未计分'
    return '已计分' if approved_points > 0 else '已扣分'


def review_details(record, approved_points=None):
    if record.coefficient is None:
        return ''
    score = (f'<dt>实得积分</dt><dd class="credit-reason">{number(approved_points)} 分</dd>'
             if approved_points is not None else '')
    reason = record.review_reason or ''
    reason_row = f'<dt>审核理由</dt><dd class="credit-reason">{escape(reason)}</dd>' if reason.strip() else ''
    return f'''
          <dt>审核系数</dt><dd class="credit-reason">{number(record.coefficient)}</dd>
          {score}
          {reason_row}
          <dt>审核人</dt><dd>{escape(record.reviewed_by)}</dd>
          <dt>审核时间</dt><dd>{time_tag(record.reviewed_at)}</dd>'''


def request_delete(record, *, member=None):
    context = f'<input type="hidden" name="member" value="{escape(member)}">' if member is not None else ''
    return f'''<details class="credit-delete">
      <summary>删除申请</summary>
      <form action="/admin/credits/delete" method="post">
        <input type="hidden" name="request_id" value="{escape(str(record.id))}">
        {context}
        <p>确认删除这条申请并更新累计积分？</p>
        <button class="logout-button credit-delete-confirm" type="submit">确认删除</button>
      </form>
    </details>'''


def request_card(record, *, show_member=False, review_form=''):
    approved_points = record.approved_points
    status = request_status(approved_points)
    name = f'<h3>{escape(record.member_name)}</h3>' if show_member else ''
    return f'''<article class="credit-card">
      <header class="credit-card-heading">{name}<span>{status}</span></header>
      <dl class="credit-record">
        <dt>申请时间</dt><dd>{time_tag(record.submitted_at)}</dd>
        <dt>积分事项</dt><dd>{escape(record.item_label or '原申请未填写事项')}</dd>
        <dt>服务日期</dt><dd>{service_date_range(record.start_date, record.end_date)}</dd>
        <dt>申请积分</dt><dd>{number(record.requested_points)} 分</dd>
        <dt>申请理由</dt><dd class="credit-reason">{escape(record.reason)}</dd>
        {review_details(record, approved_points)}
      </dl>
      {review_form}
      {request_delete(record) if show_member else ''}
    </article>'''


def credit_item_fields(item, points='', reason='', *, start='', end=''):
    key = escape(item.key)
    label = escape(item.label)
    reason_label = '具体事项和申请理由' if item.key == 'other' else '申请理由'
    return f'''<fieldset class="credit-basket-entry" data-credit-entry="{key}">
          <legend>{label}</legend>
          <div class="credit-entry-fields">
            <div class="credit-entry-dates">
              <div>
                <label for="start-{key}">开始日期</label>
                <input id="start-{key}" name="start.{key}" type="date" value="{escape(start)}" data-credit-start required>
              </div>
              <div>
                <label for="end-{key}">结束日期</label>
                <input id="end-{key}" name="end.{key}" type="date" value="{escape(end)}" data-credit-end required>
              </div>
            </div>
            <label for="points-{key}">申请积分</label>
            <input id="points-{key}" name="points.{key}" type="number" min="0.0001"
                   max="1000000" step="0.0001" value="{escape(points)}" required>
            <label for="reason-{key}">{reason_label}</label>
            <textarea id="reason-{key}" name="reason.{key}" rows="3" maxlength="1000" required>{escape(reason)}</textarea>
            <button class="logout-button" type="button" data-credit-remove aria-label="移除 {label}" hidden>移除</button>
          </div>
        </fieldset>'''


def credit_catalog():
    groups = {}
    for item in CREDIT_ITEMS:
        label = escape(item.label)
        guidance = f'<p class="credit-item-rule">{escape(item.rule)}</p>' if item.rule else ''
        choice = f'''<li class="credit-choice">
            <div><strong>{label}</strong>{guidance}</div>
            <button class="logout-button credit-choice-add" type="button" data-credit-add="{escape(item.key)}"
                    aria-label="添加 {label}" hidden>添加</button>
            <template>{credit_item_fields(item)}</template>
          </li>'''
        groups.setdefault(item.group, []).append(choice)
    return ''.join(f'''<details class="credit-category">
          <summary>{escape(group)}</summary>
          <ul>{''.join(choices)}</ul>
        </details>''' for group, choices in groups.items())


def credit_draft(draft):
    items = {}
    for field, value in draft.items():
        kind, _, key = field.partition('.')
        if kind in {'start', 'end', 'points', 'reason'} and key in CREDIT_ITEMS_BY_KEY:
            items.setdefault(key, {})[kind] = value
    legacy = draft.get('item_key')
    if legacy in CREDIT_ITEMS_BY_KEY and legacy not in items:
        items[legacy] = {
            'start': draft.get('start_date', ''), 'end': draft.get('end_date', ''),
            'points': draft.get('requested_points', ''), 'reason': draft.get('reason', ''),
        }
    return items


def history_row(record, approved_points, member):
    actual = '—' if approved_points is None else f'{number(approved_points)} 分'
    deletion = request_delete(record, member=member) if member is not None else ''
    return f'''<tr>
      <th scope="row">{escape(record.item_label or '原申请未填写事项')}</th>
      <td>{service_date_range(record.start_date, record.end_date)}</td>
      <td>{time_tag(record.submitted_at)}</td>
      <td class="credit-reason">{number(record.requested_points)} 分</td>
      <td class="credit-reason">{actual}</td>
      <td>{request_status(approved_points)}</td>
      <td><details>
        <summary>查看详情</summary>
        <dl class="credit-record">
          <dt>申请理由</dt><dd class="credit-reason">{escape(record.reason)}</dd>
          {review_details(record)}
        </dl>
      </details>{deletion}</td>
    </tr>'''


def request_history(records, heading, *, member=None):
    requests = [(record, record.approved_points) for record in records]
    total = sum_points(points for _, points in requests if points is not None)
    history = '<p class="document-summary">暂无申请记录。</p>'
    if requests:
        detail_label = '说明' if member is None else '说明与操作'
        columns = ('积分事项', '服务日期', '申请时间', '申请积分', '实得积分', '状态', detail_label)
        headings = ''.join(f'<th scope="col">{column}</th>' for column in columns)
        rows = ''.join(history_row(record, points, member) for record, points in requests)
        history = f'''<div class="table-wrap credit-history-table" role="region" aria-labelledby="credit-history" tabindex="0">
        <table aria-labelledby="credit-history">
          <thead><tr>{headings}</tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>'''
    return f'''<section class="credit-history" aria-labelledby="credit-history">
      <h2 id="credit-history">{escape(heading)}</h2>
      <p class="document-summary credit-reason">申请累计实得 {number(total)} 分。时间均为北京时间。</p>
      {history}
    </section>'''


def member_history(records):
    return request_history(records, '我的申请记录')


def admin_history(records, member):
    selected = (record for record in records if record.member_name == member)
    return ('<p><a href="/admin/credits#reviewed-credits">返回学生名单</a></p>'
            + request_history(selected, f'{member}的申请记录', member=member))


def member_content(records, token, draft):
    selected = credit_draft(draft)
    fields = ''.join(credit_item_fields(CREDIT_ITEMS_BY_KEY[key], **values) for key, values in selected.items())
    return f'''<section aria-labelledby="new-credit" data-credit-picker>
      <div class="credit-picker-heading">
        <h2 id="new-credit">新增积分</h2>
        <a href="#credit-basket" data-credit-basket-link{' hidden' if not selected else ''}>已选 <span data-credit-count>{len(selected)}</span> 项</a>
      </div>
      <p class="document-summary">按<a href="/#service-standards" target="_blank" rel="noopener">公共服务计分标准</a>逐项填写服务日期、积分和理由。</p>
      <noscript><p class="document-summary">启用 JavaScript 后可从列表添加或移除事项。</p></noscript>
      <p class="credit-picker-feedback" role="status" data-credit-feedback></p>
      <div class="credit-workspace">
        <div class="credit-catalog">
          <h3>积分事项</h3>
          {credit_catalog()}
        </div>
        <form id="credit-basket" class="credit-form credit-basket" action="/credits" method="post" aria-labelledby="credit-basket-title">
          <h3 id="credit-basket-title" tabindex="-1">本次申请</h3>
          <input type="hidden" name="request_token" value="{escape(token)}">
          <p class="document-summary" data-credit-empty{' hidden' if selected else ''}>展开分类并添加事项。</p>
          <div class="credit-entries" data-credit-entries>{fields}</div>
          <button class="logout-button credit-primary" type="submit"{' disabled' if not selected else ''}>提交申请</button>
        </form>
      </div>
    </section>
    <script src="/credit-items.js" defer></script>
    {member_history(records)}'''


def student_history_list(student_names, record_counts):
    names = sorted(record_counts.keys() | set(student_names))
    if not names:
        return '<p class="document-summary">暂无学生记录。</p>'
    links = []
    for name in names:
        query = escape(urlencode({'member': name}))
        links.append(f'''<li><a href="/admin/credits/history?{query}">
        <span>{escape(name)}</span><span class="credit-student-count">{record_counts[name]} 条</span>
      </a></li>''')
    return '<ul class="credit-student-list">' + ''.join(links) + '</ul>'


def admin_content(records, draft, student_names=()):
    pending = []
    record_counts = Counter()
    for record in records:
        record_counts[record.member_name] += 1
        if record.coefficient is not None:
            continue
        request_id = str(record.id)
        inputs = draft if draft.get('request_id') == request_id else {}
        form = f'''<form class="credit-form credit-review-form" action="/admin/credits/review" method="post">
          <input type="hidden" name="request_id" value="{request_id}">
          <label for="coefficient-{request_id}">审核系数</label>
          <input id="coefficient-{request_id}" name="coefficient" type="text" inputmode="decimal" autocomplete="off"
                 pattern="{COEFFICIENT_PATTERN}" title="请输入 0 或正数"
                 spellcheck="false" aria-describedby="credit-review-guidance" value="{escape(inputs.get('coefficient', ''))}" required>
          <label for="review-reason-{request_id}">审核理由（选填）</label>
          <textarea id="review-reason-{request_id}" name="reason" rows="3" maxlength="1000">{escape(inputs.get('reason', ''))}</textarea>
          <button class="logout-button credit-primary" type="submit">确认审核</button>
        </form>'''
        pending.append(request_card(record, show_member=True, review_form=form))
    return f'''<p id="credit-review-guidance" class="document-summary">实得积分为申请积分乘以审核系数。系数可填 0 或正数，例如 0.5 或 1.25。时间均为北京时间。</p>
    <section class="credit-history" aria-labelledby="pending-credits">
      <h2 id="pending-credits">待审核申请（{len(pending)}）</h2>
      {''.join(pending) or '<p class="document-summary">暂无待审核申请。</p>'}
    </section>
    <section class="credit-history" aria-labelledby="reviewed-credits">
      <h2 id="reviewed-credits">学生积分记录</h2>
      {student_history_list(student_names, record_counts)}
    </section>'''
