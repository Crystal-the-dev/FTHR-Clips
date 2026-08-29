const categories = [
    {
        id: 'app-failure-codes',
        label: 'App Failure Codes',
        description: 'Codes displayed in the FTHRClips bottom error bar. The message detail contains the incident-specific reason.',
        entries: [
            ['Error 001', 'CAPTURE FAILED', 'Capture', 'Capture health reported a backend failure or the capture startup sequence failed. The detail text contains the reported reason.'],
            ['Error 002', 'CAPTURE UNAVAILABLE', 'Capture', 'A save was requested while the capture engine was not connected, so there was no active replay buffer to address.'],
            ['Error 003', 'ENGINE NOT FOUND', 'Capture', 'The FTHRcapture executable could not be found at the expected install or development path.'],
            ['Error 004', 'ENGINE NOT RESPONDING', 'Capture', 'The capture process was launched but did not complete its IPC startup handshake.'],
            ['Error 005', 'ENGINE COULD NOT START', 'Capture', 'A capture startup attempt failed before a usable engine connection was established.'],
            ['Error 006', 'ENGINE STOPPED', 'Capture', 'The capture process exited unexpectedly. The process exit code is included in the message detail.'],
            ['Error 007', 'MANUAL RECORDING FAILED', 'Recording', 'A manual recording could not be completed or finalized. Recoverable video fragments may be mentioned in the detail.'],
            ['Error 008', 'RECORDING COULD NOT START', 'Recording', 'The manual recording request could not be handed to the capture engine.'],
            ['Error 009', 'RECORDING COULD NOT STOP', 'Recording', 'The capture engine was unavailable when a manual recording stop was requested.'],
            ['Error 010', 'RECORDING FOLDER UNAVAILABLE', 'Recording', 'The configured manual-recording folder could not be created or accessed.'],
            ['Error 011', 'RECORDING PATH TOO LONG', 'Recording', 'The manual-recording output path exceeds the native engine path limit.'],
            ['Error 012', 'NOT ENOUGH DISK SPACE', 'Recording', 'Manual recording was blocked by the preflight free-space threshold.'],
            ['Error 013', 'CLIP NOT SAVED', 'Clip saving', 'Capture health rejected the save because fresh replay data was not currently usable. The detail says whether capture is starting, recovering, or stale.'],
            ['Error 014', 'CLIP SAVE FAILED', 'Clip saving', 'The local clip folder, save command submission, or save setup failed before a completed clip was produced.'],
            ['Error 015', 'CLIP WAS NOT SAVED', 'Clip saving', 'The capture engine reported a terminal save failure, an incomplete path, or a missing/empty output file.'],
            ['Error 016', 'CLIP PATH TOO LONG', 'Clip saving', 'The requested clip path exceeds the Windows shared-memory path field limit.'],
            ['Error 017', 'CLIP FINALIZATION FAILED', 'Clip saving', 'The base clip could not be made available as a completed final file after the save operation.'],
            ['Error 018', 'EXPORT FAILED', 'Export and sharing', 'An edited clip export failed validation, encoding, folder creation, or file publication.'],
            ['Error 019', 'SHARE FAILED', 'Export and sharing', 'The share workflow could not create or publish its exported clip.'],
            ['Error 020', 'UPLOAD FAILED', 'Upload', 'The upload provider rejected or could not complete the upload. The provider or retry detail is passed through when available.'],
            ['Error 021', 'UPLOAD NOT CONFIGURED', 'Upload', 'An upload was requested without an installed and enabled Upload Extension.'],
            ['Error 022', 'UPLOAD CONNECTION FAILED', 'Upload', 'The upload settings Test Connection request failed. The provider response is used as the detail.'],
            ['Error 023', 'COMPRESSION FAILED', 'Upload', 'The optional compressed upload copy could not be created, verified, or brought under the provider size limit.'],
        ],
    },
];

const categoryById = new Map(categories.map((category) => [category.id, category]));
const state = {
    categoryId: categoryById.has(window.location.hash.slice(1))
        ? window.location.hash.slice(1)
        : categories[0].id,
    query: '',
};

