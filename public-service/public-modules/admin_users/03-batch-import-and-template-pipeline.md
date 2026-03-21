# admin_users 批量导入、模板与配额链

对应代码：
- `backend/app/modules/admin_users/import_service.py`
- `backend/app/modules/admin_users/api.py`
- `backend/tests/test_admin_users.py`
- `frontend-vue/src/components/BatchImportDialog.vue`

## 1. 批量导入是一条独立实现链，不复用 `create_user()`

`import_users()` 并没有调用：

- `admin_users_service.create_user()`

而是逐行直接调用：

- `admin_users_service.users.create_user(...)`

所以它绕过了单用户创建链里补充的业务语义。

这点非常关键，因为导入链和手工创建链最终不是同一套行为。

## 2. 导入前先看文件名，再看 quota，再看格式

入口顺序：

1. 清理文件名
2. `_precheck_excel_upload_quota(actor_user_id)`
3. 校验扩展名只允许 `xlsx/csv`
4. 加载行数据
5. 检查必须列
6. 逐行处理

所以配额检查发生在真正解析文件之前。

## 3. quota 接入不是标准 finalize 语义

预检查：

- `_precheck_excel_upload_quota()`

它会先读 actor 用户：

- 如果 `user_type in {1, 2}`，直接豁免
- 否则调用 `precheck_quota(user_id, quota_type="excel_upload")`

这里有两个特殊点：

- 这个接口本身被 `require_admin_context` 保护，但 quota 豁免仍然把 `user_type == 2` 视为免配额
- 导入服务直接调用 quota dependency helper，而不是通过 FastAPI dependency 注入

完成计数：

- `_finalize_excel_upload_quota()`
- 只要 `grant != None` 且 `result.success == True`，就直接 `increment_quota()`

这意味着它不是：

- 按成功创建条数计数

而是：

- 按一次导入动作计一次

## 4. 即使 0 条成功创建，也可能记一次导入 quota

`import_users()` 只要文件格式正确、必须列存在，就会在最后返回：

- `success = True`

即使结果是：

- `success_count = 0`
- 只有 `failed/skipped`

此时 `_finalize_excel_upload_quota()` 仍会计数。

所以这条 quota 的语义其实是：

- “完成了一次导入尝试”

不是：

- “成功导入了至少一条用户”

## 5. CSV 和 XLSX 解析链都很轻量

CSV：

- 顺序尝试 `utf-8-sig`
- `utf-8`
- `gb18030`

XLSX：

- 不依赖 openpyxl
- 直接读 zip 包里的 XML
- 只解析第一个 worksheet
- 读取：
  - `xl/workbook.xml`
  - `xl/_rels/workbook.xml.rels`
  - `xl/sharedStrings.xml`
  - 对应 sheet XML

这说明导入链是刻意做成轻依赖实现的。

代价是：

- 只支持较简单的 Excel 结构
- 没有复杂格式兼容层

## 6. 模板下载能力也是 import_service 自己做的

支持：

- `csv`
- `xlsx`

模板列固定：

- `username`
- `password`
- `user_type`

样例数据固定三行：

- `user001 / Pass123! / common`
- `user002 / Test456@ / super`
- `user003 / Demo789# / common`

测试也固定了：

- CSV 模板必须包含 `username,password,user_type`
- XLSX 模板响应体以 zip 头 `PK` 开头

## 7. 批量导入口令规则是“初始口令规则”，不是用户后续改密规则

逐行规则：

- 用户名不能为空
- 不能以 `admin` 开头
- 用户名长度 `3..50`
- 密码长度至少 `6`
- `user_type` 只能是 `common/super`
- 已存在用户名 -> `skipped`

这里最需要单独记的是：

- 只校验密码长度 >= 6
- 不校验 auth 自助注册/用户后续改密的 8 位 + 至少 3 类字符规则

结合当前业务澄清，这应理解为：

- 批量导入是在管理员侧批量发放初始登录口令
- 它不需要遵守用户后续自行修改密码时的强口令规则

因此这条链和“用户自己改密码”的规则不是同一层约束。

## 8. 导入成功记录没有补充完整安全副作用

逐行成功时只写：

- `username`
- `password_hash`
- `role = user`
- `user_type = 2/3`

没有显式补：

- `is_first_login = True`
- `must_set_security_questions = True`
- `password_history`
- `trim_password_history`

所以导入链的最终行为会依赖：

- 数据库默认值
- 可选列是否存在

而不是业务代码显式保证。

这是当前 admin_users 最大的实现分叉点之一。

## 9. 结果结构偏向逐行审计

最终返回：

- `summary.total`
- `summary.success`
- `summary.failed`
- `summary.skipped`
- `details[]`
- `duration`

`details[]` 的每行通常包含：

- `row`
- `username`
- `status`
- 失败/跳过时的 `reason`

成功项只写：

- `row`
- `username`
- `status=success`

并不返回：

- 新建用户 ID

## 10. 导入链的成功定义是“处理完成”，不是“全成功”

即使一个文件里：

- 一部分成功
- 一部分失败
- 一部分跳过

最终仍然返回：

- `success = True`
- `message = 批量导入完成`

只有这些场景才整体失败：

- 文件名为空
- quota 预检查失败
- 文件格式不支持
- 文件内容解析异常
- 缺少必须列

所以调用方必须看 `summary` 和 `details`，不能只看顶层 `success`。

## 11. 前端说明里有一条后端并未实现的限制

`BatchImportDialog.vue` 写着：

- 单次最多导入 1000 条记录

但当前后端 `import_service.py` 没看到任何：

- 行数上限校验

因此这是典型的“前端文案限制存在，但后端未落约束”。
