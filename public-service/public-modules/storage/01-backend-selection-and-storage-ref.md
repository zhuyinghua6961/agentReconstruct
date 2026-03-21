# storage backend 选择与存储引用模型

对应代码：
- `backend/app/integrations/storage/base.py`
- `backend/app/integrations/storage/local.py`
- `backend/app/integrations/storage/minio.py`
- `backend/app/integrations/storage/factory.py`
- `backend/app/modules/storage/service.py`
- `backend/app/modules/storage/schemas.py`
- `backend/tests/test_storage.py`

## 1. 这个模块先定义了统一 backend 接口

`StorageBackend` 抽象方法固定为：

- `object_exists`
- `upload_file`
- `download_file`
- `get_file_url`
- `delete_object`

所以 storage 不是“到处直接调 MinIO SDK”，而是先抽象成平台 backend 接口，再由模块代码只依赖这 5 个能力。

## 2. backend 选择只在 factory 做一次

`get_storage_backend(project_root=None, force_new=False)` 的逻辑是：

1. 先看 `_backend_instance` 单例缓存
2. 默认总能构造一个 `LocalStorageBackend`
3. 从 settings 读 MinIO 配置
4. 如果 MinIO 关键配置不全，直接退回 local
5. 如果 MinIO backend 构造失败，也退回 local

因此 storage 的真实运行模型是：

- local backend 永远可作为兜底
- MinIO 是增强能力，不是必需前提

## 3. local backend 不是“真正上传”，更像引用适配器

`LocalStorageBackend` 的行为很特殊：

`upload_file()`：

- 不会复制文件
- 只把路径解析成绝对路径
- 返回 `local://<absolute-path>`

`download_file()`：

- 从 object_name 对应本地路径 copy 到目标路径

`get_file_url()`：

- 返回 `file://<absolute-path>`

`delete_object()`：

- 直接删除本地文件

所以 local backend 并不提供独立对象存储空间，而是把“对象名”解释成项目根目录下的本地路径引用。

## 4. MinIO backend 才是真正的对象存储实现

`MinIOStorageBackend`：

- 初始化时会检查依赖是否安装
- 必须有 endpoint/access_key/secret_key
- 会在构造时自动 `_ensure_bucket()`

它的行为更标准：

- `upload_file()` -> `fput_object`
- `download_file()` -> `fget_object`
- `get_file_url()` -> 预签名 URL
- `delete_object()` -> remove_object

但它也做了一个重要约束：

- bucket 默认固定到 settings 里的单 bucket

所以当前平台不是多 bucket 动态路由，而是“默认 bucket + object_name 前缀”的模式。

## 5. `storage_ref` 是跨模块通用的存储位置引用

`StorageService.parse_storage_ref()` 只认两种 scheme：

- `minio://bucket/object`
- `local://path`

返回统一结构：

- `scheme`
- `bucket`
- `object_name`
- `local_path`

`storage/schemas.py` 里也把它抽成了：

- `StorageRefParts`

说明 `storage_ref` 已经是平台内部的事实标准，而不是随便拼的字符串。

## 6. parse 逻辑偏保守

对 `minio://`：

- 要求 `bucket/object` 两段都能拆出来

对 `local://`：

- 直接把后面的部分当 path

除此之外：

- 返回 `None`

这意味着像这些值都不会被当成合法 storage ref：

- 空字符串
- 普通相对路径
- 不带 bucket 的 `minio://foo`

这些值在其他模块里往往会退回 `local_path` 兜底，而不是直接报错。

## 7. `mirror_file()` 是统一上传入口，但失败是非致命的

`mirror_file()`：

- 校验本地文件存在
- 取 backend
- 调 `backend.upload_file()`
- 返回 `storage_ref`

如果异常：

- 只 `logger.warning`
- 返回 `None`

所以 storage 当前的一个明确设计是：

- “对象镜像失败不应该阻断本地链路”

这和 conversation JSON、上传文件处理链是一致的。

## 8. `project_root` 是 local backend 解析的重要参数

factory 里如果没传 `project_root`，默认从当前文件往上推根目录。

local backend 再用这个 root_dir 做：

- 相对路径 object_name 解析

因此同一个 `object_name` 在不同 `project_root` 下会指向不同本地文件。

这也是为什么大量调用点都会显式传：

- `project_root=str(WORKSPACE_ROOT)`

## 9. factory 做了缓存，但允许 force_new

`force_new=True` 会跳过单例缓存，重新建 backend。

这主要服务于：

- 测试
- runtime 初始化
- 实时配置验证

测试里也明确验证了：

- MinIO 配置缺失时会拿到 `LocalStorageBackend`
- 配置齐全时会构造 `MinIOStorageBackend`

## 10. 这个模块的核心不是“选 MinIO 还是本地”，而是统一引用层

真正重要的公共能力是：

- 上游模块都只保存 `storage_ref` / `local_path`
- 读取时再由 storage service 解析成下载、镜像、清理行为

也就是说，storage 更像“文件位置抽象层”，而不只是一个 backend 工厂。
