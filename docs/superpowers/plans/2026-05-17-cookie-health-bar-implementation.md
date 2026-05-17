# Cookie 健康检查状态栏 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在设置页 provider 列表上方添加紧凑健康状态栏，同时消除前端所有 provider 硬编码。

**Architecture:** 前端改动为主，后端无需改动。`renderProviderAuthBlocks()` 输出顶部包含健康栏 HTML，通过 `updateCookieHealthBar()` 实现增量更新（SSE 推送时只更新圆点颜色，不重建整个 DOM）。状态数据通过全局 `cookieHealthState` 流转（index.js → settings.js）。

**Follow-up requirement:** 状态栏按钮文案改为「健康检查」；每个 provider 名称本身可点击做单项检查；状态不只靠圆点，还要用名称背景色区分绿色有效、红色失效/异常、蓝色检查中、黄色待检测/未配置。

**Tech Stack:** Vanilla JS, FastAPI SSE, Tailwind CSS

---

### Task 1: 修复 `normalizeCookieHealthEntry()` — 动态 provider label

**Files:**
- Modify: `static/js/index.js:1017-1043`

**Goal:** 从 `window.providerMeta` 查找 provider label，替代硬编码的 `provider === 'quark' ? 'Quark' : '115'`。

- [ ] **Step 1: 修改 `normalizeCookieHealthEntry` 函数**

将 index.js 第1017-1043行的 `normalizeCookieHealthEntry` 函数替换为：

```javascript
        function normalizeCookieHealthEntry(raw, provider = '115') {
            const source = raw && typeof raw === 'object' ? raw : {};
            const meta = (window.providerMeta || []).find(p => p.name === provider);
            const providerLabel = meta?.label || provider;
            const configured = !!source.configured;
            const rawState = String(source.state || (configured ? 'unknown' : 'missing')).trim().toLowerCase();
            const state = ['missing', 'unknown', 'checking', 'valid', 'invalid', 'error'].includes(rawState)
                ? rawState
                : (configured ? 'unknown' : 'missing');
            let message = String(source.message || '').trim();
            if (!message) {
                if (state === 'missing') message = `未配置 ${providerLabel} Cookie`;
                else if (state === 'checking') message = `正在检测 ${providerLabel} Cookie...`;
                else if (state === 'valid') message = `${providerLabel} Cookie 可用`;
                else if (state === 'invalid') message = `${providerLabel} Cookie 可能已失效`;
                else if (state === 'error') message = `${providerLabel} Cookie 检测异常`;
                else message = `已配置 ${providerLabel} Cookie，等待检测`;
            }
            return {
                configured,
                state,
                message,
                last_checked_at: String(source.last_checked_at || ''),
                last_success_at: String(source.last_success_at || ''),
                trigger: String(source.trigger || ''),
                fail_count: Math.max(0, Number(source.fail_count || 0) || 0),
            };
        }
```

改动点：第1019行 `const providerLabel = ...` 从硬编码改为 `window.providerMeta` 查找。

- [ ] **Step 2: 提交**

```bash
git add static/js/index.js
git commit -m "fix: normalizeCookieHealthEntry uses providerMeta for label lookup"
```

---

### Task 2: 修复 `normalizeCookieHealthState()` — 动态遍历所有 provider

**Files:**
- Modify: `static/js/index.js:1045-1051`

**Goal:** 不再硬编码 `['115', 'quark']`，改为遍历后端返回的所有 key。

- [ ] **Step 1: 修改 `normalizeCookieHealthState` 函数**

将 index.js 第1045-1051行的 `normalizeCookieHealthState` 函数替换为：

```javascript
        function normalizeCookieHealthState(raw) {
            const source = raw && typeof raw === 'object' ? raw : {};
            const result = {};
            Object.keys(source).forEach((key) => {
                result[key] = normalizeCookieHealthEntry(source[key], key);
            });
            return result;
        }
```

- [ ] **Step 2: 提交**

```bash
git add static/js/index.js
git commit -m "fix: normalizeCookieHealthState iterates all provider keys dynamically"
```

---

### Task 3: 修复 `renderCookieHealthCards()` — 动态 provider 列表

**Files:**
- Modify: `static/js/index.js:1091-1119`

**Goal:** 从 `window.providerMeta` 读取 provider 列表，替代硬编码 `['115', 'quark']`。同时处理 DOM 元素可能不存在的情况（旧卡片已从模板中移除）。

- [ ] **Step 1: 修改 `renderCookieHealthCards` 函数**

