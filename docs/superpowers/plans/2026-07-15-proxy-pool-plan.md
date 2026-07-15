# Proxy Pool URL Parameters & 403 Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update project documentation and example configuration to reflect that `PROXY_POOL_URL` must include `num=1&keep_alive=1440` and that 403 responses are handled by the existing proxy-rotation logic.

**Architecture:** No code changes. Only `.env.example` and `CLAUDE.md` are modified to document the agreed-upon configuration format and behavior.

**Tech Stack:** Markdown, shell verification.

## Global Constraints

- `PROXY_POOL_URL` remains a complete URL; the application does not append `num` or `keep_alive` automatically.
- 403 handling reuses existing `nfra.py` logic: mark current proxy failed, switch to next, refresh pool after 5-minute cooldown when exhausted.
- No new code, settings, or tests are introduced.

---

## File Structure

- `.env.example` — example environment configuration; update the `PROXY_POOL_URL` comment to show the expected qg.net URL format.
- `CLAUDE.md` — project instructions; expand the “代理池” note to mention URL parameters and 403 behavior.

---

### Task 1: Update `.env.example` proxy URL comment

**Files:**
- Modify: `.env.example:45-55`

**Interfaces:**
- Consumes: none
- Produces: updated inline documentation for `PROXY_POOL_URL`

- [ ] **Step 1: Replace the `PROXY_POOL_URL` comment with a documented example**

  Change lines 47-48 from:

  ```env
  # 代理池 API URL（批量获取，缓存到本地文件，耗尽后刷新）
  PROXY_POOL_URL=
  ```

  to:

  ```env
  # 代理池 API URL（完整 URL，需自行携带 qg.net 参数；示例为 num=1、keep_alive=1440 分钟）
  # PROXY_POOL_URL=https://exclusive.proxy.qg.net/get?key=YOUR_KEY&num=1&keep_alive=1440
  PROXY_POOL_URL=
  ```

- [ ] **Step 2: Verify the change**

  Run:

  ```bash
  grep -A2 "代理池 API URL" .env.example
  ```

  Expected output contains `num=1` and `keep_alive=1440`.

- [ ] **Step 3: Commit**

  ```bash
  git add .env.example
  git commit -m "docs(env): document PROXY_POOL_URL num and keep_alive parameters"
  ```

---

### Task 2: Expand `CLAUDE.md` proxy pool note

**Files:**
- Modify: `CLAUDE.md:265-267`

**Interfaces:**
- Consumes: none
- Produces: updated “代理池” section

- [ ] **Step 1: Replace the existing proxy pool note**

  Change lines 265-267 from:

  ```markdown
  ### 10. 代理池

  `PROXY_LIST` 支持逗号分隔的 HTTP/SOCKS5 代理列表。`proxy_rotation_strategy` 仅支持 `round-robin` 和 `random`，在 `fetchers/proxy.py` 中实现。
  ```

  to:

  ```markdown
  ### 10. 代理池

  `PROXY_LIST` 支持逗号分隔的 HTTP/SOCKS5 代理列表。`proxy_rotation_strategy` 仅支持 `round-robin` 和 `random`，在 `fetchers/proxy.py` 中实现。

  使用 qg.net 动态代理池时，`PROXY_POOL_URL` 需配置为完整 URL，包含 `num=1`（每次取 1 个 IP）和 `keep_alive=1440`（有效期 1440 分钟），例如：

  ```env
  PROXY_POOL_URL=https://exclusive.proxy.qg.net/get?key=YOUR_KEY&num=1&keep_alive=1440
  ```

  目标站点返回 `403 Forbidden` 时，`crawlers/nfra.py` 会将当前代理标记失败、切换到池中下一个代理；若代理全部耗尽，则等待 5 分钟后重新从 `PROXY_POOL_URL` 获取新代理。
  ```

- [ ] **Step 2: Verify the change**

  Run:

  ```bash
  grep -A10 "### 10. 代理池" CLAUDE.md
  ```

  Expected output contains `num=1`, `keep_alive=1440`, and `403 Forbidden`.

- [ ] **Step 3: Commit**

  ```bash
  git add CLAUDE.md
  git commit -m "docs(CLAUDE): document proxy URL params and 403 rotation behavior"
  ```

---

## Self-Review

- **Spec coverage:**
  - `num=1` requirement → Task 1 and Task 2 both include the exact parameter.
  - `keep_alive=1440` default → Task 1 and Task 2 both include the exact parameter.
  - 403 re-fetch behavior → Task 2 documents existing `nfra.py` logic.
- **Placeholder scan:** No TBD/TODO or vague steps.
- **Type consistency:** Not applicable; documentation-only plan.
- **Scope:** The plan stays within two documentation files and does not introduce code changes.
