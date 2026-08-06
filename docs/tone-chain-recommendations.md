# 音色链推荐（GigBuddy 本地库版）

数据快照日期：2026-08-02 ｜ 依据：TONE3000 音色链精选推荐（全站 9281 个 A2 音色筛选，handoff）
> 注意：此为快照数据，本地库可能已更新（以 `gigbuddy tone list` 实际内容为准）
本地库：`data/gigbuddy.db`（33 个音色 / 811 个模型，metadata 1:1 取自 TONE3000 API）

## 命名与路径约定

- 每个音色一个目录：`data/tones/<tone-id>-<title-slug>/`
- 模型文件 = TONE3000 `models.name` 原样（与网站 zip 下载一致，保留空格，无序号），
  如 `data/tones/19-fender-super-reverb-1977/Fender Super Reverb: EQ Flat, Volume 3, sm57.nam`
- 具体文件以 `.venv/bin/gigbuddy tone show <id>` 输出为准

```bash
.venv/bin/gigbuddy tone import <tone_id>    # 导入（幂等）
.venv/bin/gigbuddy chain set '{"model": "data/tones/<id>-<slug>/<模型名>.nam", "ir": "data/tones/<cab-id>-<slug>/<IR名>.wav", "gain": 0.8, "master": 0.8}'
```

---

## 库内音色清单（32 个）

### 🎸 John Mayer 组（清音标杆）

| tone_id | title（API 原样） | gear | 作者 | 模型数 |
|---|---|---|---|---|
| 4658 | Two-Rock SSS Clean BAL DI | amp | amalgamaudio | 1 |
| 29285 | Dumble Steel String Singer | amp | wendycabs | 3 |
| 30435 | Dumble ODS #102 Ford Hyper Accuracy+ | amp | slamminmofo | 83 |
| 5691 | Fender '65 Princeton Reverb | amp | philvr | 5 |
| 19 | Fender Super Reverb 1977 | amp-cab | tone3000 | 3 |
| 51649 | Deluxe Reverb '65 Reissue Clean | amp-cab | gindabestari | 6 |

### 🔴 RHCP 组（Frusciante / Flea）

| tone_id | title | gear | 作者 | 模型数 |
|---|---|---|---|---|
| 38981 | John Frusciante Amps | experimental | padoaudio | 3 |
| 51310 | Marshall Major 200 Plexi Lead 1968 | amp-cab | amalgamaudio | 1 |
| 1789 | Boss CE-1 Pre Amp | pedal | chrisedmo | 1 |
| 2694 | Gallien Krueger - RB800 (0.7.0) | amp | arlingtonaudio | 19 |
| 10912 | Roland JC 120B Jazz Chorus | amp-cab | tone3000 | 6 |

### 💚 Green Day 组（Billie Joe / Mike Dirnt）

| tone_id | title | gear | 作者 | 模型数 |
|---|---|---|---|---|
| 78832 | Marshall 1959BJA | amp-cab | rjcproductions | 1 |
| 43379 | 1959 Super Lead Plexi (Dookie Mod) | amp | ripper | 4 |
| 45809 | AMPEG SVT-CL BASS HEAD | amp | deathblossomaudio | 12 |

### 🏛️ 经典吉他大师组

| tone_id | title | gear | 作者 | 模型数 | 关联 |
|---|---|---|---|---|---|
| 77706 | 1966 Marshall 1962 Bluesbreaker (JTM45) with G12 Alnico - CRUNCH - A2 | amp-cab | amalgamaudio | 1 | Clapton |
| 65578 | Marshall JMP-50 Lead 1969 Plexi A2 | amp-cab | amalgamaudio | 1 | Clapton / Page |
| 72145 | 1968 Marshall Super Lead 12 000 Series Drive A2 | amp-cab | amalgamaudio | 1 | Page |
| 53601 | VOX AC30/4 1961 Fawn EF86 | amp-cab | amalgamaudio | 1 | Beatles |
| 6379 | Marshall 1959 BRBS SIR #36 Slash [Standard] | amp | slamminmofo | 51 | Slash |
| 1071 | Marshall JCM 800 2203 | amp | arthm | 30 | Slash |
| 33505 | Marshall JTM 45 [Hyper Accuracy+] | amp | slamminmofo | 99 | 通用 Plexi |
| 31267 | VOX AC30 CH [Hyper Accuracy+] | amp | slamminmofo | 96 | May / Gallagher |
| 6233 | Hiwatt Custom 100 DR 103 1974 | amp | daweed | 13 | Gilmour / The Who |
| 38613 | Angus Young Plexi | amp | padoaudio | 3 | AC/DC |
| 26459 | Marshall 1959 SLP | amp-cab | alessandrozanca | 2 | Hendrix / Page |

