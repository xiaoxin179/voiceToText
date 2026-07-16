# Voice To Text 技术方案

本项目目标是实现一个 Windows 本地语音转文本工具：启动后可以监听电脑麦克风和系统音频，并将两路音频实时转换成文本。麦克风和系统音频需要作为两条独立输入管道处理，避免先混音后再识别导致说话来源不清晰。

## 核心目标

- 支持监听麦克风输入。
- 支持监听系统音频，也就是电脑扬声器正在播放的声音。
- 支持三种监听模式：
  - 只监听麦克风
  - 只监听系统音频
  - 同时监听麦克风和系统音频
- 两路音频独立采集、独立排队、独立标记来源。
- 实时或准实时显示转写文本。
- 优先利用本机 NVIDIA RTX 5060 显卡进行本地语音识别推理。

## 推荐技术栈

当前本机没有安装 C# 开发环境，因此第一阶段建议使用 Python 快速实现 MVP。

```text
Python
  ├─ pyaudiowpatch      // Windows 音频采集，支持 WASAPI loopback
  ├─ faster-whisper     // 本地 Whisper 语音识别
  ├─ CTranslate2 CUDA   // faster-whisper 底层 GPU 推理引擎
  ├─ numpy              // 音频数据处理
  ├─ soundfile          // 音频保存与调试
  └─ PyQt6              // 桌面 UI
```

## 音频采集方案

Windows 上需要分别采集两类音频：

- 麦克风：普通输入设备。
- 系统音频：通过 WASAPI loopback 捕获当前播放设备输出。

推荐使用 `pyaudiowpatch`，它相比普通 `pyaudio` 更适合 Windows 系统音频 loopback 场景。

整体管道如下：

```text
麦克风输入 -> MicCapture -> MicQueue -> ASR Worker -> 麦克风文本
系统音频   -> LoopbackCapture -> SystemQueue -> ASR Worker -> 系统音频文本
```

注意：不要把麦克风和系统音频先混成一路音频再识别。两路音频应该分别进入各自队列，识别完成后在 UI 层按来源和时间戳显示。

## GPU 语音识别方案

本机有 NVIDIA RTX 5060 显卡，应该优先使用 `faster-whisper` 的 CUDA 推理能力。

基础代码示例：

```python
from faster_whisper import WhisperModel

model = WhisperModel(
    "medium",
    device="cuda",
    compute_type="float16",
)
```

如果显存充足，可以尝试：

```python
model = WhisperModel(
    "large-v3",
    device="cuda",
    compute_type="float16",
)
```

如果显存压力较大或希望降低延迟，可以使用：

```python
model = WhisperModel(
    "small",
    device="cuda",
    compute_type="int8_float16",
)
```

推荐模型选择：

| 场景 | 推荐模型 | 说明 |
| --- | --- | --- |
| RTX 5060 8GB | `medium` | 速度和准确率比较平衡 |
| RTX 5060 Ti 16GB | `large-v3` | 准确率更好 |
| 低延迟优先 | `small` / `medium` | 更适合 1-2 秒切片识别 |
| 中文为主 | `medium` / `large-v3` | 中文识别效果更好 |
| 中英混合 | `large-v3` | 效果最好，但更吃显存 |

## 并发设计

采集线程可以分开，但 GPU 识别不建议为每一路音频各加载一个模型实例，因为这会浪费显存。

推荐设计：

```text
MicQueue
        \
         -> 单个 GPU ASR Worker -> TranscriptDispatcher -> UI
        /
SystemQueue
```

每个音频块携带来源信息：

```text
{
  source: "mic" | "system",
  started_at: timestamp,
  ended_at: timestamp,
  audio: pcm_data
}
```

识别完成后再输出：

```text
{
  source: "mic",
  text: "这是麦克风识别出的内容",
  started_at: timestamp,
  ended_at: timestamp
}
```

UI 可以支持两种显示方式：

- 双栏显示：左侧麦克风，右侧系统音频。
- 时间线显示：按时间顺序合并显示，并用来源标签区分。

## MVP 实现步骤

第一阶段先验证核心链路，不急着做复杂界面。