将 index.js 第1091-1119行的 `renderCookieHealthCards` 函数替换为：

```javascript
        function renderCookieHealthCards() {
            const meta = window.providerMeta || [];
            meta.forEach((p) => {
                const entry = cookieHealthState?.[p.name] || normalizeCookieHealthEntry({}, p.name);
                const cardEl = document.getElementById(`cookie-health-${p.name}-card`);
                const textEl = document.getElementById(`cookie-health-${p.name}-text`);
                const tone = getCookieHealthTone(entry);
                if (cardEl) applyCookieHealthCardTone(cardEl, tone);
                if (!textEl) return;
                const bits = [entry.message];
                if (entry.last_checked_at) bits.push(`上次检测 ${entry.last_checked_at}`);
                if (entry.last_success_at && entry.state !== 'valid') bits.push(`上次成功 ${entry.last_success_at}`);
                if (entry.fail_count > 0 && entry.state !== 'valid') bits.push(`连续失败 ${entry.fail_count} 次`);
                textEl.innerText = bits.filter(Boolean).join('；');
                textEl.classList.remove('text-slate-400', 'text-sky-200', 'text-emerald-200', 'text-amber-200', 'text-rose-200');
                if (tone === 'checking') textEl.classList.add('text-sky-200');
                else if (tone === 'success') textEl.classList.add('text-emerald-200');
                else if (tone === 'warn') textEl.classList.add('text-amber-200');
                else if (tone === 'error') textEl.classList.add('text-rose-200');
                else textEl.classList.add('text-slate-400');
            });

            const checkBtn = document.getElementById('cookie-health-check-btn');
            if (checkBtn) {
                checkBtn.disabled = cookieHealthCheckBusy;
                checkBtn.classList.toggle('btn-disabled', cookieHealthCheckBusy);
                checkBtn.innerText = cookieHealthCheckBusy ? '检测中...' : '立即检测 Cookie';
            }
        }
```

- [ ] **Step 2: 提交**

```bash
git add static/js/index.js
git commit -m "fix: renderCookieHealthCards uses providerMeta for dynamic provider list"
```

---

### Task 4: 新增 `renderCookieHealthBar()` 和 `updateCookieHealthBar()` — settings.js

**Files:**
- Modify: `static/js/modules/tabs/settings.js`

**Goal:** 在 provider 列表上方渲染健康状态栏，支持增量更新圆点颜色。

- [ ] **Step 1: 在 settings.js 末尾（`window.toggleProviderEnabled = ...` 之前）添加两个函数**

在 settings.js 第700行附近（`window.renderProviderAuthBlocks = renderProviderAuthBlocks;` 之前）插入：

```javascript
function getCookieHealthDotColor(state) {
    if (state === 'valid') return '#10b981';
    if (state === 'invalid' || state === 'error') return '#ef4444';
    if (state === 'checking') return '#0ea5e9';
    if (state === 'missing') return '#f59e0b';
    return '#64748b';
}

export function renderCookieHealthBar(cookieHealthState) {
    const container = document.getElementById('settings-provider-auth-container');
    if (!container) return;
    let bar = document.getElementById('cookie-health-bar');
    if (!bar) {
        bar = document.createElement('div');
        bar.id = 'cookie-health-bar';
        bar.className = 'flex items-center justify-between px-4 py-3 bg-slate-800/50 rounded-xl border border-slate-700/50 mb-3';
        bar.innerHTML = '<div id="cookie-health-dots" class="flex items-center gap-4 flex-wrap"></div>' +
            '<button id="cookie-health-check-all-btn" class="px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-xs font-bold transition-colors" onclick="window.checkAllCookiesHealth &amp;&amp; window.checkAllCookiesHealth()">全部检测</button>';
        container.insertBefore(bar, container.firstChild);
    }
    updateCookieHealthBar(cookieHealthState);
}

export function updateCookieHealthBar(cookieHealthState) {
    const dotsContainer = document.getElementById('cookie-health-dots');
    const checkBtn = document.getElementById('cookie-health-check-all-btn');
    if (!dotsContainer) return;
    const state = cookieHealthState && typeof cookieHealthState === 'object' ? cookieHealthState : {};
    const meta = window.providerMeta || [];
    let busy = false;
    dotsContainer.innerHTML = meta.map(p => {
        const entry = state[p.name] || {};
        const dotState = entry.state || (entry.configured ? 'unknown' : 'missing');
        const color = getCookieHealthDotColor(dotState);
        if (dotState === 'checking') busy = true;
        const pulsing = dotState === 'checking' ? ' style="animation: cookie-dot-pulse 1s ease-in-out infinite"' : '';
        const opacity = dotState === 'missing' ? ' opacity-60' : '';
        return '<span class="inline-flex items-center gap-1.5' + opacity + '">' +
            '<span class="w-2 h-2 rounded-full inline-block" style="background:' + color + ';' + (dotState === 'checking' ? 'animation:cookie-dot-pulse 1s ease-in-out infinite' : '') + '"></span>' +
            '<span class="text-xs text-slate-300">' + (p.label || p.name) + '</span>' +
            '</span>';
    }).join('');
    if (checkBtn) {
        checkBtn.disabled = busy;
        checkBtn.classList.toggle('opacity-50', busy);
        checkBtn.classList.toggle('pointer-events-none', busy);
        checkBtn.innerText = busy ? '检测中…' : '全部检测';
    }
}
```

