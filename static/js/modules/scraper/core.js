const SCRAPER_JOB_ACTIVE_STATUSES = new Set(['pending', 'running', 'rollback_running']);
const SCRAPER_PROVIDER_LABELS = { '115': '115', quark: '夸克' };

const state = {
    initialized: false,
    provider: '115',
    providers: [],
    cid: '0',
    trail: [{ id: '0', name: '根目录' }],
    entries: [],
    summary: { folder_count: 0, file_count: 0 },
    selected: new Map(),
    entrySort: { key: 'name', direction: 'asc' },
    search: '',
    loading: false,
    navigationBusy: false,
    moveBuffer: null,
    identifyBusy: false,
    identifyResult: null,
    identifySelectionKey: '',
    tmdb: null,
    manualBusy: false,
    manualResults: [],
    planBusy: false,
    plan: null,
    planSelections: new Set(),
    executeBusy: false,
    jobs: [],
    jobsBusy: false,
    jobsPollTimer: 0,
};

function $(id) {
    return document.getElementById(id);
}

function escapeHtml(value) {
    if (typeof window.escapeHtml === 'function') return window.escapeHtml(value);
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function showToast(message, options = {}) {
    if (typeof window.showToast === 'function') {
        window.showToast(message, options);
        return;
    }
    console.log(message);
}

async function showConfirm(message, options = {}) {
    if (typeof window.showAppConfirm === 'function') {
        return window.showAppConfirm(message, options);
    }
    return window.confirm(message);
}

function normalizeProvider(value) {
    const raw = String(value || '').trim().toLowerCase();
    return raw === 'quark' || raw === '夸克' ? 'quark' : '115';
}

function getProviderLabel(provider = state.provider) {
    return SCRAPER_PROVIDER_LABELS[normalizeProvider(provider)] || '115';
}

function isProviderConfigured(provider = state.provider) {
    const normalized = normalizeProvider(provider);
    const item = state.providers.find(providerInfo => normalizeProvider(providerInfo.provider) === normalized);
    return !!item?.configured;
}

function normalizeCid(value) {
    return String(value || '0').trim() || '0';
}

function normalizePath(value) {
    return String(value || '')
        .split(/[\\/]+/)
        .map(part => part.trim())
        .filter(Boolean)
        .join('/');
}

function joinPath(...parts) {
    return normalizePath(parts.join('/'));
}

function currentParentPath() {
    return normalizePath(state.trail.slice(1).map(item => item.name || '').join('/'));
}

function formatFileSize(size) {
    const value = Number(size || 0);
    if (!Number.isFinite(value) || value <= 0) return '--';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let next = value;
    let unit = 0;
    while (next >= 1024 && unit < units.length - 1) {
        next /= 1024;
        unit += 1;
    }
    return `${next.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function formatDateMinute(date) {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) return '';
    const pad = value => String(value).padStart(2, '0');
    return [
        date.getFullYear(),
        pad(date.getMonth() + 1),
        pad(date.getDate()),
    ].join('-') + ` ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatTimeText(value) {
    const text = String(value || '').trim();
    if (!text) return '--';
    if (/^\d{10,17}$/.test(text)) {
        const numeric = Number(text);
        if (Number.isFinite(numeric)) {
            const timestamp = text.length === 10 ? numeric * 1000 : numeric;
            const formatted = formatDateMinute(new Date(timestamp));
            if (formatted) return formatted;
        }
    }
    const parsed = Date.parse(text);
    if (Number.isFinite(parsed)) {
        const formatted = formatDateMinute(new Date(parsed));
        if (formatted) return formatted;
    }
    return text.replace('T', ' ').slice(0, 16);
}

function parseEntryModifiedMs(value) {
    const text = String(value || '').trim();
    if (!text) return 0;
    if (/^\d{10,17}$/.test(text)) {
        const numeric = Number(text);
        if (Number.isFinite(numeric)) return text.length === 10 ? numeric * 1000 : numeric;
    }
    const parsed = Date.parse(text);
    return Number.isFinite(parsed) ? parsed : 0;
}

function getEntryIcon(isDir) {
    if (isDir) {
        return '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 8.5C4 7.67 4.67 7 5.5 7H9L10.5 8.5H18.5C19.33 8.5 20 9.17 20 10V16.5C20 17.33 19.33 18 18.5 18H5.5C4.67 18 4 17.33 4 16.5V8.5Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>';
    }
    return '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M7 4.5H14L18 8.5V19.5H7V4.5Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M14 4.5V8.5H18" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>';
}

function enrichEntry(entry) {
    const item = entry && typeof entry === 'object' ? entry : {};
    const id = String(item.id || item.cid || item.fid || '').trim();
    const name = String(item.name || '').trim();
    const parentPath = currentParentPath();
    const itemPath = normalizePath(item.path || '');
    const combinedPath = normalizePath(joinPath(parentPath, name));
    const path = itemPath && (itemPath.includes('/') || !parentPath) ? itemPath : combinedPath;
    return {
        ...item,
        id,
        name,
        parent_id: normalizeCid(item.parent_id || state.cid),
        parent_path: parentPath,
        path,
        is_dir: !!item.is_dir,
        size: Number(item.size || 0) || 0,
    };
}

function getSelectedEntries() {
    return Array.from(state.selected.values())
        .filter(item => item && item.id && item.name)
        .map(item => ({ ...item }));
}

function getSelectionPath(entry = {}) {
    const item = entry && typeof entry === 'object' ? entry : {};
    return normalizePath(item.path || joinPath(item.parent_path || '', item.name || ''));
}

function getSelectionParentPath(entry = {}) {
    const item = entry && typeof entry === 'object' ? entry : {};
    return normalizePath(item.parent_path || currentParentPath());
}

function isSelectionPathCovered(path, ancestorPath) {
    const normalizedPath = normalizePath(path);
    const normalizedAncestor = normalizePath(ancestorPath);
    if (!normalizedPath || !normalizedAncestor) return false;
    return normalizedPath === normalizedAncestor || normalizedPath.startsWith(`${normalizedAncestor}/`);
}

function getEffectiveSelectedEntries(entries = getSelectedEntries()) {
    const items = Array.isArray(entries) ? entries : [];
    const normalized = items
        .filter(item => item && item.id && item.name)
        .map(item => ({
            ...item,
            path: getSelectionPath(item),
        }))
        .sort((a, b) => {
            const depthA = getSelectionPath(a).split('/').filter(Boolean).length;
            const depthB = getSelectionPath(b).split('/').filter(Boolean).length;
            if (depthA !== depthB) return depthA - depthB;
            if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
            const pathCompare = String(a.path || '').localeCompare(String(b.path || ''), 'zh-Hans-CN');
            if (pathCompare !== 0) return pathCompare;
            return String(a.id || '').localeCompare(String(b.id || ''));
        });
    const selected = [];
    const seenIds = new Set();
    const seenPaths = new Set();
    normalized.forEach(item => {
        const id = String(item.id || '').trim();
        const path = normalizePath(item.path || '');
        if (!id || !path) return;
        if (seenIds.has(id) || seenPaths.has(path)) return;
        if (selected.some(ancestor => ancestor.is_dir && isSelectionPathCovered(path, ancestor.path))) return;
        selected.push({ ...item, path });
        seenIds.add(id);
        seenPaths.add(path);
    });
    return selected;
}

function getSelectionKey(entries = getSelectedEntries()) {
    return getEffectiveSelectedEntries(entries)
        .map(item => `${item.is_dir ? 'd' : 'f'}:${item.id}`)
        .sort()
        .join('|');
}

function getSelectionMode(entries = getSelectedEntries()) {
    const items = getEffectiveSelectedEntries(entries);
    return items.length === 1 && !!items[0]?.is_dir ? 'folder' : 'contents';
}

function isWholeFolderSelection(entries = getSelectedEntries()) {
    return getSelectionMode(entries) === 'folder';
}

function resetIdentifyContext({ resetInputs = false } = {}) {
    state.identifyResult = null;
    state.identifySelectionKey = '';
    state.tmdb = null;
    state.manualResults = [];
    if (resetInputs) {
        const manualInput = $('scraper-manual-query');
        if (manualInput) manualInput.value = '';
        const mediaSelect = $('scraper-manual-media-type');
        if (mediaSelect) mediaSelect.value = 'movie';
    }
}

function clearSelection() {
    state.selected.clear();
}

function clearPlan() {
    state.plan = null;
    state.planSelections.clear();
    renderPlan();
    renderEntries();
}

function invalidateSelectionContext() {
    resetIdentifyContext({ resetInputs: true });
    closeIdentifyPanel();
    clearPlan();
}

function openIdentifyPanel() {
    const panel = $('scraper-identify-panel');
    if (!panel) return;
    panel.classList.remove('hidden');
    panel.classList.add('is-open');
    setTimeout(() => $('scraper-manual-query')?.focus(), 30);
}

function closeIdentifyPanel() {
    const panel = $('scraper-identify-panel');
    if (!panel) return;
    panel.classList.add('hidden');
    panel.classList.remove('is-open');
}

function setBusyButton(button, busy, busyText = '处理中...', idleText = '') {
    if (!button) return;
    button.disabled = !!busy;
    button.classList.toggle('btn-disabled', !!busy);
    if (busy) {
        button.dataset.idleText = button.textContent || idleText;
        button.textContent = busyText;
    } else if (button.dataset.idleText) {
        button.textContent = button.dataset.idleText;
        delete button.dataset.idleText;
    }
}

function scrollScraperToTop() {
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function syncScraperBackTopButton() {
    const btn = $('scraper-back-top-btn');
    const page = $('page-scraper');
    if (!btn || !page) return;
    const isVisible = !page.classList.contains('hidden');
    const isModalLocked = document.body.classList.contains('body-scroll-lock');
    const scrollTop = Math.max(0, window.scrollY || window.pageYOffset || 0);
    const shouldShow = isVisible && !isModalLocked && scrollTop > 360;
    btn.classList.toggle('hidden', !shouldShow);
}

async function promptText({ title = '输入名称', message = '', defaultValue = '', confirmText = '确认' } = {}) {
    return new Promise((resolve) => {
        const modal = document.createElement('div');
        modal.className = 'scraper-prompt-modal';
        modal.innerHTML = `
            <div class="scraper-prompt-shell" role="dialog" aria-modal="true">
                <div class="scraper-prompt-title">${escapeHtml(title)}</div>
                ${message ? `<div class="scraper-prompt-message">${escapeHtml(message)}</div>` : ''}
                <input class="scraper-input scraper-prompt-input" value="${escapeHtml(defaultValue)}">
                <div class="scraper-prompt-actions">
                    <button type="button" class="scraper-compact-btn" data-prompt-cancel>取消</button>
                    <button type="button" class="scraper-compact-btn scraper-primary-soft" data-prompt-confirm>${escapeHtml(confirmText)}</button>
                </div>
            </div>
        `;
        const input = modal.querySelector('.scraper-prompt-input');
        const cleanup = (value) => {
            document.removeEventListener('keydown', onKeydown);
            modal.remove();
            resolve(value);
        };
        const onKeydown = (event) => {
            if (event.key === 'Escape') cleanup(null);
            if (event.key === 'Enter' && !event.isComposing) cleanup(String(input.value || '').trim());
        };
        modal.addEventListener('click', (event) => {
            if (event.target === modal || event.target.closest('[data-prompt-cancel]')) cleanup(null);
            if (event.target.closest('[data-prompt-confirm]')) cleanup(String(input.value || '').trim());
        });
        document.addEventListener('keydown', onKeydown);
        document.body.appendChild(modal);
        setTimeout(() => {
            input.focus();
            input.select();
        }, 20);
    });
}

function renderProviderTabs() {
    const container = $('scraper-provider-tabs');
    if (!container) return;
    const providers = state.providers.length
        ? state.providers
        : [
            { provider: '115', label: '115', configured: false },
            { provider: 'quark', label: '夸克', configured: false },
        ];
    container.innerHTML = providers.map((item) => {
        const provider = normalizeProvider(item.provider);
        const active = provider === state.provider;
        const configured = !!item.configured;
        return `
            <button
                type="button"
                class="scraper-provider-tab ${active ? 'is-active' : ''} ${configured ? '' : 'is-muted'}"
                data-scraper-provider="${escapeHtml(provider)}"
                aria-pressed="${active ? 'true' : 'false'}"
            >
                <span>${escapeHtml(item.label || getProviderLabel(provider))}</span>
                <small>${configured ? '已配置' : '未配置'}</small>
            </button>
        `;
    }).join('');
}

function renderProviderStatus() {
    const el = $('scraper-provider-status');
    if (!el) return;
    const providerLabel = getProviderLabel();
    if (!isProviderConfigured()) {
        el.textContent = `${providerLabel} Cookie 未配置，文件管理和刮削执行暂不可用。`;
        return;
    }
    const folderCount = Number(state.summary.folder_count || 0);
    const fileCount = Number(state.summary.file_count || 0);
    el.textContent = `${providerLabel} / 当前目录 ${folderCount} 个文件夹、${fileCount} 个文件`;
}

function renderBreadcrumbs() {
    const container = $('scraper-breadcrumbs');
    if (!container) return;
    container.innerHTML = state.trail.map((item, index) => {
        const active = index === state.trail.length - 1;
        const separator = index > 0 ? '<span class="scraper-breadcrumb-sep">/</span>' : '';
        if (active) {
            return `${separator}<span class="scraper-breadcrumb is-active">${escapeHtml(item.name || '根目录')}</span>`;
        }
        return `${separator}<button type="button" class="scraper-breadcrumb" data-scraper-trail-index="${index}">${escapeHtml(item.name || '根目录')}</button>`;
    }).join('');
}

function getPlanActions() {
    return state.plan && Array.isArray(state.plan.actions) ? state.plan.actions : [];
}

function getSelectedReadyPlanCount() {
    return getPlanActions().filter(action => action.ready && state.planSelections.has(Number(action.action_index || 0))).length;
}

function renderSelection() {
    const countEl = $('scraper-selection-count');
    const selectedEntries = getEffectiveSelectedEntries();
    const planActions = getPlanActions();
    const selectedReadyCount = getSelectedReadyPlanCount();
    const hasPlan = planActions.length > 0;
    const hasBinding = !!(state.tmdb && Number(state.tmdb.tmdb_id || state.tmdb.id || 0) > 0);
    if (countEl) {
        if (hasPlan) {
            const readyCount = planActions.filter(action => action.ready).length;
            countEl.textContent = `预览 ${planActions.length} 项 / 可执行 ${readyCount} 项 / 已勾选 ${selectedReadyCount} 项`;
        } else {
            const count = selectedEntries.length;
            if (!count) {
                countEl.textContent = '未选择条目';
            } else if (isWholeFolderSelection(selectedEntries)) {
                countEl.textContent = `已选择整个文件夹：${selectedEntries[0].name || '--'}`;
            } else {
                const hasFolder = selectedEntries.some(item => item.is_dir);
                countEl.textContent = `已选择 ${count} 项 · 内容模式（只改文件名${hasFolder ? '，含子文件夹内文件' : ''}）`;
            }
        }
    }
    const bindingBtn = $('scraper-bound-media-btn');
    if (bindingBtn) {
        bindingBtn.classList.toggle('hidden', !hasBinding);
        bindingBtn.disabled = !hasBinding;
        bindingBtn.textContent = hasBinding ? `已绑定：${getTmdbDisplayTitle()}` : '';
    }
    const checkAll = $('scraper-check-all');
    if (checkAll) {
        const selectable = state.entries;
        const selectedInCurrent = selectable.filter(item => state.selected.has(item.id)).length;
        checkAll.checked = selectable.length > 0 && selectedInCurrent === selectable.length;
        checkAll.indeterminate = selectedInCurrent > 0 && selectedInCurrent < selectable.length;
        checkAll.disabled = state.loading || selectable.length <= 0;
    }
    const hasSelection = selectedEntries.length > 0;
    const selectedSingleFolder = isWholeFolderSelection(selectedEntries);
    const selectedInCurrent = state.entries.filter(item => state.selected.has(item.id)).length;
    const renameButton = document.querySelector('[data-scraper-action="rename-selected"]');
    if (renameButton) {
        const showRename = selectedSingleFolder && !hasPlan;
        renameButton.classList.toggle('hidden', !showRename);
        renameButton.disabled = state.loading || !showRename;
        renameButton.classList.toggle('btn-disabled', state.loading || !showRename);
        renameButton.textContent = '重命名文件夹';
        renameButton.title = showRename ? '仅在选中整个剧集文件夹时可用' : '仅在选中整个剧集文件夹时可用';
    }
    const actionRules = {
        'select-range': !hasPlan && selectedInCurrent >= 2,
        'prepare-move': hasSelection,
        'delete-selected': hasSelection,
        identify: hasSelection,
    };
    Object.entries(actionRules).forEach(([action, enabled]) => {
        const button = document.querySelector(`[data-scraper-action="${action}"]`);
        if (!button) return;
        button.disabled = state.loading || !enabled;
        button.classList.toggle('btn-disabled', state.loading || !enabled);
        if (action === 'identify') button.textContent = hasBinding ? '修改识别' : '识别';
    });
    const clearPlanBtn = $('scraper-clear-plan-btn');
    if (clearPlanBtn) {
        clearPlanBtn.classList.toggle('hidden', !hasPlan);
        clearPlanBtn.disabled = !hasPlan || state.executeBusy;
        clearPlanBtn.classList.toggle('btn-disabled', !hasPlan || state.executeBusy);
    }
    const inlineExecuteBtn = $('scraper-inline-execute-btn');
    if (inlineExecuteBtn) {
        const showExecute = hasPlan;
        inlineExecuteBtn.classList.toggle('hidden', !showExecute);
        inlineExecuteBtn.disabled = state.executeBusy || selectedReadyCount <= 0;
        inlineExecuteBtn.classList.toggle('btn-disabled', state.executeBusy || selectedReadyCount <= 0);
        inlineExecuteBtn.textContent = state.executeBusy ? '提交中...' : `执行重命名 ${selectedReadyCount} 项`;
    }
    syncFolderScopedControls();
    syncBuildPlanControls();
}

function syncBuildPlanControls() {
    const selectedEntries = getEffectiveSelectedEntries();
    const hasBinding = !!(state.tmdb && Number(state.tmdb.tmdb_id || state.tmdb.id || 0) > 0);
    const hasPlan = getPlanActions().length > 0;
    const showBuild = selectedEntries.length > 0 && hasBinding && !hasPlan;
    const inlineBuildBtn = $('scraper-inline-build-plan-btn');
    if (inlineBuildBtn) {
        inlineBuildBtn.classList.toggle('hidden', !showBuild);
        inlineBuildBtn.disabled = state.loading || state.planBusy || !showBuild;
        inlineBuildBtn.classList.toggle('btn-disabled', state.loading || state.planBusy || !showBuild);
        inlineBuildBtn.textContent = state.planBusy ? '识别中...' : '生成预览';
    }
    const buildBtn = $('scraper-build-plan-btn');
    if (buildBtn) {
        buildBtn.disabled = state.loading || state.planBusy || !showBuild;
        buildBtn.classList.toggle('btn-disabled', state.loading || state.planBusy || !showBuild);
        buildBtn.textContent = state.planBusy ? '识别中...' : '生成预览';
        buildBtn.title = state.planBusy
            ? '正在识别文件并生成预览'
            : (showBuild ? '生成命名预览' : '请先选择要刮削的文件或文件夹');
    }
}

function renderMoveBuffer() {
    const el = $('scraper-move-buffer');
    if (!el) return;
    const buffer = state.moveBuffer;
    if (!buffer || !Array.isArray(buffer.entries) || buffer.entries.length <= 0) {
        el.classList.add('hidden');
        el.innerHTML = '';
        return;
    }
    el.classList.remove('hidden');
    el.innerHTML = `
        <div>
            <strong>待移动 ${escapeHtml(String(buffer.entries.length))} 项</strong>
            <span>来源：${escapeHtml(buffer.source_path || '根目录')}</span>
        </div>
        <div class="scraper-move-actions">
            <button type="button" class="scraper-compact-btn scraper-primary-soft" data-scraper-action="move-here">移动到当前目录</button>
            <button type="button" class="scraper-compact-btn" data-scraper-action="clear-move">取消</button>
        </div>
    `;
}

function renderEntries() {
    const list = $('scraper-entry-list');
    if (!list) return;
    renderProviderStatus();
    renderBreadcrumbs();
    renderSelection();
    renderMoveBuffer();
    const refreshBtn = $('scraper-refresh-btn');
    if (refreshBtn) {
        refreshBtn.disabled = !!state.loading;
        refreshBtn.classList.toggle('btn-disabled', !!state.loading);
    }
    const header = document.querySelector('.scraper-entry-header');
    const table = document.querySelector('.scraper-entry-table');
    const planActions = getPlanActions();
    table?.classList.toggle('is-preview-mode', planActions.length > 0);
    if (planActions.length) {
        if (header) {
            header.innerHTML = `
                <div>原文件</div>
                <span>新名称预览</span>
                <span>执行</span>
            `;
        }
        list.innerHTML = planActions.map((action) => {
            const actionIndex = Number(action.action_index || 0) || 0;
            const checked = action.ready && state.planSelections.has(actionIndex);
            const readyClass = action.ready ? 'is-ready' : 'is-blocked';
            const statusText = action.ready ? '可执行' : '需处理';
            const oldPath = String(action.old_path || action.old_name || '--');
            const newPath = String(action.new_path || action.new_name || '--');
            return `
                <div class="scraper-preview-row ${readyClass}">
                    <div class="scraper-preview-original">
                        <span class="scraper-plan-badge">${action.is_dir ? '文件夹' : '文件'}</span>
                        <strong title="${escapeHtml(oldPath)}">${escapeHtml(action.old_name || '--')}</strong>
                    </div>
                    <div class="scraper-preview-target">
                        <div class="scraper-preview-target-head">
                            <span class="scraper-preview-status ${action.ready ? 'is-ok' : 'is-warn'}">${statusText}</span>
                            <strong title="${escapeHtml(newPath)}">${escapeHtml(action.new_name || '--')}</strong>
                        </div>
                        ${action.issue ? `<em>${escapeHtml(action.issue)}</em>` : ''}
                        ${action.warning ? `<em class="scraper-plan-warning">${escapeHtml(action.warning)}</em>` : ''}
                    </div>
                    <label class="scraper-preview-check">
                        <input type="checkbox" class="ui-checkbox ui-checkbox-sm" data-scraper-plan-check="${escapeHtml(String(actionIndex))}" ${checked ? 'checked' : ''} ${action.ready ? '' : 'disabled'}>
                        <span>${action.ready ? '执行' : '不可执行'}</span>
                    </label>
                </div>
            `;
        }).join('');
        return;
    }
    if (header) {
        header.innerHTML = `
            <div class="scraper-entry-name-cell">
                <input id="scraper-check-all" type="checkbox" class="ui-checkbox ui-checkbox-sm" aria-label="选择当前目录全部条目">
                ${renderSortButton('name', '名称')}
            </div>
            ${renderSortButton('modified_at', '修改时间')}
            ${renderSortButton('size', '大小')}
        `;
        renderSelection();
    }
    if (state.loading && !state.entries.length) {
        list.innerHTML = `<div class="scraper-empty-row">正在读取${escapeHtml(getProviderLabel())}目录...</div>`;
        return;
    }
    if (!isProviderConfigured()) {
        list.innerHTML = `<div class="scraper-empty-row">请先到参数配置填写 ${escapeHtml(getProviderLabel())} Cookie。</div>`;
        return;
    }
    if (!state.entries.length) {
        list.innerHTML = '<div class="scraper-empty-row">当前目录没有可显示条目。</div>';
        return;
    }
    const rows = getDisplayEntries().map((entry) => {
        const selected = state.selected.has(entry.id);
        const entryTitle = escapeHtml(entry.path || entry.name || '');
        const entryName = escapeHtml(entry.name || '--');
        const nameHtml = entry.is_dir
            ? `<button type="button" class="scraper-entry-link" data-scraper-entry-enter="${escapeHtml(entry.id)}" title="${entryTitle}" ${state.loading || state.navigationBusy ? 'disabled' : ''}>${entryName}</button>`
            : `<span class="scraper-entry-filename" title="${entryTitle}">${entryName}</span>`;
        return `
            <div class="scraper-entry-row ${selected ? 'is-selected' : ''}" data-scraper-entry-id="${escapeHtml(entry.id)}">
                <div class="scraper-entry-name-cell">
                    <input type="checkbox" class="ui-checkbox ui-checkbox-sm" data-scraper-check="${escapeHtml(entry.id)}" ${selected ? 'checked' : ''}>
                    <span class="scraper-entry-icon ${entry.is_dir ? 'is-folder' : 'is-file'}">${getEntryIcon(entry.is_dir)}</span>
                    <div class="scraper-entry-main">
                        ${nameHtml}
                    </div>
                </div>
                <span>${escapeHtml(formatTimeText(entry.modified_at))}</span>
                <span>${entry.is_dir ? '--' : escapeHtml(formatFileSize(entry.size))}</span>
            </div>
        `;
    });
    list.innerHTML = rows.join('');
}

function getTmdbDisplayTitle(binding = state.tmdb) {
    const item = binding && typeof binding === 'object' ? binding : {};
    const title = item.tmdb_title || item.title || item.tmdb_localized_title || item.tmdb_english_title || '';
    const year = item.tmdb_year || item.year || '';
    return `${title || '--'}${year ? ` (${year})` : ''}`;
}

function getBoundTmdbKey(binding = state.tmdb) {
    const item = binding && typeof binding === 'object' ? binding : {};
    const id = Number(item.tmdb_id || item.id || 0) || 0;
    if (id <= 0) return '';
    const mediaType = (item.tmdb_media_type || item.media_type) === 'tv' ? 'tv' : 'movie';
    return `${mediaType}:${id}`;
}

function getManualResultTmdbKey(item = {}) {
    const id = Number(item.id || item.tmdb_id || 0) || 0;
    if (id <= 0) return '';
    const mediaType = item.media_type === 'tv' ? 'tv' : 'movie';
    return `${mediaType}:${id}`;
}

function getTmdbSeasonCount(binding = state.tmdb) {
    const item = binding && typeof binding === 'object' ? binding : {};
    const map = item.tmdb_season_episode_map || item.season_episode_map || {};
    const mapSeasons = map && typeof map === 'object'
        ? Object.keys(map).map(value => Number(value || 0)).filter(value => value > 0)
        : [];
    return Math.max(0, Number(item.tmdb_total_seasons || item.total_seasons || 0) || 0, ...mapSeasons);
}

function getSelectedEpisodeModeLabel(mode) {
    const normalized = String(mode || 'auto').trim();
    if (normalized === 'absolute') return '多季：按 TMDB 连续编号';
    if (normalized === 'seasonal') return '单季：按季号识别';
    return '自动跟随 TMDB';
}

function getEffectiveEpisodeModeLabel() {
    const selected = String($('scraper-episode-mode')?.value || 'auto').trim();
    if (selected !== 'auto') return getSelectedEpisodeModeLabel(selected);
    const tmdbMode = String(state.tmdb?.tmdb_episode_mode || '').trim();
    return tmdbMode ? `TMDB 推荐：${getSelectedEpisodeModeLabel(tmdbMode)}` : '自动跟随 TMDB';
}

function syncSeasonControl() {
    const field = $('scraper-season-field');
    const input = $('scraper-season');
    if (!field || !input) return;
    const isTv = (state.tmdb?.tmdb_media_type || state.tmdb?.media_type || $('scraper-manual-media-type')?.value) === 'tv';
    field.classList.toggle('hidden', !isTv);
    if (!isTv) return;
    const seasonCount = getTmdbSeasonCount();
    const current = Math.max(1, Number(input.value || 1) || 1);
    if (seasonCount > 0) {
        input.max = String(seasonCount);
        if (current > seasonCount) input.value = String(seasonCount);
    } else {
        input.max = '99';
    }
}

function syncEpisodeModeControl() {
    const field = $('scraper-episode-mode-field');
    const input = $('scraper-episode-mode');
    if (!field || !input) return;
    const isTv = (state.tmdb?.tmdb_media_type || state.tmdb?.media_type || $('scraper-manual-media-type')?.value) === 'tv';
    field.classList.toggle('hidden', !isTv);
    if (!isTv) return;
    if (!['auto', 'seasonal', 'absolute'].includes(String(input.value || ''))) {
        input.value = 'auto';
    }
}

function syncFileInfoControls() {
    const enabled = !!$('scraper-preserve-file-info')?.checked;
    document.querySelectorAll('[data-scraper-tag]').forEach(input => {
        input.disabled = !enabled;
        input.closest('label')?.classList.toggle('is-disabled', !enabled);
    });
}

function syncIncludeTmdbIdControl() {
    const input = $('scraper-include-tmdb-id');
    const label = input?.closest('label');
    if (!input) return;
    const wholeFolderMode = isWholeFolderSelection();
    input.disabled = !wholeFolderMode;
    if (!wholeFolderMode) {
        if (input.checked) {
            input.dataset.autoDisabled = '1';
        }
        input.checked = false;
    } else if (input.dataset.autoDisabled === '1') {
        input.checked = true;
        delete input.dataset.autoDisabled;
    }
    label?.classList.toggle('is-disabled', !wholeFolderMode);
    if (label) {
        label.title = wholeFolderMode ? '' : '仅在选中整个剧集文件夹时可用';
    }
}

function syncSeasonSubfolderControl() {
    const input = $('scraper-use-season-subfolder');
    const label = $('scraper-use-season-subfolder-wrap');
    if (!input) return;
    const wholeFolderMode = isWholeFolderSelection();
    input.disabled = !wholeFolderMode;
    if (!wholeFolderMode) {
        input.checked = false;
        input.dataset.autoDisabled = '1';
    } else if (input.dataset.autoDisabled === '1') {
        input.checked = true;
        delete input.dataset.autoDisabled;
    }
    label?.classList.toggle('is-disabled', !wholeFolderMode);
    if (label) {
        label.title = wholeFolderMode ? '' : '仅在选中整个剧集文件夹时可用';
    }
}

function syncFolderRenameControl() {
    const input = $('scraper-rename-selected-folders');
    const label = $('scraper-rename-selected-folders-wrap');
    if (!input) return;
    const wholeFolderMode = isWholeFolderSelection();
    input.disabled = !wholeFolderMode;
    if (!wholeFolderMode) {
        input.checked = false;
        input.dataset.autoDisabled = '1';
    } else if (input.dataset.autoDisabled === '1') {
        input.checked = true;
        delete input.dataset.autoDisabled;
    }
    label?.classList.toggle('is-disabled', !wholeFolderMode);
    if (label) {
        label.title = wholeFolderMode
            ? '仅在选中整个剧集文件夹时可用。'
            : '仅在选中整个剧集文件夹时可用';
    }
}

function syncFolderScopedControls() {
    syncIncludeTmdbIdControl();
    syncSeasonSubfolderControl();
    syncFolderRenameControl();
}

function getDisplayEntries() {
    const sortKey = ['name', 'size', 'modified_at'].includes(state.entrySort?.key) ? state.entrySort.key : 'name';
    const direction = state.entrySort?.direction === 'desc' ? -1 : 1;
    return state.entries.slice().sort((a, b) => {
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
        let result = 0;
        if (sortKey === 'size') {
            result = (Number(a.size || 0) || 0) - (Number(b.size || 0) || 0);
        } else if (sortKey === 'modified_at') {
            result = parseEntryModifiedMs(a.modified_at) - parseEntryModifiedMs(b.modified_at);
        }
        if (result === 0) {
            result = String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN');
        }
        return result * direction;
    });
}

function renderSortButton(key, label) {
    const active = state.entrySort?.key === key;
    const direction = active && state.entrySort?.direction === 'desc' ? 'desc' : 'asc';
    const nextDirection = active && direction === 'asc' ? 'desc' : 'asc';
    const indicator = active ? (direction === 'asc' ? '↑' : '↓') : '';
    return `
        <button
            type="button"
            class="scraper-sort-button ${active ? 'is-active' : ''}"
            data-scraper-sort="${escapeHtml(key)}"
            aria-label="按${escapeHtml(label)}${nextDirection === 'asc' ? '升序' : '降序'}排序"
            aria-pressed="${active ? 'true' : 'false'}"
        >
            <span>${escapeHtml(label)}</span>
            <span class="scraper-sort-indicator" aria-hidden="true">${escapeHtml(indicator)}</span>
        </button>
    `;
}

function setEntrySort(key) {
    const normalized = ['name', 'size', 'modified_at'].includes(String(key || '')) ? String(key) : 'name';
    if (state.entrySort.key === normalized) {
        state.entrySort = {
            key: normalized,
            direction: state.entrySort.direction === 'asc' ? 'desc' : 'asc',
        };
    } else {
        state.entrySort = { key: normalized, direction: 'asc' };
    }
    renderEntries();
}

function renderKeywordSuggestions(identifyResult = {}) {
    const keywords = Array.isArray(identifyResult.keywords) ? identifyResult.keywords : [];
    if (!keywords.length) return '';
    return `
        <div class="scraper-keyword-group">
            ${keywords.slice(0, 5).map(item => `
                <button
                    type="button"
                    class="scraper-keyword-chip"
                    data-scraper-keyword="${escapeHtml(item.keyword || '')}"
                    title="${escapeHtml(item.source || '识别关键词')}"
                >
                    ${escapeHtml(item.keyword || '--')}
                </button>
            `).join('')}
        </div>
    `;
}

function renderPoster(item = {}) {
    const posterUrl = String(item.poster_url || '').trim();
    if (posterUrl) {
        return `<img class="scraper-result-poster" src="${escapeHtml(posterUrl)}" alt="" loading="lazy">`;
    }
    return '<div class="scraper-result-poster is-empty">无封面</div>';
}

function renderIdentify() {
    const summary = $('scraper-identify-summary');
    const candidates = $('scraper-candidate-list');
    const manualResults = $('scraper-manual-results');
    const identifyResult = state.identifyResult || {};
    if (summary) {
        if (state.identifyBusy) {
            summary.textContent = '正在识别 TMDB 信息...';
        } else if (state.tmdb) {
            const typeLabel = (state.tmdb.tmdb_media_type || state.tmdb.media_type) === 'tv' ? '电视剧' : '电影';
            const seasonText = typeLabel === '电视剧' ? ` / 第 ${escapeHtml(String(Math.max(1, Number($('scraper-season')?.value || 1) || 1)))} 季 / ${escapeHtml(getEffectiveEpisodeModeLabel())}` : '';
            summary.innerHTML = `已绑定 <strong>${escapeHtml(typeLabel)} #${escapeHtml(String(state.tmdb.tmdb_id || state.tmdb.id || 0))}</strong>：${escapeHtml(getTmdbDisplayTitle())}${seasonText}`;
        } else if (identifyResult.msg) {
            summary.textContent = identifyResult.msg;
        } else if (identifyResult.query) {
            summary.textContent = `已根据选中条目推荐关键词，请点击关键词填入 TMDB 搜索框后手动搜索绑定。`;
        } else {
            summary.textContent = '等待选择文件或文件夹。';
        }
    }
    if (candidates) {
        if (state.identifyBusy) {
            candidates.innerHTML = '<div class="scraper-empty-small">识别中...</div>';
        } else if (!Array.isArray(identifyResult.keywords) || !identifyResult.keywords.length) {
            candidates.innerHTML = '';
        } else {
            candidates.innerHTML = renderKeywordSuggestions(identifyResult);
        }
    }
    if (manualResults) {
        if (state.manualBusy) {
            manualResults.innerHTML = '<div class="scraper-empty-small">搜索中...</div>';
        } else if (!state.manualResults.length) {
            manualResults.innerHTML = '';
        } else {
            manualResults.innerHTML = state.manualResults.slice(0, 8).map((item, index) => {
                const typeLabel = item.media_type === 'tv' ? '电视剧' : '电影';
                const isBound = getBoundTmdbKey() && getBoundTmdbKey() === getManualResultTmdbKey(item);
                return `
                    <div class="scraper-manual-result">
                        ${renderPoster(item)}
                        <div class="scraper-manual-result-main">
                            <strong>${escapeHtml(item.title || '--')}</strong>
                            <span>${escapeHtml(typeLabel)}${item.year ? ` / ${escapeHtml(item.year)}` : ''}${item.vote_average ? ` / 评分 ${escapeHtml(String(item.vote_average))}` : ''}</span>
                            ${item.overview ? `<p>${escapeHtml(item.overview)}</p>` : ''}
                        </div>
                        <button type="button" class="scraper-compact-btn ${isBound ? 'scraper-manual-result-bound btn-disabled' : ''}" data-scraper-manual-index="${index}" ${isBound ? 'disabled aria-pressed="true"' : ''}>${isBound ? '已绑定' : '绑定'}</button>
                    </div>
                `;
            }).join('');
        }
    }
    const manualInput = $('scraper-manual-query');
    if (manualInput && !String(manualInput.value || '').trim() && identifyResult.query) {
        manualInput.value = String(identifyResult.query || '');
    }
    const mediaSelect = $('scraper-manual-media-type');
    if (mediaSelect && identifyResult.media_type) {
        mediaSelect.value = identifyResult.media_type === 'tv' ? 'tv' : 'movie';
    }
    syncSeasonControl();
    syncEpisodeModeControl();
    syncFileInfoControls();
    syncFolderScopedControls();
    syncBuildPlanControls();
}

function collectOptions() {
    const preserveTags = {};
    document.querySelectorAll('[data-scraper-tag]').forEach((input) => {
        preserveTags[String(input.dataset.scraperTag || '').trim()] = !!input.checked;
    });
    const selectionMode = getSelectionMode();
    const folderMode = selectionMode === 'folder';
    return {
        selection_mode: selectionMode,
        title_language: String($('scraper-title-language')?.value || 'zh'),
        season: Math.max(1, Number($('scraper-season')?.value || 1) || 1),
        episode_mode: String($('scraper-episode-mode')?.value || 'auto'),
        include_tmdb_id: folderMode && !$('scraper-include-tmdb-id')?.disabled && !!$('scraper-include-tmdb-id')?.checked,
        use_season_subfolder: folderMode && !$('scraper-use-season-subfolder')?.disabled && !!$('scraper-use-season-subfolder')?.checked,
        rename_selected_folders: folderMode && !$('scraper-rename-selected-folders')?.disabled && !!$('scraper-rename-selected-folders')?.checked,
        preserve_file_info: !!$('scraper-preserve-file-info')?.checked,
        preserve_tags: preserveTags,
    };
}

function renderPlan() {
    const summary = $('scraper-plan-summary');
    const list = $('scraper-plan-list');
    const executeBtn = $('scraper-execute-btn');
    const plan = state.plan || null;
    const selectedReadyCount = getSelectedReadyPlanCount();
    syncBuildPlanControls();
    const clearPlanBtn = $('scraper-clear-plan-btn');
    if (clearPlanBtn) {
        const showClear = getPlanActions().length > 0;
        clearPlanBtn.classList.toggle('hidden', !showClear);
        clearPlanBtn.disabled = state.executeBusy || !showClear;
        clearPlanBtn.classList.toggle('btn-disabled', state.executeBusy || !showClear);
    }
    const inlineExecuteBtn = $('scraper-inline-execute-btn');
    if (inlineExecuteBtn) {
        const showExecute = getPlanActions().length > 0;
        inlineExecuteBtn.classList.toggle('hidden', !showExecute);
        inlineExecuteBtn.disabled = state.executeBusy || selectedReadyCount <= 0;
        inlineExecuteBtn.classList.toggle('btn-disabled', state.executeBusy || selectedReadyCount <= 0);
        inlineExecuteBtn.textContent = state.executeBusy ? '提交中...' : `执行重命名 ${selectedReadyCount} 项`;
    }
    if (executeBtn) {
        executeBtn.disabled = state.executeBusy || selectedReadyCount <= 0;
        executeBtn.classList.toggle('btn-disabled', state.executeBusy || selectedReadyCount <= 0);
        executeBtn.textContent = state.executeBusy ? '提交中...' : `执行重命名 ${selectedReadyCount} 项`;
    }
    if (!summary || !list) return;
    if (state.planBusy) {
        summary.textContent = '正在识别文件并生成预览，请稍候...';
        list.innerHTML = '<div class="scraper-empty-row">识别中，请稍候...</div>';
        return;
    }
    if (!plan) {
        summary.textContent = '生成预览后会显示旧路径、新路径、识别依据和冲突状态。';
        list.innerHTML = '';
        return;
    }
    const total = Number(plan.total_count || 0);
    const ready = Number(plan.ready_count || 0);
    const issues = Array.isArray(plan.issues) ? plan.issues : [];
    const warnings = Array.isArray(plan.warnings) ? plan.warnings : [];
    summary.innerHTML = `
        <span class="${ready > 0 ? 'scraper-ok-text' : 'scraper-warn-text'}">${ready > 0 ? '可执行' : '需要处理'}</span>
        <span> / 已勾选 ${escapeHtml(String(selectedReadyCount))} 项，${escapeHtml(String(ready))} / ${escapeHtml(String(total))} 项可执行${issues.length ? ` / ${escapeHtml(String(issues.length))} 个冲突` : ''}${warnings.length ? ` / ${escapeHtml(String(warnings.length))} 个提醒` : ''}</span>
    `;
    const actions = Array.isArray(plan.actions) ? plan.actions : [];
    if (!actions.length) {
        list.innerHTML = '<div class="scraper-empty-row">没有可改名文件。</div>';
        return;
    }
    list.innerHTML = '';
}

function getJobStatusLabel(status) {
    const normalized = String(status || '').trim();
    const labels = {
        pending: '等待中',
        running: '执行中',
        completed: '已完成',
        partial: '部分完成',
        failed: '失败',
        rollback_running: '回退中',
        rolled_back: '已回退',
        rollback_failed: '回退失败',
    };
    return labels[normalized] || normalized || '--';
}

function renderJobs() {
    const list = $('scraper-job-list');
    if (!list) return;
    if (state.jobsBusy && !state.jobs.length) {
        list.innerHTML = '<div class="scraper-empty-row">正在读取任务记录...</div>';
        return;
    }
    if (!state.jobs.length) {
        list.innerHTML = '<div class="scraper-empty-row">暂无刮削任务记录。</div>';
        return;
    }
    list.innerHTML = state.jobs.map((job) => {
        const actions = Array.isArray(job.actions) ? job.actions : [];
        const actionPreview = actions.slice(0, 4).map(action => `
            <div class="scraper-job-action">
                <span>${escapeHtml(getJobStatusLabel(action.rollback_status || action.status))}</span>
                <span>${escapeHtml(action.new_path || action.old_path || action.old_name || '--')}</span>
            </div>
        `).join('');
        return `
            <div class="scraper-job-card">
                <div class="scraper-job-head">
                    <div>
                        <strong>#${escapeHtml(String(job.id || 0))} ${escapeHtml(getProviderLabel(job.provider))} · ${escapeHtml(getJobStatusLabel(job.status))}</strong>
                        <span>${escapeHtml(job.status_detail || '')}</span>
                    </div>
                    <div class="scraper-job-actions">
                        ${job.can_rollback ? `<button type="button" class="scraper-compact-btn" data-scraper-rollback-job="${escapeHtml(String(job.id || 0))}">回退</button>` : ''}
                    </div>
                </div>
                <div class="scraper-job-meta">
                    <span>成功 ${escapeHtml(String(job.succeeded_actions || 0))}</span>
                    <span>失败 ${escapeHtml(String(job.failed_actions || 0))}</span>
                    <span>${escapeHtml(formatTimeText(job.created_at))}</span>
                    <span>${escapeHtml(getTmdbDisplayTitle(job.tmdb || {}))}</span>
                </div>
                ${actionPreview ? `<div class="scraper-job-action-list">${actionPreview}</div>` : ''}
            </div>
        `;
    }).join('');
}

async function loadProviders() {
    const data = await window.MediaHubApi.getJson('/scraper/providers');
    state.providers = Array.isArray(data.providers) ? data.providers : [];
    const current = state.providers.find(item => normalizeProvider(item.provider) === state.provider);
    if (!current || !current.configured) {
        const firstConfigured = state.providers.find(item => item.configured);
        if (firstConfigured) state.provider = normalizeProvider(firstConfigured.provider);
    }
    renderProviderTabs();
    renderProviderStatus();
}

async function loadEntries({ force = false, keepSearch = true } = {}) {
    state.loading = true;
    renderEntries();
    try {
        const params = new URLSearchParams({ cid: state.cid });
        if (force) params.set('force_refresh', '1');
        if (keepSearch && state.search) params.set('q', state.search);
        const data = await window.MediaHubApi.getJson(`/scraper/${encodeURIComponent(state.provider)}/entries?${params.toString()}`);
        state.entries = (Array.isArray(data.entries) ? data.entries : []).map(enrichEntry);
        state.summary = data.summary || { folder_count: 0, file_count: 0 };
        clearSelection();
        resetIdentifyContext({ resetInputs: true });
    } catch (error) {
        state.entries = [];
        state.summary = { folder_count: 0, file_count: 0 };
        showToast(`读取目录失败：${error.message || '未知错误'}`, { tone: 'error', duration: 3200, placement: 'top-center' });
    } finally {
        state.loading = false;
        renderEntries();
    }
}

async function switchProvider(provider) {
    const nextProvider = normalizeProvider(provider);
    if (state.provider === nextProvider) return;
    state.provider = nextProvider;
    state.cid = '0';
    state.trail = [{ id: '0', name: '根目录' }];
    state.search = '';
    $('scraper-search-input').value = '';
    clearSelection();
    clearPlan();
    resetIdentifyContext({ resetInputs: true });
    closeIdentifyPanel();
    renderProviderTabs();
    renderIdentify();
    await loadEntries();
}

async function enterFolder(entryId) {
    if (state.loading || state.navigationBusy) return;
    const entry = state.entries.find(item => item.id === String(entryId || ''));
    if (!entry || !entry.is_dir) return;
    const nextCid = normalizeCid(entry.cid || entry.id);
    if (!nextCid || normalizeCid(state.cid) === nextCid) return;
    state.navigationBusy = true;
    try {
        state.cid = nextCid;
        state.trail = state.trail.concat([{ id: state.cid, name: entry.name }]);
        state.search = '';
        $('scraper-search-input').value = '';
        clearPlan();
        resetIdentifyContext({ resetInputs: true });
        closeIdentifyPanel();
        await loadEntries({ keepSearch: false });
    } finally {
        state.navigationBusy = false;
        renderEntries();
    }
}

async function goTrail(index) {
    if (state.loading || state.navigationBusy) return;
    const targetIndex = Math.max(0, Number(index || 0) || 0);
    const target = state.trail[targetIndex] || state.trail[0];
    if (normalizeCid(target.id) === normalizeCid(state.cid) && targetIndex === state.trail.length - 1) return;
    state.navigationBusy = true;
    try {
        state.trail = state.trail.slice(0, targetIndex + 1);
        state.cid = normalizeCid(target.id);
        state.search = '';
        $('scraper-search-input').value = '';
        clearPlan();
        resetIdentifyContext({ resetInputs: true });
        closeIdentifyPanel();
        await loadEntries({ keepSearch: false });
    } finally {
        state.navigationBusy = false;
        renderEntries();
    }
}

async function createFolder() {
    const input = $('scraper-new-folder-name');
    const name = String(input?.value || '').trim();
    if (!name) {
        showToast('请先输入文件夹名称', { tone: 'warn', duration: 2200, placement: 'top-center' });
        return;
    }
    try {
        await window.MediaHubApi.postJson(`/scraper/${encodeURIComponent(state.provider)}/folders`, {
            cid: state.cid,
            name,
        });
        if (input) input.value = '';
        showToast('文件夹已创建', { tone: 'success', duration: 2200, placement: 'top-center' });
        await loadEntries({ force: true });
    } catch (error) {
        showToast(`新建失败：${error.message || '未知错误'}`, { tone: 'error', duration: 3200, placement: 'top-center' });
    }
}

async function renameSelected() {
    const selected = getEffectiveSelectedEntries();
    if (selected.length !== 1 || !selected[0]?.is_dir) {
        showToast('请选择一个文件夹进行重命名', { tone: 'warn', duration: 2400, placement: 'top-center' });
        return;
    }
    const target = selected[0];
    const name = await promptText({
        title: '重命名',
        message: target.name,
        defaultValue: target.name,
        confirmText: '保存',
    });
    if (!name || name === target.name) return;
    const oldPath = getSelectionPath(target);
    const newPath = normalizePath(joinPath(getSelectionParentPath(target), name));
    try {
        if (oldPath && newPath) {
            let warning = '';
            try {
                const warningData = await window.MediaHubApi.postJson(`/scraper/${encodeURIComponent(state.provider)}/rename-warning`, {
                    old_path: oldPath,
                    new_path: newPath,
                });
                warning = String(warningData?.warning || '').trim();
            } catch (error) {
                showToast(`检查订阅保存路径失败：${error.message || '未知错误'}`, { tone: 'error', duration: 3200, placement: 'top-center' });
                return;
            }
            if (warning) {
                const ok = await showConfirm(warning, {
                    title: '路径提醒',
                    confirmText: '继续重命名',
                });
                if (!ok) return;
            }
        }
        await window.MediaHubApi.postJson(`/scraper/${encodeURIComponent(state.provider)}/rename`, {
            entry_id: target.id,
            parent_id: target.parent_id || state.cid,
            name,
        });
        showToast('已重命名', { tone: 'success', duration: 2200, placement: 'top-center' });
        await loadEntries({ force: true });
    } catch (error) {
        showToast(`重命名失败：${error.message || '未知错误'}`, { tone: 'error', duration: 3200, placement: 'top-center' });
    }
}

function prepareMove() {
    const selected = getEffectiveSelectedEntries();
    if (!selected.length) {
        showToast('请先选择要移动的条目', { tone: 'warn', duration: 2200, placement: 'top-center' });
        return;
    }
    state.moveBuffer = {
        provider: state.provider,
        source_cid: state.cid,
        source_path: currentParentPath() || '根目录',
        entries: selected,
    };
    clearSelection();
    renderEntries();
    showToast('已记录待移动条目，请进入目标目录后执行移动', { tone: 'info', duration: 3000, placement: 'top-center' });
}

async function moveHere() {
    const buffer = state.moveBuffer;
    if (!buffer || !buffer.entries?.length) return;
    if (buffer.provider !== state.provider) {
        showToast('待移动条目与当前网盘不一致', { tone: 'warn', duration: 2600, placement: 'top-center' });
        return;
    }
    if (normalizeCid(buffer.source_cid) === normalizeCid(state.cid)) {
        showToast('目标目录与来源目录相同', { tone: 'warn', duration: 2200, placement: 'top-center' });
        return;
    }
    const ok = await showConfirm(`将 ${buffer.entries.length} 个条目移动到当前目录，确定继续吗？`, {
        title: '确认移动',
        confirmText: '移动',
    });
    if (!ok) return;
    try {
        await window.MediaHubApi.postJson(`/scraper/${encodeURIComponent(state.provider)}/move`, {
            entry_ids: buffer.entries.map(item => item.id),
            source_cid: buffer.source_cid,
            target_cid: state.cid,
        });
        state.moveBuffer = null;
        showToast('移动已完成', { tone: 'success', duration: 2400, placement: 'top-center' });
        await loadEntries({ force: true });
    } catch (error) {
        showToast(`移动失败：${error.message || '未知错误'}`, { tone: 'error', duration: 3400, placement: 'top-center' });
    }
}

async function deleteSelected() {
    const selected = getEffectiveSelectedEntries();
    if (!selected.length) {
        showToast('请先选择要删除的条目', { tone: 'warn', duration: 2200, placement: 'top-center' });
        return;
    }
    const ok = await showConfirm(`确定删除 ${selected.length} 个条目吗？删除不纳入刮削任务回退。`, {
        title: '确认删除',
        confirmText: '删除',
        tone: 'error',
    });
    if (!ok) return;
    try {
        await window.MediaHubApi.postJson(`/scraper/${encodeURIComponent(state.provider)}/delete`, {
            entry_ids: selected.map(item => item.id),
            parent_id: state.cid,
        });
        showToast('已删除选中条目', { tone: 'success', duration: 2400, placement: 'top-center' });
        await loadEntries({ force: true });
    } catch (error) {
        showToast(`删除失败：${error.message || '未知错误'}`, { tone: 'error', duration: 3400, placement: 'top-center' });
    }
}

async function identifySelected() {
    const entries = getEffectiveSelectedEntries();
    if (!entries.length) {
        showToast('请先选择要识别的文件或文件夹', { tone: 'warn', duration: 2400, placement: 'top-center' });
        return;
    }
    const selectionKey = getSelectionKey(entries);
    if ((state.identifyResult || state.tmdb) && state.identifySelectionKey === selectionKey) {
        openIdentifyPanel();
        renderIdentify();
        return;
    }
    state.identifyBusy = true;
    resetIdentifyContext({ resetInputs: true });
    state.identifySelectionKey = selectionKey;
    clearPlan();
    openIdentifyPanel();
    renderIdentify();
    try {
        const data = await window.MediaHubApi.postJson('/scraper/identify', {
            provider: state.provider,
            entries,
        });
        state.identifyResult = data || {};
        state.tmdb = null;
        state.identifySelectionKey = selectionKey;
        const manualInput = $('scraper-manual-query');
        if (manualInput && data?.query) manualInput.value = String(data.query || '');
        const mediaSelect = $('scraper-manual-media-type');
        if (mediaSelect && data?.media_type) mediaSelect.value = data.media_type === 'tv' ? 'tv' : 'movie';
        showToast('已推荐关键词，请手动搜索并绑定 TMDB 条目', {
            tone: 'info',
            duration: 2600,
            placement: 'top-center',
        });
    } catch (error) {
        state.identifyResult = { msg: error.message || '识别失败' };
        state.identifySelectionKey = selectionKey;
        showToast(`识别失败：${error.message || '未知错误'}`, { tone: 'error', duration: 3400, placement: 'top-center' });
    } finally {
        state.identifyBusy = false;
        renderIdentify();
    }
}

async function bindTmdbCandidate(item) {
    if (!item) return;
    try {
        const mediaType = item.media_type === 'tv' ? 'tv' : 'movie';
        const params = new URLSearchParams({
            tmdb_id: String(item.id || item.tmdb_id || 0),
            media_type: mediaType,
        });
        const data = await window.MediaHubApi.getJson(`/tmdb/detail?${params.toString()}`);
        state.tmdb = data.task_binding || null;
        if (state.tmdb?.tmdb_media_type) {
            const mediaSelect = $('scraper-manual-media-type');
            if (mediaSelect) mediaSelect.value = state.tmdb.tmdb_media_type === 'tv' ? 'tv' : 'movie';
            const seasonInput = $('scraper-season');
            if (seasonInput && state.tmdb.tmdb_media_type === 'tv') {
                const maxSeason = getTmdbSeasonCount(state.tmdb);
                seasonInput.value = String(Math.min(Math.max(1, Number(seasonInput.value || 1) || 1), maxSeason || 99));
            }
        }
        clearPlan();
        state.identifySelectionKey = getSelectionKey();
        renderIdentify();
        renderSelection();
        showToast(`已绑定 TMDB：${getTmdbDisplayTitle()}`, { tone: 'success', duration: 2600, placement: 'top-center' });
    } catch (error) {
        showToast(`读取 TMDB 详情失败：${error.message || '未知错误'}`, { tone: 'error', duration: 3400, placement: 'top-center' });
    }
}

async function manualSearchTmdb() {
    const query = String($('scraper-manual-query')?.value || '').trim();
    if (!query) {
        showToast('请先输入影视名称', { tone: 'warn', duration: 2200, placement: 'top-center' });
        return;
    }
    const mediaType = $('scraper-manual-media-type')?.value === 'tv' ? 'tv' : 'movie';
    state.manualBusy = true;
    state.manualResults = [];
    renderIdentify();
    try {
        const params = new URLSearchParams({ q: query, media_type: mediaType });
        const data = await window.MediaHubApi.getJson(`/tmdb/search?${params.toString()}`);
        state.manualResults = Array.isArray(data.items) ? data.items : [];
        if (!state.manualResults.length) {
            showToast('未找到 TMDB 条目', { tone: 'warn', duration: 2400, placement: 'top-center' });
        }
    } catch (error) {
        showToast(`TMDB 搜索失败：${error.message || '未知错误'}`, { tone: 'error', duration: 3400, placement: 'top-center' });
    } finally {
        state.manualBusy = false;
        renderIdentify();
    }
}

async function buildPlan() {
    if (state.planBusy) return;
    const entries = getEffectiveSelectedEntries();
    if (!entries.length) {
        showToast('请先选择要刮削的文件或文件夹', { tone: 'warn', duration: 2400, placement: 'top-center' });
        return;
    }
    if (!state.tmdb || Number(state.tmdb.tmdb_id || state.tmdb.id || 0) <= 0) {
        showToast('请先绑定 TMDB 条目', { tone: 'warn', duration: 2400, placement: 'top-center' });
        return;
    }
    state.planBusy = true;
    state.plan = null;
    showToast('正在识别文件并生成预览...', { tone: 'info', duration: 1800, placement: 'top-center' });
    renderPlan();
    try {
        const options = collectOptions();
        const tmdb = state.tmdb ? { ...state.tmdb } : null;
        if (tmdb && options.episode_mode && options.episode_mode !== 'auto') {
            tmdb.tmdb_episode_mode = options.episode_mode;
        }
        const data = await window.MediaHubApi.postJson('/scraper/rename-plan', {
            provider: state.provider,
            base_cid: state.cid,
            base_path: currentParentPath(),
            entries,
            tmdb: tmdb || state.tmdb,
            options,
        });
        state.plan = data;
        state.planSelections = new Set(
            (Array.isArray(data.actions) ? data.actions : [])
                .filter(action => action.ready)
                .map(action => Number(action.action_index || 0) || 0)
                .filter(Boolean)
        );
        const warningCount = Array.isArray(data.warnings) ? data.warnings.length : 0;
        showToast(
            Number(data.ready_count || 0) > 0
                ? (warningCount > 0 ? `预览已生成，含 ${warningCount} 个提醒` : '预览已生成，请勾选确认后执行')
                : '预览没有可执行项，请处理冲突后再试',
            {
                tone: Number(data.ready_count || 0) > 0 ? (warningCount > 0 ? 'warn' : 'success') : 'warn',
                duration: 2800,
                placement: 'top-center',
            },
        );
        closeIdentifyPanel();
    } catch (error) {
        showToast(`生成预览失败：${error.message || '未知错误'}`, { tone: 'error', duration: 3600, placement: 'top-center' });
    } finally {
        state.planBusy = false;
        renderPlan();
        renderEntries();
    }
}

async function executePlan() {
    if (!state.plan) return;
    const selectedActions = (Array.isArray(state.plan.actions) ? state.plan.actions : [])
        .filter(action => action.ready && state.planSelections.has(Number(action.action_index || 0)));
    if (!selectedActions.length) {
        showToast('请先勾选要执行的预览项', { tone: 'warn', duration: 2400, placement: 'top-center' });
        return;
    }
    const warningLines = Array.from(new Set(selectedActions
        .map(action => String(action.warning || '').trim())
        .filter(Boolean)));
    let message = `确认执行已勾选的 ${selectedActions.length} 项重命名和移动吗？`;
    let title = '确认执行';
    let confirmText = '执行';
    if (warningLines.length > 0) {
        message = warningLines.join('\n');
        title = '路径提醒';
        confirmText = '继续执行';
    }
    const ok = await showConfirm(message, {
        title,
        confirmText,
    });
    if (!ok) return;
    state.executeBusy = true;
    renderPlan();
    try {
        const data = await window.MediaHubApi.postJson('/scraper/jobs/create', {
            plan: {
                ...state.plan,
                actions: selectedActions,
                ready: true,
                ready_count: selectedActions.length,
                total_count: selectedActions.length,
                issues: [],
            },
        });
        showToast(`刮削任务已提交 #${data.job_id}`, { tone: 'success', duration: 2600, placement: 'top-center' });
        if (typeof window.refreshTaskCenterJobsOnly === 'function') {
            await window.refreshTaskCenterJobsOnly({ preferTab: 'scraper' });
        } else if (typeof window.fetchScraperJobsState === 'function') {
            await window.fetchScraperJobsState({ silent: true });
        }
        if (typeof window.scheduleResourcePolling === 'function') {
            window.scheduleResourcePolling(1000);
        }
        state.plan = null;
        state.planSelections.clear();
        await refreshJobs();
        scheduleJobsPoll();
        await loadEntries({ force: true });
    } catch (error) {
        showToast(`提交失败：${error.message || '未知错误'}`, { tone: 'error', duration: 3600, placement: 'top-center' });
    } finally {
        state.executeBusy = false;
        renderPlan();
    }
}

async function refreshJobs() {
    state.jobsBusy = true;
    renderJobs();
    try {
        const data = await window.MediaHubApi.getJson('/scraper/jobs/state?limit=5');
        state.jobs = Array.isArray(data.jobs) ? data.jobs : [];
    } catch (error) {
        showToast(`读取任务记录失败：${error.message || '未知错误'}`, { tone: 'error', duration: 3200, placement: 'top-center' });
    } finally {
        state.jobsBusy = false;
        renderJobs();
    }
}

function hasActiveJobs() {
    return state.jobs.some(job => SCRAPER_JOB_ACTIVE_STATUSES.has(String(job.status || '').trim()));
}

function scheduleJobsPoll() {
    if (state.jobsPollTimer) return;
    state.jobsPollTimer = window.setInterval(async () => {
        await refreshJobs();
        if (!hasActiveJobs()) {
            window.clearInterval(state.jobsPollTimer);
            state.jobsPollTimer = 0;
            await loadEntries({ force: true });
        }
    }, 3000);
}

async function rollbackJob(jobId) {
    const normalizedJobId = Number(jobId || 0) || 0;
    if (normalizedJobId <= 0) return;
    const ok = await showConfirm(`回退刮削任务 #${normalizedJobId} 的成功动作吗？`, {
        title: '确认回退',
        confirmText: '回退',
    });
    if (!ok) return;
    try {
        await window.MediaHubApi.postJson(`/scraper/jobs/${encodeURIComponent(String(normalizedJobId))}/rollback`, {});
        showToast('回退任务已提交', { tone: 'success', duration: 2400, placement: 'top-center' });
        await refreshJobs();
        scheduleJobsPoll();
    } catch (error) {
        showToast(`回退提交失败：${error.message || '未知错误'}`, { tone: 'error', duration: 3400, placement: 'top-center' });
    }
}

function setSelected(entryId, checked) {
    const id = String(entryId || '').trim();
    if (!id) return;
    const entry = state.entries.find(item => item.id === id);
    if (!entry) return;
    if (checked) {
        state.selected.set(id, entry);
    } else {
        state.selected.delete(id);
    }
    invalidateSelectionContext();
    renderEntries();
}

function toggleAll(checked) {
    if (checked) {
        state.entries.forEach(entry => state.selected.set(entry.id, entry));
    } else {
        clearSelection();
    }
    invalidateSelectionContext();
    renderEntries();
}

function selectRangeBetweenChecked() {
    if (getPlanActions().length > 0) return;
    const entries = getDisplayEntries();
    const selectedIndexes = entries
        .map((entry, index) => state.selected.has(entry.id) ? index : -1)
        .filter(index => index >= 0);
    if (selectedIndexes.length < 2) {
        showToast('请先勾选区间的起点和终点', { tone: 'warn', duration: 2400, placement: 'top-center' });
        return;
    }
    const start = Math.min(...selectedIndexes);
    const end = Math.max(...selectedIndexes);
    entries.slice(start, end + 1).forEach(entry => {
        state.selected.set(entry.id, entry);
    });
    invalidateSelectionContext();
    renderEntries();
    showToast(`已补齐选择 ${end - start + 1} 项`, { tone: 'success', duration: 2200, placement: 'top-center' });
}

function handleClick(event) {
    if (event.target?.id === 'scraper-identify-panel') {
        closeIdentifyPanel();
        return;
    }
    const providerButton = event.target.closest('[data-scraper-provider]');
    if (providerButton) {
        void switchProvider(providerButton.dataset.scraperProvider);
        return;
    }
    const trailButton = event.target.closest('[data-scraper-trail-index]');
    if (trailButton) {
        void goTrail(trailButton.dataset.scraperTrailIndex);
        return;
    }
    const entryButton = event.target.closest('[data-scraper-entry-enter]');
    if (entryButton) {
        void enterFolder(entryButton.dataset.scraperEntryEnter);
        return;
    }
    const sortButton = event.target.closest('[data-scraper-sort]');
    if (sortButton) {
        setEntrySort(sortButton.dataset.scraperSort);
        return;
    }
    const keywordButton = event.target.closest('[data-scraper-keyword]');
    if (keywordButton) {
        const keyword = String(keywordButton.dataset.scraperKeyword || '').trim();
        const input = $('scraper-manual-query');
        if (input && keyword) {
            input.value = keyword;
            input.focus();
            input.select();
        }
        return;
    }
    const manualButton = event.target.closest('[data-scraper-manual-index]');
    if (manualButton) {
        void bindTmdbCandidate(state.manualResults[Number(manualButton.dataset.scraperManualIndex || 0)]);
        return;
    }
    const rollbackButton = event.target.closest('[data-scraper-rollback-job]');
    if (rollbackButton) {
        void rollbackJob(rollbackButton.dataset.scraperRollbackJob);
        return;
    }
    const actionButton = event.target.closest('[data-scraper-action]');
    if (!actionButton) return;
    const action = String(actionButton.dataset.scraperAction || '').trim();
    if (action === 'refresh') void loadEntries({ force: true });
    if (action === 'back-top') scrollScraperToTop();
    if (action === 'search') {
        state.search = String($('scraper-search-input')?.value || '').trim();
        void loadEntries();
    }
    if (action === 'create-folder') void createFolder();
    if (action === 'select-range') selectRangeBetweenChecked();
    if (action === 'rename-selected') void renameSelected();
    if (action === 'prepare-move') prepareMove();
    if (action === 'delete-selected') void deleteSelected();
    if (action === 'identify') void identifySelected();
    if (action === 'open-identify') {
        if (state.identifyResult || state.tmdb) {
            openIdentifyPanel();
            renderIdentify();
        } else {
            void identifySelected();
        }
    }
    if (action === 'close-identify') closeIdentifyPanel();
    if (action === 'manual-search') void manualSearchTmdb();
    if (action === 'build-plan') void buildPlan();
    if (action === 'clear-plan') {
        clearPlan();
        renderSelection();
    }
    if (action === 'execute-plan') void executePlan();
    if (action === 'refresh-jobs') void refreshJobs();
    if (action === 'move-here') void moveHere();
    if (action === 'clear-move') {
        state.moveBuffer = null;
        renderEntries();
    }
}

function handleChange(event) {
    const check = event.target.closest('[data-scraper-check]');
    if (check) {
        setSelected(check.dataset.scraperCheck, check.checked);
        return;
    }
    if (event.target?.id === 'scraper-check-all') {
        toggleAll(!!event.target.checked);
        return;
    }
    const planCheck = event.target.closest('[data-scraper-plan-check]');
    if (planCheck) {
        const index = Number(planCheck.dataset.scraperPlanCheck || 0) || 0;
        if (index > 0) {
            if (planCheck.checked) {
                state.planSelections.add(index);
            } else {
                state.planSelections.delete(index);
            }
            renderPlan();
            renderSelection();
        }
        return;
    }
    if (event.target?.id === 'scraper-manual-media-type') {
        syncSeasonControl();
        syncEpisodeModeControl();
        return;
    }
    if (event.target?.id === 'scraper-episode-mode') {
        clearPlan();
        return;
    }
    if (event.target?.id === 'scraper-preserve-file-info') {
        syncFileInfoControls();
        clearPlan();
        return;
    }
    if (event.target?.matches('[data-scraper-tag], #scraper-title-language, #scraper-season, #scraper-include-tmdb-id, #scraper-use-season-subfolder, #scraper-rename-selected-folders')) {
        clearPlan();
    }
}

function handleGlobalKeydown(event) {
    if (event.key !== 'Escape') return;
    const panel = $('scraper-identify-panel');
    if (!panel || panel.classList.contains('hidden')) return;
    closeIdentifyPanel();
}

function bindEvents() {
    const root = $('page-scraper');
    if (!root || root.dataset.scraperBound === '1') return;
    root.dataset.scraperBound = '1';
    root.addEventListener('click', handleClick);
    root.addEventListener('change', handleChange);
    window.addEventListener('scroll', syncScraperBackTopButton, { passive: true });
    window.addEventListener('resize', syncScraperBackTopButton);
    document.addEventListener('keydown', handleGlobalKeydown);
    window.syncScraperBackTopButton = syncScraperBackTopButton;
    $('scraper-search-input')?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' || event.isComposing) return;
        state.search = String(event.target.value || '').trim();
        void loadEntries();
    });
    $('scraper-new-folder-name')?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' || event.isComposing) return;
        void createFolder();
    });
    $('scraper-manual-query')?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' || event.isComposing) return;
        void manualSearchTmdb();
    });
}

async function refreshInitialData() {
    await loadProviders();
    renderProviderTabs();
    renderIdentify();
    renderPlan();
    await Promise.all([
        loadEntries(),
        refreshJobs(),
    ]);
    if (hasActiveJobs()) scheduleJobsPoll();
}

export async function ensureScraperManager({ firstVisit = false } = {}) {
    bindEvents();
    if (!state.initialized || firstVisit) {
        state.initialized = true;
        await refreshInitialData();
        syncScraperBackTopButton();
        return;
    }
    renderProviderTabs();
    renderEntries();
    renderIdentify();
    renderPlan();
    renderJobs();
    syncScraperBackTopButton();
    if (hasActiveJobs()) scheduleJobsPoll();
}