### Pedal 组（过载/失真/合唱）

| tone_id | title | gear | 作者 | 模型数 |
|---|---|---|---|---|
| 45294 | Pro Co RAT 2 | pedal | stomptones | 4 |
| 5933 | GILMOUR TONE | pedal | pinkflor | 3 |
| 26841 | JHS @ Andy Timmons | pedal | slamminmofo | 10 |

### 📦 IR 配套（cab，全平台通用）

| tone_id | title | gear | 作者 | 模型数 | 用途 |
|---|---|---|---|---|---|
| 27465 | Fender Deluxe Reverb Mix Ready | cab | vulturized | 4 | ODS #102（全站 cab 下载第一） |
| 51086 | Marshall 1960BV V30 and G12T75 | cab | jpisoutoftune | 55 | SIR #36、JCM800 系 |
| 45022 | Celestion Greenback - Marshall 1960TV 4x12 SM57 | cab | outmodedelectronics | 120 | Plexi / JTM45 系 |
| 45023 | Celestion Vintage 30 - 2002 Mesa Boogie 4x12 - SM57 | cab | outmodedelectronics | 168 | 通用备选 |

---

## 推荐组合（直接成套使用）

Built-in presets are the classic amp rigs, grouped as Classic Pairing · Guitar / Bass.
首次运行 `gigbuddy` 会自动下载模型并建好全部 14 条 preset（见下节
「首次运行自动初始化」）；需要手动重建时：

```bash
.venv/bin/gigbuddy preset seed --replace    # delete existing presets and rebuild the catalog
.venv/bin/gigbuddy preset list              # list presets
.venv/bin/gigbuddy preset load classic-guitar-plexi  # load with engine hot-swap
```

| preset | note | amp model | IR |
|---|---|---|---|
| classic-guitar-plexi | Classic Pairing · Guitar — Marshall JMP-50 Lead 1969 Plexi，crunch 全 rig（@amalgamaudio verified） | 418470 | — |
| classic-guitar-jcm800 | Classic Pairing · Guitar — Marshall JCM800 2203 Modified（EL34）高增益全 rig（@2dor verified） | 567060 | — |
| classic-guitar-jtm45 | Classic Pairing · Guitar — Marshall JTM45 1964 Block Logo 全 rig（R121/R160/U87 拾 1966 4x12 Alnico） | 667990 | — |
| classic-guitar-super-reverb | Classic Pairing · Guitar — Fender Super Reverb 1977 清音（EQ flat, Vol 3，官方 @tone3000） | 379727 | — |
| classic-guitar-deluxe-reverb | Classic Pairing · Guitar — Fender Deluxe Reverb，Vibrato 通道清音（NAM 作者 @sdatkinson） | 385845 | — |
| classic-guitar-twin-reverb | Classic Pairing · Guitar — Fender Twin Reverb（@timr），Vibrato Bright + JBL D120 2x12 IR | 418200 | 156871 |
| classic-guitar-ac30 | Classic Pairing · Guitar — Vox AC30 CH 全开（Dallas Rangemaster）+ Celestion Blue 2x12 IR | 494010 | 106589 |
| classic-guitar-hiwatt-dr103 | Classic Pairing · Guitar — Hiwatt Custom 100 DR103 1974（@daweed），Volume 8 + Hiwatt 4x12 Fane IR | 418892 | 81156 |
| classic-guitar-dumble-ods | Classic Pairing · Guitar — Dumble ODS #102（Robben Ford），PAB clear drive + open-back 1x12 IR | 418380 | 139268 |
| classic-guitar-mesa-badlander | Classic Pairing · Guitar — Mesa Boogie Badlander 100W EL34（@2dor verified），Murder Tones crush + Mesa 4x12 OS SM57 IR | 564451 | 53740 |
| classic-bass-gk-rb800 | Classic Pairing · Bass — Gallien-Krueger RB800（@arlingtonaudio），直出 DI（Flea 风格，免 IR） | 419198 | — |
| classic-bass-svt | Classic Pairing · Bass — Ampeg SVT Classic 带 6x10，Gain 10 Ultra Lo/Hi（官方 @tone3000） | 379990 | — |
| classic-bass-bassman-50 | Classic Pairing · Bass — Fender Bassman 50 1968（@arlingtonaudio），jumped channels G6 + 1970 Bassman 2x15 CTS SM57 IR | 413107 | 352839 |
| classic-bass-ampeg-b15 | Classic Pairing · Bass — Ampeg B-15 64 Portaflex（@ivanbassist），Flat EQ light comp | 381194 | — |

