# GigBuddy 音色文件管理

> 面向客户的操作指南见 [GigBuddy 音色文件管理指南](tone-file-management.zh-CN.md)。
> 本文保留实现边界、索引结构和工程约束，供开发与集成使用。

本文说明 GigBuddy 当前如何保存远程下载的音色、如何加载用户自己的本地
Tone Pack，以及文件格式、目录结构和元数据方面的限制。这里的 `data/`
路径是 GigBuddy 数据根目录的相对写法：源码 checkout 中位于仓库下，安装版
默认位于 `~/.local/share/gigbuddy-data/` 下。安装版 checkout 的
`<GigBuddy home>/data` 只是指向该数据根目录的兼容链接。

## 1. 结论

本地文件夹应当是一等 Tone Pack：

- 一个 Tone Pack = 一个文件夹；
- 文件夹下的 `.nam` 文件 = NAM Model；
- 文件夹下的 `.wav` 文件 = IR Model；
- `gigbuddy.json` 是可选的 Pack 元数据文件；
- 没有元数据文件时，文件仍然可以扫描、试听、进入 Slot、保存到 Chain；
- 文件扩展名和实际文件路径是处理事实，元数据只能补充显示信息，不能把 `.nam` 变成 IR，也不能把 `.wav` 变成 NAM。

这能兼容用户已有的普通文件夹，也能让 TONE3000 下载保持同一套本地格式。

## 2. 文件放在哪里

当前 checkout 中，TONE3000 Pack 下载到：

```text
data/tones/<tone-id>-<title-slug>/
  <model-name>.nam
  <ir-name>.wav
```

安装后的实际根目录是：

```text
~/.local/share/gigbuddy-data/tones/
```

`data/gigbuddy.db` 保存远程 Tone/Model 的 TONE3000 索引和下载状态，也保存
本地 Pack 的可重建索引。数据库不是音色文件本身；重新建立本地索引不会改写
`.nam` / `.wav` 文件。

`data/live_chain.json` 只保存当前 Slot 路径和链参数，不保存 Tone、gear 或
Model 的整段元数据。`data/presets/` 保存可编辑的 preset JSON，具体见第
6 节。

目标格式保持简单：

```text
data/tones/
  123-fender-deluxe-reverb/
    gigbuddy.json              # 可选；TONE3000 下载默认生成
    Clean SM57.nam             # NAM Model
    Bright M201.nam            # NAM Model
    4x12 V30.wav                # IR Model

  local-my-princeton-pack/
    gigbuddy.json              # 可选；本地 Pack 可以没有它
    57 off-axis.nam
    121 edge.wav
```

Model 不再额外套一层 `model/` 目录。Pack 文件夹的直接子文件就是 Model；`.part`、隐藏临时文件、`.trash` 和导入 staging 目录不计入 Pack。

## 3. 两种来源

### 3.1 远程 TONE3000 音色

TONE3000 的来源元数据完整保存在 SQLite，文件夹是可移动的文件资产副本：

- Pack 目录名：`<tone-id>-<title-slug>`；
- Model 文件名优先使用 TONE3000 `models.name`；
- 没有 `models.name` 时回退到下载 URL 的 basename；
- 不在文件名上追加 Model ID，不把语义名称改成无意义编号；
- `.nam` 和 `.wav` 按每个 Model 自身的分类落盘，混合 Pack 不按 Tone 级别统一扩展名；
- 下载成功后生成或更新 `gigbuddy.json`，写入来源信息和当前 Pack 中的 Model 清单；
- 用户手工补充的字段不得在重新下载或刷新时被覆盖。

SQLite 仍是 TONE3000 Pack 的查询索引和下载状态来源。`gigbuddy.json` 是便携 manifest，用于文件夹脱离数据库后保留基本描述；它不是引擎协议，也不是实时处理配置。

远程导入是幂等的，并且按 Model 粒度工作：

