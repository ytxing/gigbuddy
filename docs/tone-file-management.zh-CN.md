# GigBuddy 音色文件管理

**语言：** [English](tone-file-management.md) | [中文](tone-file-management.zh-CN.md)

这份指南面向 GigBuddy 用户，说明音色文件放在哪里、如何从
TONE3000 下载 Tone Pack、如何加入自己的本地 Pack，以及 LOCAL、音色链和
preset 如何处理这些文件。

## 先记住这几条

- 一个 Tone Pack 就是 `data/tones/` 下的一个文件夹。
- Pack 根目录下的 `.nam` 文件按 NAM 模型处理。
- Pack 根目录下的 `.wav` 文件按 IR 处理。
- `gigbuddy.json` 是可选的元数据，不能改变文件扩展名代表的类型。
- 远程导入由 GigBuddy 从 TONE3000 下载；本地导入就是把文件夹复制到
  `data/tones/`。
- 只扫描 Pack 根目录的直接子文件，不递归扫描更深层目录。

## 找到受管理的音色目录

目录位置取决于安装方式：

| 安装方式 | 音色 Pack 目录 |
|---|---|
| 源码 checkout | `<checkout>/data/tones/` |
| 默认用户安装 | `~/.local/share/gigbuddy/data/tones/` |
| 自定义安装位置 | `<GigBuddy home>/data/tones/` |

同一个 GigBuddy home 下还会有本地索引 `data/gigbuddy.db`、当前音色链
`data/live_chain.json`，以及 `data/presets/` 下可编辑的 preset 文件。

不要把 Pack 放在 `~/.local/share/gigbuddy/tones/`，也不要放在源码目录的
`src/` 旁边。GigBuddy 只扫描受管理的 `data/tones/` 根目录。

## 从 TONE3000 远程导入

如果需要保留 TONE3000 元数据、来源链接和作者信息，并让 GigBuddy 跟踪下载
状态，请使用远程导入。

### 在 TUI 中导入

1. 打开 `TONE3000` 视图。如果提示认证，先登录自己的 TONE3000 账号。
2. 搜索 Tone，打开它的 Pack/Model 视图。
3. 选择整个 Pack，或只选择想保留的 A2/IR 模型。
4. 确认安装。下载完成后，文件会出现在 `LOCAL`。

### 使用 CLI 导入

在源码 checkout 中：

```sh
bin/gigbuddy tone login
bin/gigbuddy tone search "fender super reverb"
bin/gigbuddy tone import <tone-id>
bin/gigbuddy tone list
bin/gigbuddy tone show <tone-id>
```

用户目录安装版使用 `gigbuddy`：

```sh
gigbuddy tone search "fender super reverb"
gigbuddy tone import <tone-id>
```

搜索结果里的数字就是要导入的 Tone ID。重复导入同一个 Tone 不会创建
第二个 Pack。GigBuddy 会下载受支持的 A2 NAM 与 IR 文件，把完整远程元数据
写入本地库，后续仍使用同一个 Pack 目录。

### 文件保存在哪里

```text
data/tones/123-fender-super-reverb/
  gigbuddy.json
  Clean SM57.nam
  4x12 V30.wav
```

文件名优先使用 TONE3000 提供的语义名称，不额外追加 Model ID 或序号。如果
API 没有语义名称，则回退到下载 URL 的文件名。

### 部分导入与重复导入

TUI 可以只安装 Pack 中选定的模型。部分安装是正常状态：元数据可能描述多
个模型，但当前磁盘上只有其中一部分。以后再次导入同一个 Tone，可以补齐
缺失文件。

GigBuddy 会先检查本地记录、文件大小、语义名称、来源 URL 和 SHA-256；匹配的
文件直接复用。新文件先下载到隐藏的临时目录，随后才移入 Pack 目录。
传输失败时，GigBuddy 不会把半截 `.nam` 或 `.wav` 文件标记为已安装。

