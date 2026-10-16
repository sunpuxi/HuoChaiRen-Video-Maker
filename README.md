# StickMan Video Maker

一套以 JSON 分镜驱动的火柴人科普视频渲染工具。画面由 SVG 组件生成，`resvg` 负责稳定栅格化，FFmpeg 负责镜头运动、音频和视频封装。

项目地址：[github.com/sunpuxi/StickMan-Video-Maker](https://github.com/sunpuxi/StickMan-Video-Maker)

## 功能

- 两套可切换的角色画风：线条火柴人（默认）与手绘卡通小人（见「手绘画风」）

- JSON 分镜与项目配置
- 6 种人物姿势与 6 种表情
- 道具：箭头、双向箭头（交换）、红叉、气泡、信息节点、镜子、弧线、星形，以及羊/米/鱼/鞋等极简实物图标
- `static`、`zoom_in`、`zoom_out`、`pan_left`、`pan_right` 镜头
- 4× SVG 超采样、偶数像素裁切与缓入缓出，避免细线推镜抖动
- 镜头时长对齐帧网格，短视频不会因不足一帧的舍入而漂移
- 可选极简背景层：同色系背景纸片、地面线、角色投影与标题下划线，摆位逐镜轮换
- 可选逐帧动画：角色入场、走路循环、眨眼；动画镜头可继续叠加推拉运镜
- macOS 本地中文 TTS、DashScope Qwen3-TTS、MiniMax 语音合成 API，或使用预生成音频
- 短句级配音：每个镜头可拆成多个独立配音和字幕片段
- 配音文本支持停顿标记与语气词，字幕自动剔除这些控制符
- 自动生成 SRT、ASS，并将带样式字幕烧录到发布视频
- 根据真实音频长度自动计算镜头时长
- 图片、音频和镜头三级内容哈希缓存
- 快速预览与正式渲染模式
- 自动生成时间线、联系表和构建报告
- 自动检查分辨率、帧率、音轨和静音问题

## 系统要求

- Python 3.9+
- Node.js 18+
- FFmpeg 与 FFprobe
- macOS `say`（仅使用 macOS 本地配音时需要）
- DashScope API Key（仅使用 Qwen3-TTS 云端配音时需要）
- MiniMax API Key（仅使用 MiniMax 云端配音时需要）

## 安装

```bash
git clone https://github.com/sunpuxi/StickMan-Video-Maker.git
cd StickMan-Video-Maker
npm install
```

项目只使用一个 Node 依赖：`@resvg/resvg-js`。Python 端不需要第三方包。

## 使用 Qwen3-TTS API

管线支持通过阿里云百炼的非实时 HTTP API 调用 `qwen3-tts-flash`。这种模式不需要在本机下载模型或准备 GPU。

先在终端设置 API Key，不要把密钥写进项目文件：

```bash
export DASHSCOPE_API_KEY="你的 API Key"
```

也可以把密钥写进仓库根目录的 `.env` 让 `render.py` 自动加载，见下文「使用 MiniMax 语音合成」。

然后将项目的 `voice` 配置改为：

```json
{
  "voice": {
    "engine": "dashscope_qwen3",
    "model": "qwen3-tts-flash",
    "voice": "Elias",
    "language_type": "Chinese",
    "api_key_env": "DASHSCOPE_API_KEY",
    "timeout": 90,
    "max_retries": 3
  }
}
```

可将 `voice` 换成 `Cherry`、`Serena`、`Neil` 等 Qwen3-TTS 系统音色。`language_type` 应尽量明确指定文本语言；中英混合或语言未知时可以使用 `Auto`。

每个 `narration_segment` 会独立请求语音。返回的临时音频会立即下载、转成 48kHz 单声道 WAV，并进入内容哈希缓存；再次渲染相同文本、模型和音色时不会重复调用 API。API 遇到限流或临时服务错误时会自动重试。

如果使用业务空间专属域名，可在 `voice` 中用完整的 `endpoint` 覆盖默认地址。密钥仅从 `api_key_env` 指定的环境变量读取，不会写入缓存或构建报告。

## 使用 MiniMax 语音合成

管线支持 MiniMax 开放平台的同步语音合成接口（`POST /v1/t2a_v2`，非流式）。渲染是离线批处理，一次请求拿回整段音频即可，因此没有走 WebSocket 流式通道——两者返回的音频一致，而流式的复杂度对成片没有收益。

```bash
export MINIMAX_API_KEY="你的 API Key"
```

不想每次导出，也可以把密钥放在项目根目录的 `.env`（该文件已被 `.gitignore` 忽略）：

```
MINIMAX_API_KEY=sk-api-...
```

`python3 render.py` 启动时会依次读取**项目目录**和仓库根目录下的 `.env`，**已存在的环境变量优先，不会被文件覆盖**。密钥不会写入缓存、构建报告或任何提交内容。

```json
{
  "voice": {
    "engine": "minimax",
    "model": "speech-2.8-hd",
    "voice_id": "male-qn-qingse",
    "speed": 0.95,
    "vol": 1.0,
    "pitch": 0,
    "emotion": "calm",
    "language_boost": "Chinese",
    "text_normalization_mode": "basic",
    "format": "mp3",
    "sample_rate": 32000,
    "api_key_env": "MINIMAX_API_KEY",
    "timeout": 90,
    "max_retries": 3
  }
}
```

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `model` | `speech-2.8-hd`（默认）、`speech-2.8-turbo`、`speech-2.6-hd`、`speech-2.6-turbo`、`speech-02-hd`、`speech-02-turbo`、`speech-01-hd`、`speech-01-turbo` | `hd` 适合成片，`turbo` 更快更便宜 |
| `voice_id` | 系统音色、复刻音色或 `timbre_weights` 混音 | 用控制台的音色列表或 Get Voice API 查询 |
| `speed` / `vol` / `pitch` | `0.5–2` / `(0, 10]` / `-12–12` | 建议语速留在 `0.9–1.05`，加速会连同韵律一起压扁 |
| `emotion` | `happy`、`sad`、`angry`、`fearful`、`disgusted`、`surprised`、`calm`、`fluent`、`whisper`（后两者仅 2.6 系列） | 不填时模型按文本自动选择，通常比手动指定更自然 |
| `language_boost` | 语言名或 `auto`（默认） | 中英混排时用 `auto` |
| `text_normalization_mode` | `basic`、`quality` | `quality` 用 LLM 归一化数字/日期/货币，仅非流式且 2.6/2.8 生效 |
| `format` / `sample_rate` / `bitrate` / `channel` | `mp3`/`pcm`/`flac`/`wav`、`8000`–`44100`、`32000`–`256000`（仅 mp3）、`1`/`2` | 管线最终统一转成 48kHz 单声道 WAV |

音频里可以直接写控制标记，模型会照做，字幕不会显示它们：

| 写法 | 作用 |
| --- | --- |
| `<#0.4#>` | 在该处停顿 0.4 秒，范围 `0.01–99.99`，可用于句读呼吸 |
| `(sighs)` `(laughs)` `(breath)` `(emm)` … | 语气词，仅 `speech-2.8-hd` / `speech-2.8-turbo` 支持 |
| `处理/(chu3)(li3)` | 通过 `pronunciation_dict` 指定多音字读法，也支持 IPA 与粤语拼音 |

```json
{
  "narration_segments": [
    { "text": "你有没有想过<#0.3#>一件很怪的事？", "pause_after": 0.25 },
    { "text": "钱，到底是什么(sighs)？", "caption": "钱，到底是什么？" }
  ]
}
```

`text` 是交给 TTS 的文字，`caption` 是字幕文字；只写 `text` 时，管线会自动去掉 `<#…#>` 与语气词标记再生成字幕。

缓存键包含模型、音色、`speed`/`vol`/`pitch`/`emotion`/归一化等所有影响音频的参数，因此调参后会自动重新合成，未变的镜头继续命中缓存。密钥只从 `api_key_env` 读取。华北地区可以把 `endpoint` 换成 `https://api-bj.minimaxi.com/v1/t2a_v2` 降低首包延迟；接口返回业务错误码时（`1001` 超时、`1002` 限流、`1039` TPM 限流）会按 `max_retries` 退避重试，其余错误立即失败并打印 `status_code` 与 `status_msg`。

## 渲染示例

快速预览：

```bash
python3 render.py projects/clothing --preview
```

正式输出：

```bash
python3 render.py projects/clothing --final
```

跳过配音，以静音方式快速检查画面：

```bash
python3 render.py projects/clothing --preview --mute
```

忽略缓存、强制重新渲染：

```bash
python3 render.py projects/clothing --preview --force
```

输出位于：

```text
builds/clothing/preview/
├── clothing_explainer_preview.mp4
├── clothing_explainer_preview_clean.mp4
├── subtitles.srt
├── subtitles.ass
├── contact_sheet.png
├── timeline.json
├── build_report.json
└── scenes/
```

## 项目结构

```text
StickMan-Video-Maker/
├── render.py
├── storyboard.schema.json
├── pipeline/
│   ├── build.py
│   ├── config.py
│   ├── handdraw.py
│   ├── ink.py
│   ├── media.py
│   └── svg.py
├── scripts/
│   └── render_svg.cjs
├── projects/
│   └── clothing/
│       ├── project.json
│       ├── storyboard.json
│       └── audio/
├── .cache/
└── builds/
```

## 新建视频

复制示例项目：

```bash
cp -R projects/clothing projects/my-video
```

然后修改：

- `project.json`：分辨率、帧率、发音人、输出名称
- `storyboard.json`：旁白、标题、人物、道具和镜头运动

如果删除场景中的 `audio_file`，程序会调用 `project.json` 中配置的 TTS 引擎自动生成旁白。保留 `audio_file` 则使用预生成音频，适合真人或第三方配音。

## 最小场景

```json
{
  "id": "scene_01",
  "title": "你今天穿了什么？",
  "narration": "你今天穿了什么？",
  "caption": "你今天穿了什么？",
  "camera": "zoom_in",
  "characters": [
    {
      "x": 640,
      "y": 240,
      "scale": 0.7,
      "pose": "look_down",
      "expression": "confused",
      "shirt": "#6599D1"
    }
  ],
  "props": []
}
```

完整字段约束见 `storyboard.schema.json`。

## 极简背景层

默认关闭。在 `project.json` 中开启后，每个镜头会增加四层低干扰背景元素，用于消除纯色平涂的单调感：

```json
{
  "background_style": {
    "enabled": true,
    "paper_shape": "auto",
    "ground": true,
    "title_stroke": true,
    "character_shadows": true
  }
}
```

- `paper_shape`：同色系加深 6.5% 的大色块，制造前后层次。`auto` 按镜头顺序在 4 种摆位之间轮换，保证相邻镜头不重复；也可取 `none`、`circle_top_right`、`circle_bottom_left`、`band_bottom`、`circle_top_left`
- `ground`：角色脚下的地面线，自动避让角色脚底与低处道具
- `title_stroke`：标题下划线，浅色背景用品牌黄，饱和背景按对比度自动转深墨色
- `character_shadows`：角色脚下的椭圆投影，随位置和缩放自动生成

四个开关可以单独关闭；单个镜头可用 `"background_style": false` 整体关闭，或用 `"paper_shape": "circle_top_left"` 单独指定摆位。**关闭时渲染输出与不含该功能时逐字节一致**，已有缓存不会失效。

背景可以逐镜换色来形成节奏：相邻镜头使用不同底色（例如 `#FFFDF7`、`#EAF6FF`、`#F6EFE2`、`#E9F4DD`），把 `#F6B95C`、`#CFEBC3` 这类饱和度较高的颜色留给需要强调的镜头。

## 手绘画风（可选）

默认是线条火柴人（`art_style: "line"`）。改成 `"hand"` 后角色由引擎重绘成手绘卡通小人：豆形头、大眼、粗眉、手绘描边。

```json
{ "art_style": "hand" }
```

**每一根线都不是 `stroke`。** 线条火柴人用的是 `stroke-width="12"` 的均匀描边 —— 这正是矢量图看起来像机器画的原因。手绘引擎把每根线换成**填充轮廓**：中心线被噪声横向推移，宽度沿路径呼吸，两端收笔。噪声由稳定字符串播种（`zlib.crc32`，不是会随机化的 `hash()`），所以同一场戏每次渲染逐字节一致，三级缓存照常工作。

角色是一组参数，不是硬编码图形：

| 字段 | 取值 |
|---|---|
| `hair` | `spikes` `bowl` `ring` `flat` `afro` `combover` `none` |
| `brow` | `worried` `angry` `raised` `flat` `unibrow`（默认由 `expression` 推导） |
| `nose` | `hook` `potato` `pointy` `tiny` |
| `mouth` | `flat` `grin` `squiggle` `open` `frown`（默认由 `expression` 推导） |
| `outfit` | `fur` `shirt` `tie` `overalls` `robe` `none` |
| `ears` | `normal` `big` `none` |
| `hair_colour` `accent` `skin` `shirt` | 颜色 |

`style` 字段可在单个角色上覆盖项目设置。所有取值都有默认值，未知值会回退而不是让渲染失败。

```json
{
  "x": 380, "y": 300, "scale": 0.68, "pose": "point", "expression": "smile",
  "hair": "spikes", "nose": "hook", "outfit": "fur", "shirt": "#C79A5C"
}
```

**道具、背景层也走同一支笔。** `art_style: "hand"` 时，9 种道具（箭头、双向箭头、叉号、气泡、节点、镜子、弧线、星形、图标）以及四色纸片背景、地面线、标题下划线全部换成手绘版本，由 `pipeline/handprops.py` 绘制。否则画面里会出现「手绘的人 + 机器画的碗」，比缺功能更显眼。

图标沿用原来的 0..100 盒子，所以 `size` 的含义不变；线宽按缩放倒数取值，大图标不会比小图标看起来更粗。

一个容易踩的坑：`ink.chain()` 把 3 个点当作**二次曲线**。三角形的三个角如果直接丢进 `chain()`，会被弯成一片透镜形状 —— 箭头会无声地消失。需要直边的形状要用 `handprops._polygon()`。

**两套画风可以共存。** 手绘人物的比例与线条人物不同，脚底落点也不同，所以地面线和投影按每个角色自己的画风取偏移（`handdraw.METRICS`），不需要为了迁就而伪造坐标。

**缓存注意**：动态场景的片段是从「不含角色的底图 + 逐帧透明层」合成的，画风不会进入 `image_key`。因此切换画风时 `art_style_key()` 会把画风写进片段缓存键，否则会把旧画风的片段当成命中直接用掉。默认画风返回 `None`，已有缓存不受影响。

示例项目见 `projects/hand-demo/`：

```bash
python3 render.py projects/hand-demo --preview --mute
```

## 逐帧动画（可选）

默认关闭。给场景加 `animation` 后，该镜头不再用单张图片，而是**逐帧用代码画出来**：角色可以走进画面、原地走路、眨眼。

```json
{
  "id": "scene_05",
  "title": "第二层皮肤",
  "animation": { "enabled": true, "blink": true, "oversample": 2 },
  "characters": [
    {
      "x": 640, "y": 255, "scale": 0.68, "pose": "walk", "shirt": "#7D9FC6",
      "animation": { "preset": "walk_in", "from_x": -200, "duration": 1.4,
                     "walk_speed": 1.8, "walk_cycle": true }
    }
  ]
}
```

角色入场预设：

| preset | 效果 |
|---|---|
| `walk_in` | 从 `from_x` 走到自己的 `x`，期间腿臂摆动；`walk_cycle` 为真时到位后继续原地走 |
| `slide_in` | 从 `from_x` 平滑滑入，不摆腿 |
| `pop_in` | 原地从 0 缩放弹出（带回弹缓动） |
| `none` | 不动（默认） |

- 角色级：`preset`、`start`、`duration`、`from_x`、`walk_speed`、`walk_cycle`、`blink`
- 场景级：`enabled`、`blink`（默认开）、`blink_period`（默认 3.1 秒）、`walk_cycle`（默认开）、`oversample`（默认 2）

实现要点：画面被拆成**静态层**（背景、背景纸片、标题、地面线、带文字的 node/bubble 道具）和**动画层**（角色与纯矢量道具）。静态层每镜只渲一次；动画层逐帧渲成透明 PNG，且不含任何文字，所以能跳过系统字体扫描——这正是逐帧渲染从 544 ms/帧降到约 60 ms/帧的原因。`oversample` 只作用于动画镜头，静止镜头仍用 `project.motion_oversample`（默认 4×）。没有 `animation` 的场景走原来的单图路径，不产生额外开销。

## 道具语义

同一套线条符号要保持含义稳定，否则观众会误读（例如用单向箭头表示"交换"）：

| 符号 | 含义 | 不用于 |
|---|---|---|
| `arrow` 箭头 | 流向、导致、下一步 | 交换、对等 |
| `exchange` 双向箭头 | 交换、互惠 | 单向流程 |
| `cross` 红叉 | 否定、错误 | 缺失 |
| `node` 节点 | 抽象概念、标签 | 实物 |
| `bubble` 气泡 | 话语、思考 | 实物 |
| `icon` 实物图标 | 具体物件 | 抽象概念 |
| `mirror` / `arc` / `star` | 自我审视 / 强调 / 点缀 | 承载信息 |

实物图标（`type: "icon"`）目前内置 `sheep`、`rice`、`fish`、`shoes`，画在 100×100 的局部坐标里、以 `size` 定宽，线宽会自动补偿，因此放大缩小都保持与角色一致的笔画粗细：

```json
{ "type": "icon", "name": "sheep", "x": 520, "y": 265, "size": 150,
  "animation": { "preset": "pop", "start": 1.9, "duration": 0.5 } },
{ "type": "exchange", "x1": 640, "y1": 265, "x2": 790, "y2": 265,
  "animation": { "preset": "grow", "start": 2.15, "duration": 0.5 } }
```

道具动画支持 `pop`（缩放弹出，带回弹）和 `grow`（由起点生长，适合箭头），`start` / `duration` 以秒计。含道具动画的镜头会自动走逐帧渲染路径。

约定：**同屏实物图标不超过 2 个**（最多 3 个），装不下就拆镜；画面画不出的抽象概念退回 `node` / `bubble` 文字标签，不硬画。

## 分段配音与字幕

需要让字幕跟随配音短句切换时，在场景中增加 `narration_segments`。每一段会独立生成并缓存配音，程序根据音频真实长度生成字幕时间轴：

```json
{
  "id": "scene_01",
  "title": "你今天穿了什么？",
  "narration_segments": [
    {
      "text": "你今天穿了什么？",
      "pause_after": 0.18
    },
    {
      "text": "我们每天都会穿衣服，",
      "caption": "我们每天都会穿衣服，",
      "pause_after": 0.08
    },
    {
      "text": "却很少认真想过原因。",
      "pause_after": 0.2
    }
  ],
  "camera": "zoom_in",
  "characters": [],
  "props": []
}
```

- `text`：交给 TTS 的文字，也是未设置 `caption` 时的字幕文字；其中的 `<#0.4#>`、`(sighs)` 等语音控制标记不会出现在字幕里。
- `caption`：可选的显示文本，适合简化口语或手动换行；设置后完全按原样显示。
- `audio_file`：可选的片段音频；设置后该片段不调用 TTS。
- `pause_after`：片段结束后的静音秒数，字幕会保持到停顿结束。

场景级 `audio_file` 与 `narration_segments` 不能同时使用。若要使用预录配音，请给每个片段分别配置 `audio_file`。

字幕样式在 `project.json` 中配置：

```json
{
  "subtitles": {
    "enabled": true,
    "font_name": "PingFang SC",
    "font_size": 42,
    "color": "#FFFFFF",
    "background_color": "#171717B8",
    "outline_color": "#171717",
    "outline": 2,
    "shadow": 1,
    "margin_bottom": 34
  }
}
```

`font_size`、`outline` 和 `margin_bottom` 以 720p 为参考尺寸，预览和正式输出会按画面高度自动缩放。`enabled` 为 `false` 时仍生成字幕文件，但不会烧录到发布视频。

若 FFmpeg 包含 `ass` 滤镜，管线会直接烧录 ASS；否则会自动使用 `resvg` 生成透明字幕图层并按时间叠加，无需额外安装 `libass`。

## 缓存策略

- 旁白缓存键：文字、发音人、语速或音频文件内容
- 图片缓存键：SVG 内容、分辨率、字体和渲染器版本
- 镜头缓存键：图片、音频、时长、镜头运动、帧率和质量模式

修改一个镜头时，其余未变化镜头会直接复用缓存。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 开源协议

本项目采用 [MIT License](LICENSE)。