- `gigbuddy tone import <tone-id>` 下载这个 Tone 当前允许的 A2 NAM 和 IR
  模型，并把 Tone/Model 元数据写入 SQLite；不支持的 A1、Custom 等模型不
  会进入 GigBuddy 的可用列表。
- 从 Pack 中只选择一个缺失 Model 时，只下载该文件；已有且指纹匹配的文件
  会复用。重新导入可以补齐同一个 Pack 的其他文件。
- 下载先进入隐藏 staging 目录，成功后再发布到 Pack；下载失败不会把半个
  `.nam` / `.wav` 当成已安装文件。
- 远程 Pack 的 `gigbuddy.json` 缺失时会生成；已有 GigBuddy manifest 会保留
  用户手工填写的显示字段和额外字段。损坏或其他软件的同名 JSON 不会被
  GigBuddy 自动覆盖。

### 3.2 用户本地 Pack

用户可以直接创建：

```text
data/tones/my-pack/
  amp-clean.nam
  cab-v30.wav
```

当前使用方式是把文件夹直接放到 `data/tones/` 下。GigBuddy 在启动、切换
LOCAL 页面和文件变化后扫描它，并自动把含有受支持文件的目录列入 LOCAL。
当前没有把项目外文件夹通过 `tone import-local` 导入的命令；项目外的文件
需要用户自行复制到受管理的 `data/tones/<local-pack>/` 目录。GigBuddy 不会
修改、重命名或删除原始文件。安装器会把旧版安装遗留的真实
`<GigBuddy home>/tones/` 迁移到受管理根目录；目标已有内容时拒绝合并，遗留
符号链接不会被跟随。

本地 Pack 不要求 TONE3000 Tone ID、Model ID、作者、标签、下载量或来源 URL。缺失字段使用以下回退：

| 字段 | 缺失时的显示/行为 |
|---|---|
| Pack name | 文件夹名称 |
| Model name | 文件名 |
| gear | `SLOT` |
| author | `LOCAL` |
| tags / makes | 空集合 |
| source | `local` |
| processing type | `.nam` = NAM，`.wav` = IR |
| Tone/Model ID | 不生成伪 TONE3000 ID；使用本地 Pack ID + 相对文件路径 |

这样丢失的只是远程描述，不是音频文件和可执行处理类型。

## 4. `gigbuddy.json` 格式

文件位于 Pack 根目录，文件名固定为 `gigbuddy.json`。它可以缺省，也可以由用户手工编辑。建议的 v1 结构如下：

```json
{
  "schema_version": 1,
  "kind": "gigbuddy-tone-pack",
  "pack": {
    "id": "local-8e5d7d1e-6cf0-4a1c-8b2a-3e3e2e3a9b10",
    "name": "My Princeton Captures",
    "author": "LOCAL",
    "gear": "amp",
    "tags": ["clean", "edge-of-breakup"],
    "makes": ["Fender"],
    "description": "My captures for a small combo setup.",
    "source": {
      "kind": "local",
      "url": null,
      "tone_id": null
    }
  },
  "models": [
    {
      "file": "amp-clean.nam",
      "id": "model-amp-clean",
      "name": "Clean capture",
      "format": "nam",
      "description": "Lower gain setting",
      "metadata": {
        "mic": "SM57",
        "setting": "clean"
      }
    },
    {
      "file": "cab-v30.wav",
      "id": "model-cab-v30",
      "name": "V30 cabinet",
      "format": "ir"
    }
  ],
  "metadata": {}
}
```

### 4.1 解析优先级

解析时遵循固定优先级：

1. 实际文件存在性和扩展名；
2. `models[].file` 对应的文件路径；
3. `gigbuddy.json` 中的 Pack 和 Model 描述；
4. TONE3000 SQLite 元数据（如果该 Pack 带 `source.tone_id`）；
5. 文件名和文件夹名的本地回退值。

`format` 只用于展示和校验提示，最终处理类型仍由文件扩展名决定。manifest 中写错 `format` 时显示 warning，但不能阻止合法文件进入 Slot。

