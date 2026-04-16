# Frontend Vue Nginx Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a user-space Nginx deployment path that serves `frontend-vue/dist`, proxies `/api` and `/health` to the gateway, preserves SSE streaming, and keeps refresh-survivable task recovery working through the proxy.

**Architecture:** Add a renderable Nginx template under `deploy/nginx`, manage it with top-level shell scripts, and verify behavior with focused tests plus a runtime validation script. Keep gateway as the only backend upstream and keep its current 16-worker model unchanged.

**Tech Stack:** Nginx, bash, pytest, Vue/Vite build output, FastAPI gateway SSE/task APIs

---

### Task 1: Add Failing Tests For The Deployment Surface

**Files:**
- Create: `tests/test_frontend_nginx_deploy.py`
- Test: `tests/test_frontend_nginx_deploy.py`

- [ ] **Step 1: Write the failing test**

```python
def test_frontend_nginx_scripts_and_template_exist():
    ...

def test_frontend_nginx_start_script_uses_user_space_runtime_and_nginx_t():
    ...

def test_frontend_nginx_test_script_checks_streaming_and_task_recovery():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_frontend_nginx_deploy.py -p no:cacheprovider`
Expected: FAIL because the template and scripts do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create the template and scripts with the interfaces asserted by the test.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_frontend_nginx_deploy.py -p no:cacheprovider`
Expected: PASS

### Task 2: Implement The Nginx Template And Management Scripts

**Files:**
- Create: `deploy/nginx/frontend-vue-gateway.nginx.conf.template`
- Create: `scripts/build_frontend.sh`
- Create: `scripts/start_nginx_frontend.sh`
- Create: `scripts/stop_nginx_frontend.sh`
- Create: `scripts/status_nginx_frontend.sh`
- Create: `scripts/test_nginx_frontend.sh`
- Modify: `docs/superpowers/specs/2026-04-15-frontend-vue-nginx-gateway-design.md` only if implementation reveals a spec mismatch that must be corrected

- [ ] **Step 1: Render the failing behavior into script requirements**

Ensure the scripts expose:
- `FRONTEND_NGINX_PORT`
- `GATEWAY_UPSTREAM_URL`
- `FRONTEND_DIST_DIR`
- `NGINX_RUNTIME_ROOT`
- `NGINX_LOG_ROOT`
- `NGINX_BIN`

- [ ] **Step 2: Implement the Nginx template**

Template must:
- serve `index.html` with SPA fallback
- proxy `/api/` and `/health`
- disable proxy buffering and request buffering
- disable gzip
- set long proxy timeouts
- add `X-Accel-Buffering: no`

- [ ] **Step 3: Implement the build/start/stop/status scripts**

Minimal behaviors:
- build script runs `npm run build`
- start script renders config, runs `nginx -t`, starts with `-p`
- stop script stops via pid or `nginx -s stop`
- status script reports pid/config/logs/runtime roots

- [ ] **Step 4: Run the focused tests**

Run: `pytest -q tests/test_frontend_nginx_deploy.py -p no:cacheprovider`
Expected: PASS

### Task 3: Verify Real Build And Deployment Workflow

**Files:**
- Modify: `tests/test_frontend_nginx_deploy.py`
- Modify: `scripts/test_nginx_frontend.sh`

- [ ] **Step 1: Add a failing test for verification script coverage**

```python
def test_frontend_nginx_test_script_mentions_static_proxy_stream_and_recovery_checks():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_frontend_nginx_deploy.py -p no:cacheprovider`
Expected: FAIL until the verification script includes all required checks.

- [ ] **Step 3: Implement the verification script**

The script must check:
- `/`
- SPA fallback route
- `/health`
- an SSE endpoint through Nginx
- reconnect-with-`after_seq` task replay semantics, gated on auth env and Redis-ready conditions

- [ ] **Step 4: Run the focused tests**

Run: `pytest -q tests/test_frontend_nginx_deploy.py -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Run build and broader verification**

Run: `cd frontend-vue && npm run build`
Expected: build succeeds

Run: `pytest -q tests/test_frontend_nginx_deploy.py tests/test_service_lifecycle_scripts.py scripts/tests/test_service_common.py -p no:cacheprovider`
Expected: PASS
