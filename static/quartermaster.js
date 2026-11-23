const catalog = document.getElementById('quartermaster-catalog');
const editor = document.getElementById('stock-editor');
const anchor = document.getElementById(location.hash.slice(1));

if (anchor?.matches('[data-stock-variant]')) {
  anchor.closest('details').open = true;
  anchor.scrollIntoView({block: 'nearest'});
}

if (editor) {
  const form = editor.querySelector('form');
  const recipient = form.elements.recipient_id;
  const recipientField = form.querySelector('[data-stock-recipient]');
  const submit = form.querySelector('[data-stock-submit]');
  let trigger;

  catalog.addEventListener('click', (event) => {
    const button = event.target.closest('[data-stock-action]');
    if (!button) return;

    trigger?.setAttribute('aria-expanded', 'false');
    trigger = button;
    trigger.setAttribute('aria-expanded', 'true');
    const action = button.dataset.stockAction;
    const assigning = action === 'assign';
    form.reset();
    form.elements.variant_id.value = button.closest('[data-stock-variant]').dataset.stockVariant;
    form.elements.action.value = action;
    recipientField.hidden = !assigning;
    recipient.disabled = !assigning;
    recipient.required = assigning;
    submit.textContent = assigning ? 'Assign' : 'Add stock';
    button.closest('td').append(editor);
    editor.hidden = false;
    (assigning ? recipient : form.elements.quantity).focus();
  });

  form.querySelector('[data-stock-cancel]').addEventListener('click', () => {
    editor.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    trigger.focus();
  });
}
