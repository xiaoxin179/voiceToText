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

不想输入命令时，可以直接双击项目根目录的启动文件：

```text
start_gui.bat
```

首次使用前建议先双击模型初始化文件，提前下载模型：

```text
init_models.bat
```

也可以双击带菜单的启动文件：

```text
start_voice_to_text.bat
```

启动菜单包含：

- 打开 PyQt 桌面界面
- GPU 双通道监听
- CPU 备用双通道监听
- 环境和设备检查
- 音频设备列表
- 5 秒录音测试
- CUDA 运行库依赖安装
- Whisper 模型下载

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
