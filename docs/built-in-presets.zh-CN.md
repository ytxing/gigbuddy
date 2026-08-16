# 内置 Preset 使用说明

GigBuddy 随仓库提供 20 套已经校准的吉他与贝斯音色。全新安装不需要先生成
preset 数据库，也不依赖旧安装遗留的 JSON；应用启动后会直接读取仓库中的固定
文件并把它们显示在 **PRESETS** 面板。

## 文件来源

内置文档随版本保存在：

```text
presets/built-in/*.json
```

每个文件都包含稳定的 `catalog_key`、显示名称、说明和整条音色链。Slot 使用
TONE3000 的 `tone_id` 与 `model_id` 标识模型，不保存任何机器相关的本地路径。
NAM Slot 还可以携带按模型 loudness 元数据计算的推荐 `output_gain_db`，模型下载
完成后会继续保留这个校准值。

## 启动与后台下载

启动 GigBuddy 时会先完成一次很快的本地注册：

1. 读取仓库中的 JSON，把名称和音色链登记到本地 SQLite 索引。
2. 立即在 **PRESETS** 面板显示全部内置行；这一步不下载模型，也不会改动当前
   正在使用的 Tone Chain。
3. 后台 worker 按 TONE3000 Tone 合并缺少的模型并尝试下载，不阻塞 TUI 打开。
4. 每个 preset 根据本地模型是否可用更新自己的状态。

状态含义如下：

| 状态 | 含义 |
|---|---|
| `PREPARING` | 后台正在准备该 preset 缺少的一个或多个模型。 |
| `READY` | 该 preset 需要的全部模型都已在本地可用。 |
| `UNAVAILABLE` | 尚未登录、下载失败，或某个模型暂时无法解析。 |
| `USER` | 用户创建、可以编辑的普通 preset。 |

`UNAVAILABLE` 不会让 preset 从列表消失。选择或双击它时，只会在后台重试这一套
音色需要的模型；失败不会覆盖当前 Tone Chain，之后仍可再次重试。只有状态变为
`READY` 后，加载操作才会通过正常的受控写入路径替换当前音色链。

## 安装与登录

一键安装脚本仍会检查 TONE3000 登录，并在用户同意时打开系统浏览器；这个步骤
没有被后台下载机制删除。安装器只登记内置目录，不会等待 20 套音色的远程模型
全部下载完毕。

如果暂时不登录，可以在提示时输入 `n`。安装仍会继续，首次打开 GigBuddy 时
内置 preset 依然会显示，但依赖远程模型的行会保持 `UNAVAILABLE`。之后运行：

```sh
gigbuddy tone login
```

没有交互终端时，安装器会停止并提示需要明确选择。自动化安装若确定要跳过首次
preset 登记，必须显式传入 `--skip-presets`：

```sh
curl -sSL https://raw.githubusercontent.com/ytxing/gigbuddy/v1.2.4/scripts/install.sh | bash -s -- --skip-presets
```

登录完成后，打开 TUI 并加载某一行即可只重试那套音色。需要从 CLI 主动重试
全部内置模型时再运行：

```sh
gigbuddy preset bootstrap
```

该命令会报告未成功下载的数量和名称，不会替换当前 Tone Chain。

## CLI 用法

只列出目录、不下载模型：

```sh
gigbuddy preset list
gigbuddy preset list --json
```

加载一套内置音色：

```sh
gigbuddy preset load marshall-jcm800-klon
```

如果模型尚未下载，CLI 会先尝试准备这一个 preset 需要的模型；成功后才写入
Tone Chain。若准备失败，命令会给出错误，原有音色链保持不变。普通用户 preset
维持原有行为，不会因为加载命令触发整套内置目录下载。

查看当前程序版本：

```sh
gigbuddy --version
```

## 编辑规则

内置行以仓库 JSON 为唯一来源，因此不允许直接重命名、修改、删除，也不能使用
同名的 `preset save` 或 `preset import` 覆盖。要基于某套内置音色继续调整，先
加载它，再使用 **Save As** 以新名称保存。新行会成为普通 `USER` preset，并写入：

```text
data/presets/*.json
```

如果用户已有同名 preset，用户数据优先，仓库同步不会覆盖它。旧版本留下、没有
可靠来源标记的行也继续按用户数据处理；GigBuddy 不会根据名称、备注、文件内容
或听感猜测所有权。

未被数据库跟踪、但与内置名称冲突的 JSON 会移动到：

```text
data/presets/.quarantine/
```

文件不会被删除。需要恢复时，先修改其中的 preset 名称，再把它放回
`data/presets/`。

## 失败时的行为

- 登录缺失或网络失败：相关行保持 `UNAVAILABLE`，TUI 正常打开。
- 单个模型下载失败：只影响引用该模型的 preset，当前 Tone Chain 不变。
- 内置 JSON 无效：CLI 会报告失败的 preset；其他有效行继续可用。
- 同步期间文件发生变化：本次不发布不完整目录，下一次显式刷新重新读取。
- preset 写入中断或出现重名：SQLite 中已提交的数据优先，不确定的 JSON 会进入
  `.quarantine`，不会静默删除用户文件。

内置同步本身从不写 `data/live_chain.json`。模型文件仍通过正常的 TONE3000
staging 和校验路径下载，只有可验证的本地文件才计为 `READY`。

如果仓库 preset 在旧下载任务运行期间发生变化，旧任务的结果会被丢弃。即使
`model_id` 没变，只要 Slot 顺序、增益、bypass 状态或声明的 Tone 来源不同，
也会被视为新一代 preset，不会被旧任务清除或误标为 `UNAVAILABLE`。

没有原生音频引擎时也可以使用目录、下载和 preset 管理：

```sh
gigbuddy --no-engine
```

实时播放和热切换仍需要已构建的引擎与可用声卡。
