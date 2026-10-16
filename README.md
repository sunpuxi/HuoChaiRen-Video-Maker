# HuoChaiRen Video Maker

一套以 JSON 分镜驱动的火柴人科普视频渲染工具。画面由 SVG 组件生成，`resvg` 负责稳定栅格化，FFmpeg 负责镜头运动、音频和视频封装。

项目地址：[github.com/sunpuxi/HuoChaiRen-Video-Maker](https://github.com/sunpuxi/HuoChaiRen-Video-Maker)

## 功能

- JSON 分镜与项目配置
- 6 种人物姿势与 6 种表情
- 箭头、红叉、气泡、信息节点、镜子、弧线和星形道具
- `static`、`zoom_in`、`zoom_out`、`pan_left`、`pan_right` 镜头
- 4× SVG 超采样、偶数像素裁切与缓入缓出，避免细线推镜抖动
- 镜头时长对齐帧网格，短视频不会因不足一帧的舍入而漂移
- macOS 本地中文 TTS、DashScope Qwen3-TTS API，或使用预生成音频
- 短句级配音：每个镜头可拆成多个独立配音和字幕片段
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

## 安装

```bash
git clone https://github.com/sunpuxi/HuoChaiRen-Video-Maker.git
cd HuoChaiRen-Video-Maker
npm install
```

项目只使用一个 Node 依赖：`@resvg/resvg-js`。Python 端不需要第三方包。

## 使用 Qwen3-TTS API

管线支持通过阿里云百炼的非实时 HTTP API 调用 `qwen3-tts-flash`。这种模式不需要在本机下载模型或准备 GPU。

先在终端设置 API Key，不要把密钥写进项目文件：

```bash
export DASHSCOPE_API_KEY="你的 API Key"
```

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
stickman-pipeline/
├── render.py
├── storyboard.schema.json
├── pipeline/
│   ├── build.py
│   ├── config.py
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

- `text`：交给 TTS 的文字，也是未设置 `caption` 时的字幕文字。
- `caption`：可选的显示文本，适合简化口语或手动换行。
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
