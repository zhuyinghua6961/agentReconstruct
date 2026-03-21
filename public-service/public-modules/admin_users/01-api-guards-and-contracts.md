# admin_users 接口面、权限与契约

对应代码：
- `backend/app/modules/admin_users/api.py`
- `backend/app/modules/admin_users/schemas.py`
- `backend/app/modules/admin_users/service.py`
- `backend/tests/test_admin_users.py`

## 1. 对外接口全部挂在 `/api/admin`

当前路由前缀只有一套：

- `/api/admin/users`
- `/api/admin/users/{user_id}/password`
- `/api/admin/users/{user_id}/status`
- `/api/admin/users/{user_id}/type`
- `/api/admin/users/{user_id}`
- `/api/admin/users/batch-import`
- `/api/admin/users/import-template`

没有像 `auth` 那样提供 `/api/v1/...` 与历史前缀双兼容。

这说明后台管理接口目前只服务当前管理端，不承担老客户端兼容层。

## 2. 所有接口都依赖 `require_admin_context`

`test_admin_routes_registered_and_protected()` 明确固定了：

- `GET /users`
- `POST /users`
- `POST /users/batch-import`
- `GET /users/import-template`

都必须挂 `require_admin_context`。

而 `require_admin_context` 的真实语义来自 auth：

- 必须先通过 `require_auth_context`
- 并且 `role == admin`

所以：

- `super`
- `user_type == 2`

都不是这里的后台管理员身份。

## 3. schema 层限制很薄

`schemas.py` 只声明了：

- `UserCreateRequest`
- `UserPasswordResetRequest`
- `UserStatusUpdateRequest`
- `UserTypeUpdateRequest`

其中：

- `user_type: Literal["common", "super"] | str`
- `status: Literal["active", "disabled"] | str`
- `user_type` 更新时甚至接受 `str | int`

这类 schema 的作用更多是“接住字段”，而不是严格业务约束。

真正约束仍在 service 层。

## 4. API 层主要做三件事

普通 JSON 接口统一走：

- `_respond(result, ok_status=...)`

它会调用：

- `admin_users_service.status_code_for(...)`

文件导入接口额外做一件事：

- `_extract_file_from_multipart()`

模板下载接口则可能直接返回：

- `fastapi.responses.Response`

所以 API 层不是完全纯粹转发，它还承担了 multipart 解析和响应分派。

## 5. 状态码映射偏“后台工具接口”风格

`status_code_for()` 把这些错误统一映射到 `400`：

- `VALIDATION_ERROR`
- `USERNAME_INVALID`
- `USERNAME_EXISTS`
- `PERMISSION_DENIED`
- `NOT_SUPPORTED`
- `INVALID_FILE_TYPE`
- `FILE_MISSING`
- `FILENAME_EMPTY`
- `INVALID_FORMAT`
- 各类密码强度错误

其他映射：

- `QUOTA_EXCEEDED` -> `429`
- `USER_NOT_FOUND` -> `404`
- `DB_UNAVAILABLE` -> `503`

这里有两个明显特征：

- `PERMISSION_DENIED` 在这里不是 `403`，而是 `400`
- `USERNAME_EXISTS` 也不是 `409`，而是 `400`

因此它的 HTTP 语义比 auth 更“业务结果导向”，不那么 REST 化。

## 6. 批量导入接口不用 `UploadFile`

`batch_import_users()` 没有使用 FastAPI 常见的：

- `file: UploadFile = File(...)`

而是：

1. `await request.body()`
2. 读完整个 body 到内存
3. 用 `email.parser.BytesParser` 解析 multipart
4. 只找 `name="file"` 的 form-data part

这意味着：

- 文件上传是全量读入内存后再处理
- 没有流式上传处理
- 文件字段名必须是 `file`

测试 `test_extract_file_from_multipart()` 也固定了这一契约。

## 7. 模板下载接口是二态返回

`GET /users/import-template`

当 `format` 合法时返回：

- 直接下载响应

当格式非法时返回：

- JSON 错误 payload

这和普通 CRUD 接口不同，因为它不是始终 `JSONResponse`。

## 8. list/create/reset/status/type/delete 的表层契约

`GET /users`：

- 支持 `page`
- 支持 `page_size`
- page_size 越界时被 service 夹回 `10`

`POST /users`：

- 返回 `201`

`PUT /users/{id}/password`：

- 重置目标用户密码

`GET /users/{id}/password`：

- 不是取回明文密码
- 只是返回“系统采用哈希存储，无法查看明文密码”

`PUT /users/{id}/status`：

- 切 active/disabled

`PUT /users/{id}/type`：

- 切 `common/super` 或 `3/2`

`DELETE /users/{id}`：

- 物理删除用户

## 9. API 层并不统一处理前端常见错误

对比 auth 服务端，这里没有特别区分：

- 401 token 失效
- 403 管理员权限不足

这些错误还是由 auth dependency 抛出。

所以 admin_users 自己的 `_respond()` 只负责业务 service 的结果，不负责 dependency 异常。

## 10. 测试更关注“接口保护”和“核心 contract”

`test_admin_users.py` 主要固定了：

- 管理接口必须受 admin dependency 保护
- 单用户创建会写 `role=user`
- 用户类型切换会正确把 `super` 解析为 `2`
- 重置密码会补写首次登录与安全问题强制标志
- 批量导入 CSV/XLSX 的 happy path
- 缺列和 quota exceed 的返回

测试没有完整覆盖所有管理页交互，但已经把最关键的公共契约钉住了：

- 后台权限门槛
- 导入成功结构
- 用户类型和密码重置副作用