1. 列出 Windows 音频设备。
2. 找到默认麦克风设备。
3. 找到默认扬声器对应的 loopback 设备。
4. 分别录制 5 秒麦克风和系统音频，保存为 wav。
5. 确认两路音频都能正常录到。
6. 接入 `faster-whisper`，先对录音文件转写。
7. 改成按 1-3 秒音频块准实时转写。
8. 增加 PyQt6 界面，提供监听模式选择和文本显示。

## 初始依赖

建议优先使用 conda 创建环境：

```powershell
conda env create -f environment.yml
conda activate voice-to-text
```

如果不使用 `environment.yml`，也可以手动创建：

```powershell
conda create -n voice-to-text python=3.11 -y
conda activate voice-to-text
python -m pip install --upgrade pip
```

安装全部 pip 依赖：

```powershell
pip install -r requirements.txt
```

`requirements.txt` 已包含桌面界面、音频采集、faster-whisper、NumPy 兼容版本，以及 GPU 推理需要的 NVIDIA CUDA/cuDNN Python 运行库。

识别后的中文默认会用 OpenCC 统一转换为简体中文。界面里的“文本”选项可以切换为 `简体中文`、`原始输出` 或 `繁体中文`。

## 当前代码入口

当前已经实现命令行 MVP，入口文件是 `main.py`。

不想输入命令时，可以直接双击 `scripts` 目录中的启动文件：

```text
scripts\start_gui.bat
```

首次使用前建议先双击模型初始化文件，提前下载模型：

```text
scripts\init_models.bat
```

也可以双击带菜单的启动文件：

```text
scripts\start_voice_to_text.bat
```

启动菜单包含：

- 打开 PyQt 桌面界面
- GPU 双通道监听
- CPU 备用双通道监听
- 环境和设备检查
- 音频设备列表
- 5 秒录音测试
- Python 依赖安装和修复
- Whisper 模型下载

## 无 NVIDIA 显卡 / CPU 启动方式

没有 NVIDIA 显卡的电脑也可以启动本项目，只是不能使用 `cuda` 推理，需要切换到 CPU 模式。

首次准备环境：

```powershell
conda env create -f environment.yml
conda activate voice-to-text
python init_models.py --models tiny
```

如果只下载 `tiny` 模型，需要让启动检查只要求 `tiny`，否则默认会同时检查 `tiny, medium`：

```powershell
$env:VTT_REQUIRED_MODELS="tiny"
python app.py
```

GUI 打开后建议这样设置：

```text
模型: tiny
设备: cpu
精度: int8
语言: zh
文本: 简体中文
切片: 2 s 或 3 s
```

也可以使用菜单启动器：

```text
scripts\start_voice_to_text.bat
```

然后选择 `Start listening - CPU fallback`。这个模式会用：

```text
model=tiny
device=cpu
compute-type=int8
source=both
```

CPU 模式的实时性取决于处理器性能。如果出现延迟，优先使用 `tiny` 模型，或者把切片长度调到 `3 s`。

## 模型初始化

`faster-whisper` 第一次使用某个模型时会从 HuggingFace Hub 下载模型文件。控制台出现下面这句不是错误：

```text
Warning: You are sending unauthenticated requests to the HF Hub.
```

意思是当前没有配置 `HF_TOKEN`，所以使用匿名请求下载。匿名下载可以用，但可能更慢或被限速。

模型默认下载到当前 Windows 用户的 HuggingFace 缓存目录：

```text
C:\Users\<你的用户名>\.cache\huggingface\hub\
```

本机对应路径是：

```text
C:\Users\dgy30\.cache\huggingface\hub\
```

程序启动时会强制检查必需模型缓存。默认必需模型是：

```text
tiny, medium
```

如果任意一个模型没有完整缓存，GUI 会直接中断启动，并提示应该运行的下载命令。这样可以避免启动后才偷偷下载模型，导致界面看起来没有反应。

可以用环境变量调整启动时要求检查的模型：

```powershell
$env:VTT_REQUIRED_MODELS="tiny,medium"
```

`medium` 模型对应目录是：

```text
C:\Users\dgy30\.cache\huggingface\hub\models--Systran--faster-whisper-medium
```

