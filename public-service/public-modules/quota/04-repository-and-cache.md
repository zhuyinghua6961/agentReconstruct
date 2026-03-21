# quota 数据层与缓存层

对应代码：
- `backend/app/modules/quota/repository.py`
- `backend/app/modules/quota/cache.py`
- `backend/app/modules/quota/service.py`

## 1. 三张核心表

### 1.1 `quota_configs`

存：
- `quota_type`
- `quota_name`
- `period`
- `period_days`
- `default_limit`
- `daily_limit`
- `weekly_limit`
- `monthly_limit`
- `is_active`

这是规则定义表。

### 1.2 `user_quota_overrides`

存：
- `user_id`
- `quota_type`
- `custom_limit`

这是用户级统一覆盖表，不是每窗口分别覆盖表。

### 1.3 `user_quota_usage`

存：
- `user_id`
- `quota_type`
- `period_key`
- `used_count`

这是实际消耗记录表。

## 2. Repository 很薄，业务基本都在 service

Repository 只负责：
- 查 config
- 查 override
- 查 usage
- increment usage
- list configs
- create/update config
- reset 当前 usage

它不负责：
- period 校验
- 多窗口逻辑
- override 解释
- strict_config 语义

这些都在 service。

## 3. usage 的写法是 UPSERT 风格

`increment_usage()` 用的是：
- `INSERT ... ON DUPLICATE KEY UPDATE used_count = used_count + 1`

这意味着：
- 只要 `(user_id, quota_type, period_key)` 唯一键存在
- 同一窗口的计数会原子递增

这是 quota 记账最关键的 DB 行为。

## 4. reset 不是删除 usage 记录，而是改成 0

`reset_user_usage()`：
- `UPDATE user_quota_usage SET used_count = 0`

不是 delete。

所以：
- 重置后记录仍在
- 只是当前值被清零

## 5. Redis 缓存缓存的是什么

当前缓存的是“元数据”，不是 usage：

- 单个 quota config
- active configs 列表
- all configs 列表
- 用户 override

usage 并没有进 Redis。

这意味着：
- 配置读取可以走缓存
- 每次 quota check 的 usage 仍要打数据库

## 6. cache key 的设计

所有 key 都带：
- `QUOTA_CACHE_EPOCH`

例如：
- `quota:config:<epoch>:<quota_type>`
- `quota:active-configs:<epoch>`
- `quota:all-configs:<epoch>`
- `quota:user-override:<epoch>:<user_id>:<quota_type>`

epoch 是环境变量，不是自动递增版本号。

这意味着：
- 要做全局 cache 命名空间切换时，可以改环境变量
- 但平时日常失效还是靠 delete 具体 key

## 7. TTL 是分项配置的

环境变量：
- `QUOTA_CONFIG_CACHE_TTL_SECONDS`
- `QUOTA_ACTIVE_LIST_CACHE_TTL_SECONDS`
- `QUOTA_ALL_LIST_CACHE_TTL_SECONDS`
- `QUOTA_OVERRIDE_CACHE_TTL_SECONDS`

而且都有最小值 30 秒。

这说明 quota 缓存被当成：
- 稳定元数据缓存
- 不追求秒级强一致

## 8. config 更新后的失效方式

`QuotaService._invalidate_config_metadata()` 会：
- invalidate 单项 config cache
- invalidate active/all list cache

然后下次读取再回源。

注意：
- 它不会自动 bump epoch
- `bump_quota_epoch_marker()` 只是工具函数，目前 service 没主动调用它

## 9. override cache 目前只有读，没有管理 API

虽然有：
- `get_cached_quota_override`
- `cache_quota_override`
- `invalidate_quota_override_cache`

但当前后端 API 并没有提供：
- 创建/修改/删除 user override 的公开管理接口

这说明 override 能力在 service/repo 层已经预留好了，但管理面还没真正开放。

## 10. service 对缓存的使用方式

### 10.1 `check_quota()`

会缓存：
- config
- override

但 usage 每次都直接读 repo。

### 10.2 `get_user_quotas()`

先读 active config list cache，再逐项调 `check_quota()`。

### 10.3 `get_all_configs()`

直接走 all-configs cache。

## 11. 这套存储模型的实际含义

quota 当前是一个典型的：
- MySQL 规则与计数
- Redis 元数据缓存

结构。

它没有：
- 分布式 usage cache
- 批量窗口聚合缓存
- 按用户 quota 快照缓存

所以一致性比较直接，但热点用户 usage 读取仍然是 DB 压力点。