const categoryNav = document.querySelector('#categoryNav');
const categoryEyebrow = document.querySelector('#categoryEyebrow');
const pageTitle = document.querySelector('#pageTitle');
const pageDescription = document.querySelector('#pageDescription');
const searchInput = document.querySelector('#searchInput');
const clearSearch = document.querySelector('#clearSearch');
const resultCount = document.querySelector('#resultCount');
const codeList = document.querySelector('#codeList');

function renderCategoryNav() {
    categoryNav.replaceChildren();
    categories.forEach((category) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'category-button';
        button.classList.toggle('is-active', category.id === state.categoryId);
        button.setAttribute('aria-pressed', category.id === state.categoryId ? 'true' : 'false');
        button.append(document.createTextNode(category.label));

        const count = document.createElement('span');
        count.className = 'category-count';
        count.textContent = ` (${category.entries.length})`;
        button.append(count);
        button.addEventListener('click', () => selectCategory(category.id));
        categoryNav.append(button);
    });
}

function selectCategory(categoryId) {
    if (!categoryById.has(categoryId)) return;
    state.categoryId = categoryId;
    state.query = '';
    searchInput.value = '';
    window.history.replaceState(null, '', `#${categoryId}`);
    render();
}

function makeCodeEntry(entry) {
    const [code, title, category, explanation] = entry;
    const article = document.createElement('article');
    article.className = 'code-entry';

    const codeValue = document.createElement('code');
    codeValue.className = 'code-value';
    codeValue.textContent = code;
    article.append(codeValue);

    const copy = document.createElement('div');
    copy.className = 'code-copy';
    const heading = document.createElement('h2');
    heading.className = 'code-title';
    heading.textContent = title;
    copy.append(heading);

    const categoryLabel = document.createElement('p');
    categoryLabel.className = 'eyebrow';
    categoryLabel.textContent = category;
    copy.prepend(categoryLabel);

    const detail = document.createElement('p');
    detail.className = 'code-explanation';
    detail.textContent = explanation;
    copy.append(detail);
    article.append(copy);

    const copyButton = document.createElement('button');
    copyButton.type = 'button';
    copyButton.className = 'copy-code';
    copyButton.textContent = 'Copy';
    copyButton.setAttribute('aria-label', `Copy ${code}`);
    copyButton.addEventListener('click', () => copyCode(code, copyButton));
    article.append(copyButton);
    return article;
}

async function copyCode(code, button) {
    try {
        await navigator.clipboard.writeText(code);
    } catch (_error) {
        const helper = document.createElement('textarea');
        helper.value = code;
        helper.setAttribute('readonly', '');
        helper.style.position = 'fixed';
        helper.style.opacity = '0';
        document.body.append(helper);
        helper.select();
        document.execCommand('copy');
        helper.remove();
    }
    const original = button.textContent;
    button.textContent = 'Copied';
    window.setTimeout(() => { button.textContent = original; }, 1200);
}

function render() {
    const category = categoryById.get(state.categoryId);
    const query = state.query.trim().toLowerCase();
    const entries = category.entries.filter((entry) => (
        !query || entry.join(' ').toLowerCase().includes(query)
    ));

    categoryEyebrow.textContent = category.label;
    pageTitle.textContent = category.label;
    pageDescription.textContent = category.description;
    resultCount.textContent = query
        ? `${entries.length} of ${category.entries.length} entries`
        : `${category.entries.length} entr${category.entries.length === 1 ? 'y' : 'ies'}`;
    clearSearch.hidden = !state.query;
    codeList.replaceChildren();

    if (!entries.length) {
        const empty = document.createElement('p');
        empty.className = 'empty-state';
        empty.textContent = category.entries.length
            ? 'No entries match this filter.'
            : 'No entries have been documented in this category yet.';
        codeList.append(empty);
    } else {
        entries.forEach((entry) => codeList.append(makeCodeEntry(entry)));
    }
    renderCategoryNav();
}

searchInput.addEventListener('input', (event) => {
    state.query = event.target.value;
    render();
});

clearSearch.addEventListener('click', () => {
    state.query = '';
    searchInput.value = '';
    render();
    searchInput.focus();
});

render();