> 注：gear=amp-cab 的 capture 自带箱体（combo 或整机麦拾，零 IR）；纯 amp /
> DI 直录的 capture 显式挂匹配的 cab IR（品牌对应：Twin→JBL D120、AC30→
> Celestion Blue、Hiwatt→4x12 Fane、Dumble→1x12、Badlander→Mesa 4x12 OS、
> Bassman→2x15 CTS）。贝斯按惯例：GK 直出（作者建议免 IR）、SVT 自带 6x10、
> B-15 自带箱体。选型标准为「经典型号 + 权威音源（官方/verified/顶级
> creator）+ 社区热度（下载/收藏）」，14 条覆盖吉他清音 → 边缘过载 → crunch →
> 高 headroom → 高增益与贝斯清亮/复古/驱动风格链。模型文件按旋钮/麦位命名，
> 选哪个由 `gigbuddy tone show <id>` 按需查看。preset 存逻辑引用（模型 id），
> 库内文件改名/迁移后 `preset load` 依然能解析到当前路径。

### 选型说明（v0.1.0 经典音箱重选）

目录从「吉他手关联」改为「纯经典音箱」视角：吉他手标志音色依赖效果器链，
关联不严谨；音箱型号 + capture 设置本身即可代表经典音色。10 条经典吉他
音箱 + 4 条经典贝斯音箱；音源优先官方账号（@tone3000 ×2）与 verified 作者
（@amalgamaudio ×3、@2dor ×3），其次 NAM 项目作者（@sdatkinson）与顶级
creator（@slamminmofo ×2、@arlingtonaudio ×2、@vulturized、@daweed、
@ivanbassist、@timr、@wendycabs、@leomoraes、@maestrodimusica）。

旧库中按旧名 seed 的 preset（band-* 7 条、classic-guitar-beano、
classic-guitar-brian-may、classic-guitar-fender-* 等 15 条旧目录）升级后
不会自动删除——属已知残留。运行
`gigbuddy preset seed --replace` 会删除全部 preset 并按新清单重建目录；
被替换 tone 的模型文件保留在 `data/tones/`（不回收，可继续在 TUI 中
手动选用）。

## 首次运行自动初始化（default presets）

首次运行 `gigbuddy`（CLI 任意子命令或 TUI 启动）会自动下载上述 14 条内置
preset 精确引用的 **19 个模型**（14 个 amp 模型 + 5 个箱体 IR，约 5.6MB），
并 seed 出全部 14 条 preset。这是**一次性**流程：

- **幂等**：settings 表 `default_presets_initialized` 标记已写则直接跳过；
  CLI 与 TUI 共用同一标记。全部模型就绪并 seed 成功后才写标记。
- **触发点**：CLI 子命令执行前同步触发；TUI 启动时由守护线程异步触发，
  不阻塞渲染。模型只按 SEED_CHAINS 引用的模型 id 做子集下载，不会拉整个 pack。
- **失败语义**：网络/API 错误只打印提示、不写标记、不中断当前命令；
  下次启动自动重试，已下载的文件由 download() 幂等跳过，仍缺失的模型由
  preset_seed() 跳过（其余照常 seed）。
- **CLI 进度输出**：逐文件一行，格式
  `[default presets] <done>/<total>  <文件名>`。手动 `gigbuddy preset seed`
  完成时另有 `Seeded <n>/<len(SEED_CHAINS)> presets.` 汇总行；
  TUI 初始化完成时在右上角通知 `Seeded <n> default preset(s)`。