- [ ] **Step 2: 注册到 window**

在 settings.js 的 `if (typeof window !== 'undefined')` 块中（约第698行）添加：

```javascript
    window.renderCookieHealthBar = renderCookieHealthBar;
    window.updateCookieHealthBar = updateCookieHealthBar;
    window.checkAllCookiesHealth = async function() {
        const meta = window.providerMeta || [];
        const providers = meta.filter(p => p.enabled !== false).map(p => p.name);
        if (!providers.length) return;
        try {
            const data = await window.MediaHubApi.postJson('/settings/cookies/check', {
                providers: providers,
                force: true
            });
            if (data?.cookie_health && typeof updateCookieHealthBar === 'function') {
                updateCookieHealthBar(data.cookie_health);
            }
        } catch (e) {
            console.warn('checkAllCookiesHealth failed', e);
        }
    };
```

- [ ] **Step 3: 添加 pulse 动画到 `templates/index.html` 的 `<style>` 块**

`templates/index.html:10-12` 已有 `<style>` 块，在其中添加 `@keyframes cookie-dot-pulse`：

```css
    <style>
        .hidden { display: none !important; }
        @keyframes cookie-dot-pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.3; }
        }
    </style>
```

- [ ] **Step 4: 提交**

```bash
git add static/js/modules/tabs/settings.js templates/partials/pages/settings.html
git commit -m "feat: add renderCookieHealthBar with dynamic health status bar"
```

---

### Task 5: 修复 `checkCookiesNow()` — 动态 provider 列表

**Files:**
- Modify: `static/js/modules/tabs/settings.js:469-495`

**Goal:** 从 `window.providerMeta` 读取 provider 列表，替代硬编码 `['115', 'quark']`。

- [ ] **Step 1: 修改 `checkCookiesNow` 函数**

将 settings.js 第481-483行的 providers 参数改为动态：

```javascript
        const meta = window.providerMeta || [];
        const providers = meta.filter(p => p.enabled !== false).map(p => p.name);
        const data = await window.MediaHubApi.postJson('/settings/cookies/check', {
            providers: providers.length ? providers : ['115', 'quark'],
            force: !!force
        });
```

替换原来的：
```javascript
        const data = await window.MediaHubApi.postJson('/settings/cookies/check', {
            providers: ['115', 'quark'],
            force: !!force
        });
```

- [ ] **Step 2: 提交**

```bash
git add static/js/modules/tabs/settings.js
git commit -m "fix: checkCookiesNow uses dynamic provider list from providerMeta"
```

---

### Task 6: 修复 `renderResourceCookieHint()` — resource/core.js

**Files:**
- Modify: `static/js/modules/resource/core.js:345-390`

**Goal:** 从 `window.providerMeta` 读取 provider 列表，替代硬编码。

- [ ] **Step 1: 修改 `renderResourceCookieHint` 函数**

将 resource/core.js 第353-358行的 providerMeta 构建改为动态：

```javascript
            const state = normalizeCookieHealthState(resourceState?.cookie_health || cookieHealthState || {});
            const wm = window.providerMeta || [];
            const providerMeta = wm.length ? wm.map((p) => ({
                provider: p.name,
                label: p.label || p.name,
                entry: normalizeCookieHealthEntry(state?.[p.name], p.name)
            })) : ['115', 'quark'].map((provider) => ({
                provider,
                label: provider === 'quark' ? 'Quark' : '115',
                entry: normalizeCookieHealthEntry(state?.[provider], provider)
            }));
```

同时更新警告文案（第367-375行），将硬编码的 "115 或 Quark Cookie" 改为通用表述：

