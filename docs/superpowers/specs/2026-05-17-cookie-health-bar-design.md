# 网盘健康检查功能设计

## 概述

在设置页「网盘认证与签到」区域顶部新增一条紧凑的健康状态栏，一眼看到所有网盘的 cookie 健康状态，支持一键全部健康检查，也支持点击单个网盘名称独立检查。

## 现状与问题

- **后端已支持动态 provider 健康检查**：`refresh_cookie_health_status()` 自动遍历注册表中所有 provider，`probe_connectivity()` 各 provider 已实现
- **前端多处硬编码 `['115', 'quark']`**：`normalizeCookieHealthState()`、`renderCookieHealthCards()`、`checkCookiesNow()`、`renderResourceCookieHint()` 都丢弃了新 provider（天翼云盘、123云盘、阿里云盘）的健康数据
- **设置页无健康状态展示**：旧版 Cookie 健康卡片在 multi-provider 重构时被移除，用户无法看到网盘 cookie 是否正常

## 触发方式

**按需检测**：打开设置页时从 `/get_settings` 获取初始状态（`cookie_health` 字段），点击「健康检查」按钮或单个网盘名称时 POST `/settings/cookies/check` 触发检测，SSE 实时推送结果更新 UI。

不引入后台定时轮询。

## UI 设计

### 位置

设置页 → 「1. 网盘认证与签到」→ provider 认证块列表上方

### 状态栏结构

```
┌──────────────────────────────────────────────────────────┐
│  [115网盘]  [夸克网盘]  [天翼云盘]  [123云盘]  [阿里云盘]  [健康检查] │
└──────────────────────────────────────────────────────────┘
```

- 每个网盘：圆点 + 名称胶囊按钮，背景颜色直接表达状态
- 点击单个网盘名称：只检查该 provider
- 最右侧：「健康检查」按钮：检查所有已启用 provider
- 检测中圆点闪烁蓝色，按钮显示 "检查中…" 并禁用

### 状态颜色

| 颜色 | 状态 | 含义 |
|------|------|------|
| 绿 `#10b981` | valid | cookie 正常 |
| 黄 `#f59e0b` | missing / unknown | 未配置或未检测 |
| 红 `#ef4444` | invalid / error | cookie 失效或检测异常 |
| 灰 `#64748b` | 未启用 | provider 被禁用 |
| 蓝闪烁 `#0ea5e9` | checking | 检测进行中 |

### 交互流程

1. 打开设置页 → `/get_settings` 返回 `cookie_health` 初始状态 → 渲染状态栏
2. 点击「健康检查」→ POST `/settings/cookies/check` → 所有已启用 provider 开始检测
3. 点击某个网盘名称 → POST `/settings/cookies/check`，只传该 provider
4. 后端执行 `probe_connectivity()` → SSE 推送 `cookie_health` 增量 → 状态栏实时更新

## 后端改动

### 无需新增接口

复用现有端点：

- `POST /settings/cookies/check`（已存在）：接受 `{"providers": ["115", "quark", "tianyi", ...]}`，前端动态传所有已启用 provider
- `GET /get_settings`（已存在）：返回 `cookie_health` 字段，`build_public_settings_payload()` 已包含

### 需确认

1. `get_cookie_health_providers()` 在 core.py 中动态读取注册表，新 provider 自动纳入 — **无需改动**
2. `build_cookie_health_payload()` 遍历所有 provider — **无需改动**
3. `refresh_cookie_health_status()` 通过 `p.probe_connectivity()` 动态分发 — **无需改动**

## 前端改动

### 1. 新增 `renderCookieHealthBar()` — settings.js

在 `renderProviderAuthBlocks` 调用之前执行。从 `window.providerMeta` 读取 provider 列表，从全局 `cookieHealthState` 读取状态，生成状态栏 HTML 插入到 `settings-provider-auth-container` 内部顶部。

```javascript
function renderCookieHealthBar(cookieHealthState) {
    const providers = window.providerMeta || [];
    // 每个 provider 渲染圆点 + 名称
    // 状态从 cookieHealthState[provider.name] 读取
    // 「健康检查」按钮和 provider 名称按钮 → POST /settings/cookies/check
}
```

### 2. 修复去硬编码 — index.js

**`normalizeCookieHealthState(raw)`**：
- 旧：硬编码 `const normalized = { '115': ..., 'quark': ... }`
- 新：遍历 `Object.keys(raw)`，为每个 key 调用 `normalizeCookieHealthEntry(raw, key)`

**`normalizeCookieHealthEntry(raw, provider)`**：
- 旧：`providerLabel = provider === 'quark' ? 'Quark' : '115'`
- 新：从 `window.providerMeta` 查找 `p.label`，回退到 `provider` 本身

**`renderCookieHealthCards()`**：
- 旧：硬编码 `const providers = ['115', 'quark']`
- 新：从 `window.providerMeta` 读取

### 3. 修复 `checkCookiesNow()` — settings.js

- 旧：`providers: ['115', 'quark']`
- 新：从 `window.providerMeta` 读取所有已启用 provider 的 name

### 4. 修复 `renderResourceCookieHint()` — resource/core.js

- 旧：硬编码 `['115', 'quark']`
- 新：遍历 `Object.keys(cookieHealthState)` 检查所有 provider 状态

## 数据流

```
设置页加载
  → GET /get_settings → cfg.cookie_health
  → renderCookieHealthBar(cookie_health) → 渲染初始状态栏

点击「健康检查」
  → POST /settings/cookies/check {providers: ['115','quark','tianyi','123pan','aliyun']}
  → refresh_cookie_health_status() 逐个调 probe_connectivity()
  → 每个 provider 检测完成 → schedule_ui_state_push(0)
  → SSE push {cookie_health: {...}}
  → applyCookieHealthState() → renderCookieHealthBar() 更新状态背景

点击单个网盘名称
  → POST /settings/cookies/check {providers: ['quark']}
  → 同一条 cookie_health 状态流更新
```

## 边界情况

- **provider 被禁用**：显示灰色背景，不参与「健康检查」
- **provider 无 cookie**：状态为 `missing`，黄色圆点
- **检测超时/网络错误**：状态为 `error`，红色圆点
- **providerMeta 为空**：不渲染状态栏
- **SSE 断连**：状态栏显示上次已知状态，重新连接后增量更新

## 不改的范围

- 不新增后端路由
- 不引入定时轮询
- 不在状态栏显示检测时间戳（保持简洁）
- 每个 provider 块内按钮改名「健康检查」；未填写新认证时检查已保存认证，填写新认证时只验证当前输入
- 新 provider（aliyun、tianyi、pan123）的运行时健康标记（操作成功/失败时自动标记）延后处理
