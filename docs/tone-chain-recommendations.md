# 音色链推荐（GigBuddy 本地库版）

数据快照日期：2026-08-02 ｜ 依据：TONE3000 音色链精选推荐（全站 9281 个 A2 音色筛选，handoff）
> 注意：此为快照数据，本地库可能已更新（以 `gigbuddy tone list` 实际内容为准）
本地库：`data/gigbuddy.db`（33 个音色 / 811 个模型，metadata 1:1 取自 TONE3000 API）

## 命名与路径约定

- 每个音色一个目录：`data/tones/<tone-id>-<title-slug>/`
- 模型文件 = TONE3000 `models.name` 原样（与网站 zip 下载一致，保留空格，无序号），
  如 `data/tones/19-fender-super-reverb-1977/Fender Super Reverb: EQ Flat, Volume 3, sm57.nam`
- 具体文件以 `bin/gigbuddy tone show <id>` 输出为准

```bash
bin/gigbuddy tone import <tone_id>    # 导入（幂等）
bin/gigbuddy chain set '{"model": "data/tones/<id>-<slug>/<模型名>.nam", "ir": "data/tones/<cab-id>-<slug>/<IR名>.wav", "gain": 0.8, "master": 0.8}'
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
| 27465 | Fender Deluxe Reverb Mix Ready | cab | vulturized | 4 | Dumble SSS / ODS #102（全站 cab 下载第一） |
| 51086 | Marshall 1960BV V30 and G12T75 | cab | jpisoutoftune | 55 | SIR #36、JCM800 系 |
| 45022 | Celestion Greenback - Marshall 1960TV 4x12 SM57 | cab | outmodedelectronics | 120 | Plexi / JTM45 系 |
| 45023 | Celestion Vintage 30 - 2002 Mesa Boogie 4x12 - SM57 | cab | outmodedelectronics | 168 | 通用备选 |

---

## 推荐组合（直接成套使用）

Built-in presets are grouped as Band Gear and Classic Pairing, then as Guitar or Bass:

```bash
bin/gigbuddy preset seed --replace    # delete existing presets and rebuild the catalog
bin/gigbuddy preset list              # list presets
bin/gigbuddy preset load band-guitar-rhcp  # load with engine hot-swap
```

| preset | amp model | IR model | description |
|---|---|---|---|
| band-guitar-rhcp | 383442 | — | Frusciante / Marshall Major 200 full rig |
| band-guitar-green-day | 684630 | — | Billie Joe / Marshall 1959BJA full rig |
| band-bass-rhcp | 419198 | — | Flea style / GK RB800 direct |
| band-bass-green-day | 382795 | — | Green Day style / approximate Ampeg SVT-CL pairing, direct |
| classic-guitar-beano | 677999 | — | Bluesbreaker + G12 Alnico full rig |
| classic-guitar-vox-ef86 | 383682 | — | Vox AC30/4 EF86 + 2x12 Alnico full rig |
| classic-guitar-jtm45 | 494341 | 239163 | JTM45 + Marshall 1960TV Greenback |
| classic-guitar-fender-super | 379720 | — | Fender Super Reverb full rig |
| classic-bass-gk-rb800 | 419198 | — | GK RB800 direct |
| classic-bass-ampeg-svt | 382790 | — | Ampeg SVT-CL clean direct |

> 注：gear=amp-cab 的音色模型自带箱体（零 IR）。本地没有贝斯箱体 IR，
> 两条经典贝斯 preset 和两条乐队贝斯 preset 都明确采用直出，不混用吉他箱体 IR。
> 模型文件按旋钮/麦位命名（如 `JCM800 2203 - P5 B5 M5 T5 MV6 G5 - AZG - 700.nam`），
> 选哪个由 `gigbuddy tone show <id>` 按需查看。preset 存逻辑引用（模型 id），
> 库内文件改名/迁移后 `preset load` 依然能解析到当前路径。