```javascript
            if (!configuredAny) {
                const labels = wm.map(p => p.label).join('、') || '115 或 Quark';
                message = `尚未配置可用网盘 Cookie。请在“参数配置”填写 ${labels} Cookie，保存后可点击“立即检测 Cookie”。`;
```

- [ ] **Step 2: 提交**

```bash
git add static/js/modules/resource/core.js
git commit -m "fix: renderResourceCookieHint uses providerMeta for dynamic provider list"
```

---

### Task 7: 在 boot.js 中接入健康栏渲染

**Files:**
- Modify: `static/js/modules/app/boot.js`

**Goal:** 页面初始化时调用 `renderCookieHealthBar`，SSE 更新时调用 `updateCookieHealthBar`。

- [ ] **Step 1: 在 boot.js init() 中 `renderProviderAuthBlocks` 之后调用 `renderCookieHealthBar`**

在 boot.js 第125行 `renderProviderAuthBlocks(cfg, sensitiveMeta);` 之后添加：

```javascript
                if (typeof renderCookieHealthBar === 'function') {
                    renderCookieHealthBar(cfg.cookie_health || {});
                }
```

- [ ] **Step 2: 提交**

```bash
git add static/js/modules/app/boot.js
git commit -m "feat: wire renderCookieHealthBar into boot.js init flow"
```

---

### Task 8: SSE 和 saveSettings 中接入健康栏增量更新

**Files:**
- Modify: `static/js/index.js` — `applyCookieHealthState` 函数
- Modify: `static/js/modules/tabs/settings.js` — `saveSettings` 函数

**Goal:** 当 health state 更新时，同步刷新健康栏圆点。

- [ ] **Step 1: 在 `applyCookieHealthState` 中调用 `updateCookieHealthBar`**

在 index.js 的 `applyCookieHealthState` 函数（约第1121-1130行）末尾，`renderResourceCookieHint();` 之后添加：

```javascript
            if (typeof updateCookieHealthBar === 'function') {
                updateCookieHealthBar(cookieHealthState);
            }
```

完整函数变为：

```javascript
        function applyCookieHealthState(payload) {
            if (!payload || typeof payload !== 'object') return;
            cookieHealthState = normalizeCookieHealthState(payload);
            resourceState = {
                ...resourceState,
                cookie_health: cookieHealthState
            };
            renderCookieHealthCards();
            renderResourceCookieHint();
            if (typeof updateCookieHealthBar === 'function') {
                updateCookieHealthBar(cookieHealthState);
            }
        }
```

- [ ] **Step 2: 在 `saveSettings` 的 cookie_health 更新路径中调用 `updateCookieHealthBar`**

在 settings.js 的 `saveSettings` 函数（约第669-672行），`applyCookieHealthState` 调用之后添加：

```javascript
        if (data?.cookie_health && typeof applyCookieHealthState === 'function') {
            applyCookieHealthState(data.cookie_health);
        }
```

在同一个 if 块内，`applyCookieHealthState` 调用后添加 `updateCookieHealthBar` 调用。由于 `applyCookieHealthState` 内部已经会调用 `updateCookieHealthBar`（Step 1），所以无需额外改动。

- [ ] **Step 3: 提交**

```bash
git add static/js/index.js static/js/modules/tabs/settings.js
git commit -m "feat: wire updateCookieHealthBar into SSE and saveSettings flows"
```

---

### Task 9: 端到端验证

- [ ] **Step 1: 启动开发服务器**

```bash
.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 18080
```

- [ ] **Step 2: 验证健康栏渲染**

打开浏览器 → 设置页 → 检查「网盘认证与签到」区域顶部是否出现健康状态栏，包含所有 provider 名称按钮和「健康检查」按钮。

- [ ] **Step 3: 验证全部健康检查功能**

点击「健康检查」→ 名称按钮应变蓝并闪烁 → 检测完成后背景变绿/黄/红。按钮文字变为「检查中…」并在完成后恢复。

- [ ] **Step 4: 验证单个 provider 测试**

点击某个 provider 名称，或展开 provider 块点击「健康检查」→ 只检查该 provider。

- [ ] **Step 5: 验证 provider 禁用行为**

禁用一个 provider → 点击「健康检查」→ 被禁用的 provider 不参与检测（灰色背景不变）。

- [ ] **Step 6: 验证保存设置后的状态更新**

修改 cookie 值 → 保存 → 健康栏状态应更新。

- [ ] **Step 7: 提交（如有修改）**

```bash
git add -A
git commit -m "chore: final adjustments for cookie health bar"
```