### 4.2 缺失或损坏元数据

- 文件不存在 `gigbuddy.json`：正常扫描所有直接子目录文件；不创建空的虚假 Tone。
- JSON 解析失败：保留文件可见性和加载能力，Pack 显示 warning，并使用文件夹/文件名回退；不自动覆盖用户文件。
- `models` 缺失或某一条目无效：仍扫描目录中所有合法 `.nam/.wav`，无效条目只失去附加描述。
- 未知字段：读取时保留在索引的 `metadata_json` 中，UI 不理解也不丢弃。
- manifest 引用目录外文件：忽略该条目并显示 warning；不允许通过 manifest 绕过 `data/tones` 路径边界。

## 5. 索引与刷新

不建议把本地 Pack 伪装成负数 TONE3000 ID。负数 ID 会污染远程搜索、下载、作者跳转和 `tone_by_id()` 等现有语义。

当前使用两张本地索引表。SQLite 只是可重建索引，文件夹和 manifest 才是用户
资产事实：

```sql
CREATE TABLE local_packs (
    pack_id       TEXT PRIMARY KEY,
    root_path     TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    author        TEXT,
    gear          TEXT,
    tags_json     TEXT,
    makes_json    TEXT,
    description   TEXT,
    source_kind   TEXT NOT NULL DEFAULT 'local',
    source_tone_id INTEGER,
    metadata_json TEXT,
    manifest_sha256 TEXT,
    scanned_at    TEXT NOT NULL
);

CREATE TABLE local_models (
    model_key     TEXT PRIMARY KEY,
    pack_id       TEXT NOT NULL REFERENCES local_packs(pack_id),
    relative_path TEXT NOT NULL,
    name          TEXT NOT NULL,
    format        TEXT NOT NULL,
    size          INTEGER,
    sha256        TEXT,
    metadata_json TEXT,
    scanned_at    TEXT NOT NULL,
    UNIQUE(pack_id, relative_path)
);
```

其中：

- `pack_id` 优先使用 manifest 中的 ID；无 manifest 时由规范化 Pack 路径派生，移动目录后视为新的本地 Pack；导入项目外目录时生成 UUID 并写入新 manifest。
- `model_key` 是 `pack_id + relative_path` 的稳定身份；本地 Model 不使用远程整数 Model ID。
- `format` 只允许 `nam` / `ir`，由后缀生成，不信任 manifest 的同名字段。
- `metadata_json` 保存未被 TUI 识别的额外字段，避免扫描时把用户信息丢掉。
- 每次扫描只更新索引，不重写 Model 文件。文件变化会通过目录、文件大小、
  修改时间和 manifest 指纹触发表格刷新；扫描时会为模型记录 SHA-256。被删除
  的文件从可见列表移除，但不会自动删除 manifest 中用户写的描述。

现有 `tones/models` 表继续服务 TONE3000 来源，不迁移为本地表。TUI 在 LOCAL 视图使用一个统一的 view model：远程 Pack 和本地 Pack 都能显示名称、文件数、类型和状态，但本地 Pack 的动作不调用 TONE3000 API。

## 6. Chain 与 Preset 边界

### Chain

`data/live_chain.json` 保持现有最小协议：

```json
{
  "slots": [
    {"path": "data/tones/local-my-pack/amp-clean.nam"},
    {"path": "data/tones/local-my-pack/cab-v30.wav"}
  ]
}
```

Chain 不写 `tone_id`、`model_id`、gear 或 manifest 的整段内容。音频引擎只关心文件路径和扩展名。UI 通过本地索引反查 Pack/Model，查不到时仍显示 `SLOT` 和文件名。

### Preset

Preset 保存现有 `path`，并对本地 Model 增加可选逻辑引用：