可以提前下载默认模型：

```powershell
python init_models.py --models tiny medium
```

也可以下载指定模型：

```powershell
python init_models.py --models small medium large-v3
```

如果想下载后顺便验证模型能加载：

```powershell
python init_models.py --models medium --load-check
```

如果想手动删除某个模型缓存，可以关闭 GUI 后删除对应目录，例如：

```powershell
Remove-Item "$env:USERPROFILE\.cache\huggingface\hub\models--Systran--faster-whisper-medium" -Recurse -Force
Remove-Item "$env:USERPROFILE\.cache\huggingface\hub\.locks\models--Systran--faster-whisper-medium" -Recurse -Force
```

## 界面调试方法

如果点击“开始监听”后没有文字输出，先看界面底部的“调试日志”。

常见日志含义：

- `model loaded`：模型已经加载完成。
- `capture started`：对应音频设备已经开始采集。
- `chunk queued, rms=..., peak=...`：已经采到一段音频。`rms` 和 `peak` 越接近 0，说明声音越小。
- `skipped, rms=... < min_rms=...`：音量低于静音阈值，被跳过了。可以把界面里的“静音阈值”调低到 `0.00000` 或 `0.00050` 再试。
- `result: [empty]`：音频送进 ASR 了，但模型没有识别出文字。可能是这段没有人声、音量太小、切片太短，或语言设置不对。
- `ASR error` / `model load error`：识别引擎报错。如果使用 `cuda`，先切换到 `cpu` + `tiny` 测试核心链路。

建议排查顺序：

1. 先只勾选“麦克风”，推理设备选 `cpu`，模型选 `tiny`，点击“开始监听”。
2. 对着麦克风持续说话 5-10 秒。
3. 看调试日志里是否出现 `chunk queued`。
4. 如果没有 `chunk queued`，说明采集设备没有正常工作，尝试切换“麦克风设备”。
5. 如果有 `chunk queued` 但 `rms` 很低，调低“静音阈值”或提高系统麦克风音量。
6. 如果 CPU 模式正常，再切换到 `cuda` 测试 GPU。

检查 Python、依赖、CUDA 和默认音频设备：

```powershell
python main.py doctor
```

列出音频设备：

```powershell
python main.py devices
```

录制 5 秒麦克风和系统音频，用于确认两路采集是否正常：

```powershell
python main.py record-test --source both --seconds 5
```

转写已经录好的 wav 文件：

```powershell
python main.py transcribe-file recordings\mic.wav --model tiny --device cuda --compute-type float16
```

开始监听并调用 GPU 识别：

```powershell
python main.py listen --source both --model medium --device cuda --compute-type float16
```

如果 RTX 5060 显存压力较大，可以先用更小模型：

```powershell
python main.py listen --source both --model small --device cuda --compute-type int8_float16
```

如果 `faster-whisper` 没有正确使用 GPU，需要继续检查：

- NVIDIA 驱动是否已安装并支持当前 RTX 5060。
- CUDA / cuDNN / CTranslate2 版本是否匹配。
- `WhisperModel(..., device="cuda")` 是否成功初始化。

## 后续可选优化

- 增加 VAD，只在检测到人声时送入 ASR，减少无效识别。
- 增加回声抑制，减少扬声器声音被麦克风重复收录的问题。
- 增加热键控制开始/暂停监听。
- 增加转写历史保存。
- 增加导出 Markdown / TXT / SRT。
- 如果后续需要真正低延迟流式识别，可以评估 `sherpa-onnx`。
- 如果后续需要正式 Windows 桌面软件安装包，可以再迁移到 C# + WPF / WinUI 3，或继续用 Python + PyInstaller 打包。

## 平台视频转写

桌面 GUI 现在包含两个侧边栏栏目：

- `语音转写`：保留原来的麦克风和系统音频实时监听逻辑。
- `平台视频转写`：粘贴 Bilibili、YouTube、Douyin 等 `yt-dlp` 支持的视频链接，下载音频后用本地 `faster-whisper` 转成文字稿。

示例链接：

