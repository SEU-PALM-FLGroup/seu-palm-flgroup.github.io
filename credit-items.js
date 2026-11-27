(() => {
  const picker = document.querySelector('[data-credit-picker]');
  if (!picker) return;

  const basket = picker.querySelector('#credit-basket');
  const entries = basket.querySelector('[data-credit-entries]');
  const basketTitle = basket.querySelector('#credit-basket-title');
  const empty = basket.querySelector('[data-credit-empty]');
  const submit = basket.querySelector('button[type="submit"]');
  const basketLink = picker.querySelector('[data-credit-basket-link]');
  const count = basketLink.querySelector('[data-credit-count]');
  const feedback = picker.querySelector('[data-credit-feedback]');
  const choices = new Map(Array.from(picker.querySelectorAll('[data-credit-add]'), (button) => [button.dataset.creditAdd, button]));
  const selected = new Map();

  function updateBasket() {
    count.textContent = String(selected.size);
    empty.hidden = selected.size > 0;
    basketLink.hidden = selected.size === 0;
    submit.disabled = selected.size === 0;
  }

  function showChoiceState(key) {
    const button = choices.get(key);
    const added = selected.has(key);
    button.textContent = added ? '已添加' : '添加';
    button.setAttribute('aria-disabled', String(added));
    const label = button.closest('.credit-choice').querySelector('strong').textContent;
    button.setAttribute('aria-label', `${button.textContent} ${label}`);
  }

  function connectDates(entry) {
    const start = entry.querySelector('[data-credit-start]');
    const end = entry.querySelector('[data-credit-end]');
    const updateBounds = () => {
      start.max = end.value;
      end.min = start.value;
    };
    for (const input of [start, end]) {
      input.addEventListener('input', updateBounds);
      input.addEventListener('change', updateBounds);
    }
    updateBounds();
  }

  function register(entry) {
    const key = entry.dataset.creditEntry;
    selected.set(key, entry);
    entry.querySelector('[data-credit-remove]').hidden = false;
    connectDates(entry);
    showChoiceState(key);
  }

  entries.querySelectorAll('[data-credit-entry]').forEach(register);
  for (const button of choices.values()) button.hidden = false;
  updateBasket();

  picker.addEventListener('click', (event) => {
    if (!(event.target instanceof Element)) return;
    const add = event.target.closest('[data-credit-add]');
    if (add) {
      if (selected.has(add.dataset.creditAdd)) return;
      const template = add.closest('.credit-choice').querySelector('template');
      const entry = template.content.firstElementChild.cloneNode(true);
      entries.append(entry);
      register(entry);
      updateBasket();
      feedback.textContent = `已添加 ${entry.querySelector('legend').textContent}。`;
      return;
    }

    const remove = event.target.closest('[data-credit-remove]');
    if (remove) {
      const entry = remove.closest('[data-credit-entry]');
      const next = entry.nextElementSibling || entry.previousElementSibling;
      const key = entry.dataset.creditEntry;
      const label = entry.querySelector('legend').textContent;
      selected.delete(key);
      entry.remove();
      showChoiceState(key);
      updateBasket();
      feedback.textContent = `已移除 ${label}。`;
      (next?.querySelector('[data-credit-remove]') || basketTitle).focus();
    }
  });
})();