远程导入还会创建或更新 `gigbuddy.json`。已有 GigBuddy manifest 中用户修改
的显示字段和未知字段会保留。损坏的 JSON 或其他软件的同名 JSON 不会被自动
覆盖，也不会因此隐藏合法音色文件。

## 加入本地 Tone Pack

本地导入就是把已有文件夹复制到受管理的音色库中。本版本没有
`gigbuddy tone import-local` 命令。

源码 checkout：

```sh
mkdir -p data/tones/my-pack
cp "/path/to/my-clean-capture.nam" data/tones/my-pack/
cp "/path/to/my-v30-cab.wav" data/tones/my-pack/
```

默认用户安装：

```sh
mkdir -p ~/.local/share/gigbuddy/data/tones/my-pack
cp "/path/to/my-clean-capture.nam" \
  ~/.local/share/gigbuddy/data/tones/my-pack/
cp "/path/to/my-v30-cab.wav" \
  ~/.local/share/gigbuddy/data/tones/my-pack/
```

也可以直接复制整个 Pack 文件夹。Pack 文件夹本身必须直接位于
`data/tones/` 下：

```text
data/tones/my-pack/
  clean.nam
  v30.wav
```

GigBuddy 不会修改、移动或删除你复制过来的原始文件。复制完成后切到
`LOCAL`、重新打开 LOCAL 视图，或等待文件变化刷新。目录的直接子文件中至少
有一个受支持文件时，才会显示为 Pack。

## 文件规则

| 文件 | GigBuddy 类型 | 是否扫描 |
|---|---|---|
| `capture.nam` | NAM 模型 | 是 |
| `cabinet.wav` | IR | 是 |
| `capture.NAM` | NAM 模型 | 是 |
| `notes.txt` | 无 | 否 |
| `subdir/capture.nam` | 无 | 否，嵌套文件忽略 |
| `.capture.nam` | 无 | 否，隐藏文件忽略 |
| `.part` 或临时文件 | 无 | 否 |
| `gigbuddy.json` | Pack 元数据 | 单独读取 |

类型由扩展名决定。`.nam` 在 LOCAL 里作为 NAM/A2 类本地资产显示，`.wav`
作为 IR 显示。GigBuddy 只检查路径、扩展名和文件是否存在，不会在原生引擎
加载前校验文件内容是否为有效 NAM 或 IR。

## 可选的 `gigbuddy.json`

没有 manifest 也能使用本地 Pack。需要固定 Pack 身份、友好的 Pack 名称、作者、
标签或文件备注时，可以在 Pack 根目录添加 `gigbuddy.json`：

```json
{
  "schema_version": 1,
  "kind": "gigbuddy-tone-pack",
  "pack": {
    "id": "local-my-princeton-pack",
    "name": "My Princeton captures",
    "author": "Me",
    "gear": "amp",
    "tags": ["clean", "edge-of-breakup"],
    "makes": ["Fender"],
    "description": "Captures for my small-combo setup.",
    "source": {
      "kind": "local",
      "url": null,
      "tone_id": null
    }
  },
  "models": [
    {
      "file": "clean.nam",
      "name": "Clean capture",
      "description": "Lower-gain setting",
      "metadata": {"mic": "SM57"}
    },
    {
      "file": "v30.wav",
      "name": "V30 cabinet"
    }
  ],
  "metadata": {}
}
```

LOCAL 显示文件和 Pack 信息时按以下顺序确定：

1. 磁盘上实际存在的文件和扩展名。
2. manifest 中 `file` 与根目录文件名相同的条目。
3. `gigbuddy.json` 中的 Pack 元数据。
4. 如果是远程 Pack，则使用 TONE3000 元数据。
5. 最后回退到文件夹名和文件名。

`models[].format` 只是显示元数据。它和扩展名冲突时，以扩展名为准。manifest
缺失、损坏或属于其他软件时，会显示元数据问题，但合法的 `.nam` 与 `.wav`
仍然可以使用。