```text
https://www.bilibili.com/video/BV1rv7A6oEeP/
```

该示例是 120 P 的 Bilibili 长合集。当前 Bilibili fallback 会处理第 1 P，后续可以继续扩展为指定分 P 转写。

新增依赖：

```powershell
pip install yt-dlp requests
```

系统还需要能从命令行访问 `ffmpeg`。本地转写不需要额外 API 费用，仍然使用 HuggingFace 缓存中的 `Systran/faster-whisper-*` 模型。

如果视频需要登录态，在 `Cookie` 下拉框中选择已经登录的平台浏览器，例如 `Chrome` 或 `Edge`。
抖音的用户页/弹窗链接会自动规范化为 `/video/<id>` 视频页；抖音通常仍需要新鲜浏览器 Cookie。若提示无法复制浏览器 Cookie 数据库，请关闭对应浏览器窗口后重试，或切换到另一个已登录浏览器。
如果关闭 Chrome/Edge 后又提示 `Failed to decrypt with DPAPI`，通常是新版 Chromium 浏览器在 Windows 上启用了 v20/App-Bound Cookie 加密；关闭浏览器只能解除文件锁，不能让 `yt-dlp` 解密这些 Cookie。此时建议改用 Firefox Cookie、浏览器扩展导出的 Netscape `cookies.txt`，或复制真实媒体流链接。
如果 Chrome 已经能播放抖音视频，也可以在 Chrome 开发者工具 Network 中复制 `douyinvod` / `media-audio` 音频流链接，直接粘贴到平台视频输入框转写。

如果需要调用 Agent 优化识别结果，勾选 `使用 DeepSeek 优化识别文字`。DeepSeek 只处理已经识别出的文本，不负责音频转写。API Key 可以直接填在界面中，也可以设置环境变量：

```powershell
$env:DEEPSEEK_API_KEY="你的 DeepSeek API Key"
```

如果界面里没有填写 Key，程序会优先读取 `DASHSCOPE_API_KEY`，然后读取 `DEEPSEEK_API_KEY`。该变量需要对应目标 DeepSeek 兼容接口可用的 Key。

输出文件默认保存到 `transcripts/video-YYYYMMDD-HHMMSS/`，包括：

- `audio.mp3`
- `transcript_raw.md`
- `transcript_timestamped.md`
- `transcript_deepseek.md`，仅在启用 DeepSeek 优化时生成。

## 服务化调用

项目现在提供 GUI 之外的本地服务化入口，方便 AI 通过 CLI 或 HTTP 调用。

### CLI：视频链接转写

```powershell
python main.py transcribe-video "https://www.bilibili.com/video/BV..." --model tiny --device cpu --compute-type int8 --json
```

返回 JSON，包含 `raw_text`、`timestamped_text`、`audio_path`、`raw_transcript_path`、`timestamped_transcript_path` 和输出目录。

### CLI：监听扬声器音频

适合抖音、登录态网页、外站视频等无法直接下载音频的场景。先让浏览器播放视频，再调用：

```powershell
python main.py transcribe-speaker --seconds 120 --source system --model tiny --device cpu --compute-type int8 --json
```

输出默认保存到 `transcripts/speaker-YYYYMMDD-HHMMSS/`，包括：

- `speaker.wav`
- `transcript_raw.md`
- `transcript_timestamped.md`

注意：系统扬声器监听会录到当前电脑正在播放的所有声音。转写期间请避免其他视频、提示音或 TTS 混入。

### HTTP 服务

启动本地服务：

```powershell
python main.py serve --host 127.0.0.1 --port 8765
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

同步转写视频链接：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/transcribe/video -Method Post -ContentType "application/json" -Body '{
  "url": "https://www.bilibili.com/video/BV...",
  "model": "tiny",
  "device": "cpu",
  "compute_type": "int8",
  "language": "zh"
}'
```

创建扬声器监听任务：

```powershell
$session = Invoke-RestMethod http://127.0.0.1:8765/sessions/speaker -Method Post -ContentType "application/json" -Body '{
  "seconds": 120,
  "source": "system",
  "model": "tiny",
  "device": "cpu",
  "compute_type": "int8",
  "language": "zh"
}'

Invoke-RestMethod "http://127.0.0.1:8765/sessions/$($session.session_id)?result=1"
```

