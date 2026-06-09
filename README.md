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
8. 增加 PySide6 界面，提供监听模式选择和文本显示。

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

安装基础依赖：

```powershell
pip install pyaudiowpatch faster-whisper numpy==1.26.4 soundfile
```

如果运行 GPU 转写时报 `cublas64_12.dll` 或 cuDNN 相关 DLL 缺失，安装 CUDA 运行库依赖：

```powershell
pip install -r requirements-cuda.txt
```

当前命令行 MVP 不依赖 UI。如果后续要做桌面界面，再安装：

```powershell
pip install -r requirements-ui.txt
```

## 当前代码入口

当前已经实现命令行 MVP，入口文件是 `main.py`。

不想输入命令时，可以直接双击项目根目录的启动文件：

```text
start_gui.bat
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