没有 manifest 时，Pack 名称使用文件夹名，模型名称使用文件名，作者显示为
`LOCAL`，Pack 身份由受管理路径派生。manifest 中的 `pack.id` 可以给 Pack
一个固定身份，但移动 Pack 不会自动迁移已有 preset。移动后打开 LOCAL，重新
选择文件并保存受影响的 preset。

## LOCAL、TONE3000、Pack、Model、Chain 和 Preset

- `TONE3000` 是实时远程目录，需要网络和当前用户的 TONE3000 会话。
- `LOCAL` 是已安装的本地库，远程 Pack 和用户复制的本地 Pack 会一起显示。
- Pack 是文件夹级对象，保存显示元数据并包含一个或多个模型文件。
- Model 是 Pack 中的一个 `.nam` 或 `.wav` 文件。
- Chain 是当前有序 Slot 列表，保存文件路径和链参数，不复制整段 Tone 元数据。
- Preset 保存 Chain 快照。对本地文件，能识别时还会保存 Pack 身份和相对文件名，
  加载时按这个身份解析当前路径，而不是猜一个远程 ID。

从 LOCAL 选中本地或远程 Model 后，载入 Slot 的方式相同。只要文件位于
GigBuddy 音色目录内，并且是受支持的 `.nam` 或 `.wav`，来源不会影响载入方式。

## 修改和删除文件

### 添加或替换

把新文件复制到 Pack 根目录，等待 LOCAL 刷新。同名文件被替换后，本地索引会
更新其大小和校验信息。如果文件正被当前音色链使用，先把 Slot 切换到别的文件，
再替换并重新载入。

### 删除本地文件

由用户自行删除或移动文件。下一次扫描会把它从 LOCAL 的 Pack/Model 列表中移除。
扫描不会顺便删除其他本地文件。manifest 里旧条目的描述可能保留，但
文件不存在时不能选择，文件恢复后才会重新出现。

### 卸载远程文件

在 LOCAL 或 Pack/Model 详情中执行卸载。GigBuddy 会检查文件是否正在当前 Chain
中使用，以及是否被 preset 引用；有 preset 引用时需要确认。受管理的远程文件会
先移动到 `data/.trash/<operation>/`，不会立即删除，远程元数据保留，之后可以
再次安装。

只卸载一个模型不会删除 Pack 的其他模型。最后一个远程模型卸载后，Tone 元数据
仍保留，但该 Tone 不再显示为已安装。

如果卸载目标解析到受管理 `data/tones/` 之外，GigBuddy 会拒绝操作，不会接管或
删除磁盘上其他位置的文件。

## 移动 Pack 与 Preset

在 GigBuddy home 内移动 Pack 时，保留 Pack 文件夹名和 manifest。manifest 会
保留 Pack 的元数据，但移动文件夹不会自动迁移已有 preset。移动后打开 LOCAL，
重新选择文件并保存受影响的 preset。

没有 manifest 的 Pack 会按受管理路径生成身份。移动后它会成为新的本地 Pack，
仍指向旧路径的 preset 可能失效；恢复原路径，或重新把新模型保存进 preset 即可。

不要把 Pack 移到 `data/tones/` 之外；移出去就不再受管理。Chain 和 preset 会
拒绝根目录之外的路径。

## 离线与故障行为

- 本地 Pack、已导入文件和已保存 preset 可以离线浏览。
- 远程搜索、创作者页面、模型详情和下载需要网络与有效 TONE3000 会话。
- 缺少 manifest 不会阻止本地文件使用。
- 损坏的 JSON 不会被自动覆盖。
- 不支持的扩展名会被扫描器忽略。
- 内容损坏或不兼容的 NAM/WAV 仍可能出现在 LOCAL，但原生引擎加载时会失败；
  换成有效文件后刷新 LOCAL。
- GigBuddy 不递归扫描文件夹，嵌套文件需要移动到 Pack 根目录。
- 远程导入或卸载时，不要让其他进程同时重命名、替换或删除同一个 Pack。GigBuddy
  不支持其他进程同时进行的文件操作。
