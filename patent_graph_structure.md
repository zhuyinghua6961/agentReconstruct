# Patent 项目知识图谱结构分析

## 1. 基本配置

- **Neo4j 端口**: 8687 (bolt://127.0.0.1:8687)
- **配置文件**: patent/config.py 中的 PatentGraphSettings
- **环境变量前缀**: PATENT_NEO4J_*
- **默认数据库**: neo4j
- **超时设置**: 3000ms
- **最大返回行数**: 20

## 2. 代码文件结构



## 3. 节点类型 (Node Labels)

### 核心节点
- **Patent** - 专利主节点，包含专利基本信息
  - 属性: patent_id, title, abstract, application_date, publication_date, ipc_main, patent_type, legal_status, source_file, stub

### 工艺相关
- **ProcessStep** - 工艺步骤
  - 属性: step_order, step_name, step_template

### 材料相关
- **MaterialRole** - 材料角色
  - 属性: material_name, role_description

### 实验数据
- **ExperimentTable** - 实验表格
  - 属性: table_title, table_index
- **TableRow** - 表格行
  - 属性: row_index, sample_label, process_note
- **Measurement** - 测量数据
  - 属性: metric_key, value_raw, unit_hint

### 技术内容
- **ProblemSolution** - 技术问题与解决方案
  - 属性: problem_text, solution_text
- **InventiveScope** - 发明点/保护范围
  - 属性: scope_text

### 引用关系
- **PatentCitation** - 专利引用
  - 属性: cited_patent_id, citation_type

### 分类信息
- **IPC** - 国际专利分类主分类
  - 属性: code
- **IPCSubclass** - IPC子分类
  - 属性: subclass

### 主体信息
- **Organization** - 组织/公司（申请人）
  - 属性: name
- **Inventor** - 发明人
  - 属性: name
- **Agency** - 代理机构
  - 属性: name

## 4. 关系类型 (Relationship Types)



## 5. 查询模板 (9个)

### 单专利查询
1. **lookup_patent_by_id** - 根据专利ID查询详情
   - 参数: patent_id
   - 返回: 专利完整信息 + 关联的IPC、申请人、发明人、代理机构

### 专利内容查询
2. **list_patent_process_steps** - 查询工艺步骤
   - 参数: patent_id
   - 返回: 步骤顺序、名称、模板

3. **list_patent_material_roles** - 查询材料角色
   - 参数: patent_id
   - 返回: 材料名称、角色描述

4. **list_patent_experiment_tables** - 查询实验表格
   - 参数: patent_id
   - 返回: 表格标题、行数据、测量指标

5. **list_patent_problem_solution** - 查询技术问题与方案
   - 参数: patent_id
   - 返回: 问题描述、解决方案

6. **list_patent_inventive_scope** - 查询发明点
   - 参数: patent_id
   - 返回: 保护范围文本

7. **list_patent_citations** - 查询引用关系
   - 参数: patent_id
   - 返回: 被引用的专利ID和类型

### 列表查询
8. **list_patents_by_ipc** - 根据IPC分类查询专利列表
   - 参数: ipc_code
   - 返回: 匹配IPC的专利列表

9. **list_patents_by_applicant** - 根据申请人查询专利列表
   - 参数: organization_name
   - 返回: 该申请人的所有专利

## 6. Classifier 分类逻辑

### 正则表达式模式
- **专利ID**: CN|US|WO|JP|EP|KR 开头的专利号
- **IPC分类**: [A-H][0-9]{2}[A-Z][0-9]+/[0-9A-Z]+
- **申请人查询**: {name}有哪些专利 格式

### 决策逻辑
1. 如果有专利ID + 特定关键词 → 使用对应内容查询模板
2. 如果有IPC + 专利 → IPC列表查询
3. 如果匹配申请人查询模式 → 申请人列表查询
4. 否则跳过图谱查询

### 关键词映射
- 工艺步骤、步骤、工艺 → list_patent_process_steps
- 原料、材料角色 → list_patent_material_roles
- 实验表格、性能数据、实验数据、测量 → list_patent_experiment_tables
- 技术问题、方案、应用场景 → list_patent_problem_solution
- 发明点、保护范围、保护、性能事实、claim → list_patent_inventive_scope
- 引用 → list_patent_citations

## 7. 与 fastQA 文献图谱的初步对比

| 维度 | Patent 图谱 | fastQA 文献图谱 |
|------|-------------|-----------------|
| 端口 | 8687 | 7688 |
| 主键 | patent_id | doi |
| 节点类型 | 专利为中心，多维度展开 | 文献为中心，字段分桶 |
| 关系复杂度 | 高（工艺、材料、实验等多层关系） | 中等（字段关联） |
| 查询模板数 | 9个 | 5个（V1）/ 更多（V2） |
| 领域特性 | 专利特定（IPC、申请人、发明人等） | 学术文献特定 |

