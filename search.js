'use strict';

const panel = document.querySelector('.search-panel');
const form = panel.querySelector('form');
const input = document.querySelector('#site-search');
const status = document.querySelector('#search-status');
const results = document.querySelector('#search-results');
const maxResults = 20;

const tidy = (text) => text.replace(/\s+/gu, ' ').trim();
const itemText = (item) => item.cells
  ? Array.from(item.cells, (cell) => tidy(cell.textContent)).join(' · ')
  : tidy(item.innerText);

const records = Array.from(document.querySelectorAll('.document')).flatMap((article) => {
  const articleTitle = tidy(article.querySelector('h2').textContent);
  let section = '';
  return Array.from(article.querySelectorAll('h2, h3, h4, h5, [data-search-item]'), (item, index) => {
    if (item.matches('h3, h4, h5')) section = tidy(item.textContent);
    if (!item.id) item.id = `${article.id}-entry-${index}`;
    const text = itemText(item);
    return { id: item.id, text, location: section === text || !section ? articleTitle : `${articleTitle} / ${section}` };
  });
});

function excerpt(text, query) {
  const start = Math.max(0, text.toLowerCase().indexOf(query) - 30);
  const end = Math.min(text.length, start + Math.max(150, query.length + 65));
  return `${start ? '…' : ''}${text.slice(start, end)}${end < text.length ? '…' : ''}`;
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  const query = tidy(input.value).toLowerCase();
  results.replaceChildren();
  if (!query) {
    status.textContent = '请输入搜索关键词。';
    return;
  }

  const matches = records.filter((record) => record.text.toLowerCase().includes(query));
  status.textContent = matches.length > maxResults
    ? `找到 ${matches.length} 条结果，显示前 ${maxResults} 条。`
    : `找到 ${matches.length} 条结果。`;

  for (const match of matches.slice(0, maxResults)) {
    const item = document.createElement('li');
    const link = document.createElement('a');
    const location = document.createElement('span');
    link.href = `#${match.id}`;
    link.textContent = excerpt(match.text, query);
    location.textContent = match.location;
    item.append(link, location);
    results.append(item);
  }
});

input.addEventListener('input', () => {
  if (!input.value) {
    results.replaceChildren();
    status.textContent = '';
  }
});

panel.hidden = false;