提前停止任务：

```powershell
Invoke-RestMethod "http://127.0.0.1:8765/sessions/$($session.session_id)/stop" -Method Post
```

AI 自动化建议：

1. 调 `/sessions/speaker` 开始监听。
2. 控制浏览器打开并播放目标视频。
3. 轮询 `/sessions/{session_id}?result=1`。
4. 根据 `status=completed` 后读取 `result.raw_text` 或 `result.timestamped_text`。

## 媒体下载

项目额外提供一个只负责“链接 -> 视频文件”的下载入口，不会启动 Whisper，也不会生成文字稿。底层使用 `yt-dlp` 支持的平台解析能力，适用于 Bilibili、YouTube、TikTok/Douyin、Instagram、X 等其已支持的平台。

```powershell
python main.py download-video "https://www.bilibili.com/video/BV..." --output-dir downloads --json
```

如平台需要登录态，优先使用 Firefox，或从已登录浏览器导出 Netscape 格式的 `cookies.txt`。新版 Chrome/Edge 的 App-Bound Cookie 加密可能导致第三方程序无法复用 Cookie：

```powershell
python main.py download-video "https://www.douyin.com/video/..." --cookies-file C:\path\to\cookies.txt --referer "https://www.douyin.com/"
```

对于浏览器已捕获的真实媒体地址，也可带上请求头下载：

```powershell
python main.py download-video "https://.../video.mp4" --referer "https://www.douyin.com/" --header "Authorization: Bearer ..."
```

### OmniGet backend (optional)

OmniGet can be used as an optional local download backend for platforms such as
Douyin. The integration uses OmniGet's documented authenticated localhost
bridge; no OmniGet source code is copied into this project.

1. Install and launch OmniGet.
2. In OmniGet, open **Settings -> Network -> Browser extension** and copy the
   local bridge URL and token.
3. Either pass both values to the CLI, or configure them as environment
   variables for the desktop app and service process:

```powershell
$env:OMNIGET_BRIDGE_URL="http://127.0.0.1:47720"
$env:OMNIGET_BRIDGE_TOKEN="your-OmniGet-token"
python main.py download-video "https://www.douyin.com/video/..." --backend omniget --json
```

`--backend auto` is the default. It uses OmniGet when both bridge values are
configured, and otherwise uses `yt-dlp`. `--backend omniget` fails clearly when
the bridge is unavailable. OmniGet only acknowledges that a URL was queued;
the resulting file path and progress remain managed by OmniGet itself.

The HTTP `POST /download/video` and `POST /sessions/download` payloads accept
the same optional fields: `backend`, `omniget_endpoint`, and `omniget_token`.

本地 HTTP 服务也提供同步和异步入口：

```powershell
# 同步下载
Invoke-RestMethod http://127.0.0.1:8765/download/video -Method Post -ContentType "application/json" -Body '{
  "url": "https://www.bilibili.com/video/BV...",
  "output_dir": "downloads"
}'

# 异步下载，随后查询 /sessions/{session_id}?result=1
Invoke-RestMethod http://127.0.0.1:8765/sessions/download -Method Post -ContentType "application/json" -Body '{
  "url": "https://www.bilibili.com/video/BV..."
}'
```

### OmniGet 参考与许可证说明

下载服务的产品方向和浏览器到本地服务的桥接思路，参考了 [OmniGet](https://github.com/tonhowtf/omniget) 的公开设计。OmniGet 使用 `yt-dlp` 覆盖 1,800+ 站点，并通过浏览器扩展将已捕获的媒体 URL、Referer、Cookie/请求头交给本地应用；这也是本项目后续适配受登录态限制平台时应持续关注的上游实现。

本项目没有复制、链接或捆绑 OmniGet 的 GPL-3.0 源代码；这里的下载模块是独立实现，仅调用 `yt-dlp`。因此本段是来源说明和技术参考，不代表本项目是 OmniGet 的派生代码。请遵守平台条款、版权及当地法律，仅下载你有权访问和保存的内容。