```json
{
  "path": "data/tones/local-my-pack/amp-clean.nam",
  "source": "local",
  "pack_id": "local-8e5d7d1e-6cf0-4a1c-8b2a-3e3e2e3a9b10",
  "relative_path": "amp-clean.nam"
}
```

加载时优先按 `pack_id + relative_path` 找文件；找不到再按保存的 `path` 找。文件路径是最终 fallback，保证用户手工整理文件后仍能尽量恢复，而不把 Tone/Model ID 猜错。

## 7. 本地扫描行为

扫描范围和规则：

- 默认扫描 `data/tones/*/` 的直接子文件；
- 只识别 `.nam` 和 `.wav`；
- 忽略 `.gigbuddy` 临时文件、`gigbuddy.json`、`.part`、`.trash` 和隐藏 staging 目录；
- 不自动移动、重命名或删除用户文件；
- 同一 Pack 重复扫描只更新索引，不复制或重命名文件；
- 已由 SQLite 记录为远程 Tone 的 Pack 目录不会再次作为匿名 LOCAL Pack 显示；
- 只有含有至少一个合法模型文件的目录才会显示为 Pack。

远程下载和手工放入最终都进入同一个 Chain 可访问根目录，因此实时引擎不需要
知道文件来源。

一个普通本地 Pack 可以这样创建：

```text
data/tones/my-pack/
  clean.nam
  v30.wav
```

放入文件后重新打开 LOCAL 页面，或等待 TUI 的文件变化刷新，即可在 Pack 下
看到文件并选择到 Slot。

## 8. 字段丢失的处理原则

本地文件本身不携带统一的 Tone3000 元数据，这是正常情况。GigBuddy 不应伪造下载量、作者、Tone ID 或 Model ID。字段处理分三层：

| 层 | 内容 | 是否影响加载 |
|---|---|---|
| 文件事实 | 文件名、后缀、大小、SHA-256、相对路径 | 影响 |
| 可选 manifest | Pack 名、作者、gear、tags、makes、描述、每个 Model 的备注 | 不影响处理类型 |
| TONE3000 来源 | Tone/Model ID、原始 URL、下载量、原始完整字段 | 不影响引擎加载 |

这样即使没有 `gigbuddy.json`，用户仍然能用文件；有 metadata 时，GigBuddy 能恢复更好的浏览、AB 对比和 Chain 说明。

## 9. 限制与故障边界

- 当前只识别 Pack 根目录的直接子文件，不递归扫描更深层目录；
- 当前只支持 `.nam` 和 `.wav`。`.nam` 按 NAM 处理，`.wav` 按 IR 处理，扩展
  名是实际类型的唯一依据；`gigbuddy.json` 里的 `format` 写错时不能改变
  处理类型；
- GigBuddy 只检查文件后缀、路径和文件是否存在，不保证文件内容一定是有效的
  NAM 或 IR。文件损坏、格式不兼容或采样率不合适，会在引擎加载时失败；
- 本地文件没有 TONE3000 Tone ID、Model ID、作者、下载量等远程信息。没有
  manifest 时使用文件夹名和文件名；不会伪造远程元数据；
- Pack 目录必须位于受管理的 `data/tones/` 根目录内，Chain 协议也会拒绝
  解析后位于该根目录外的路径；
- manifest 是可选的显示元数据，不是引擎配置。缺失或损坏时文件仍可显示和
  尝试加载，但附加描述会退回到文件夹名/文件名；
- 远程卸载会把已管理的模型文件移到 `data/.trash/<operation>/`，保留远程
  SQLite 元数据以便之后重新导入；本地 Pack 不会因为一次扫描就被删除。

## 10. 不做的事情

- 不把所有 `.nam/.wav` 复制到单一平面目录；Pack 文件夹结构保留。
- 不将 gear 写入 `live_chain.json`；gear 只是显示/筛选元数据。
- 不用负数伪造 TONE3000 ID。
- 不要求 metadata 文件存在才能播放或加载。
- 不把 metadata 中的 `format` 当作实际处理类型。
