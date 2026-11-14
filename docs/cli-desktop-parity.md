# CLI 与客户端功能对齐

在项目目录、`voice-to-text` Python 环境中使用 `python -m voice_to_text`。
CLI 覆盖客户端的实时转写、视频处理和任务中心业务操作。
原有 CLI 命令和不带筛选条件的 `task list` JSON 数组格式保留。

## 实时转写

```powershell
python -m voice_to_text devices
python -m voice_to_text listen --source both --mic-device-index 3 --system-device-index 7 --language auto
python -m voice_to_text listen --device cpu --interactive
python -m voice_to_text listen --json --output-dir transcripts/live
```

- 先用 `devices` 查询索引，再分别指定麦克风和系统回环设备；省略索引使用默认设备。
- `--source mic/system/both` 对应客户端的来源复选框。
- 模型、设备、精度、语言、简繁、切片和阈值分别使用 `--model`、`--device`、`--compute-type`、`--language`、`--text-mode`、`--chunk-seconds`、`--min-rms`。
- CPU 默认 `int8`，CUDA 默认 `float16`；显式精度参数优先。
- `auto` 与客户端一样转换为自动识别语言。
- 两端复用 `live_runtime.RuntimeState`：模型就绪后开始采集，停止生产者后处理尾片，再关闭识别线程。
- `Ctrl+C` 停止并等待尾片处理。默认加载超时 180 秒、排空超时 300 秒，可用对应 `--*-timeout-seconds` 参数覆盖。
- `--interactive` 需要交互终端；输入 `status` 查看状态，`view all/mic/system` 切换视图，`clear` 清空本次保留的显示文本，`stop` 结束监听。
- `--view all/mic/system` 只筛选显示，不改变采集来源。
- `--verbose` 在 stderr 显示采集、模型和识别日志。
- `--json` 输出 JSON Lines，包含 `status` 和 `transcript` 事件；最后的 `stopped` 事件包含完整性、错误和输出路径。
- `--output-dir` 可选：落盘 `transcript_all.md`、`transcript_mic.md`、`transcript_system.md`。同目录同名文件会被本次结果覆盖，请给不同监听使用不同目录。
- 识别错误、丢片或排空超时不会被报告为完整成功；正常结束为 0，不完整为 3，启动失败为 1。

## 视频与已有稿件优化

```powershell
python -m voice_to_text transcribe-video "https://example.com/video" --cookie-browser edge --json
python -m voice_to_text download-video "https://example.com/video" --cookie-browser edge --json
python -m voice_to_text transcribe-video "https://example.com/video" --optimize-with-deepseek --json
python -m voice_to_text optimize-file "transcripts/video-example/transcript_raw.md" --json
python -m voice_to_text optimize-file "raw.md" --output "optimized.md" --deepseek-model deepseek-v4-flash
```

独立优化复用客户端的优化函数，不重新下载视频或调用 ASR。
默认优化文件为输入文件旁的 `transcript_deepseek.md`；不允许覆盖输入文件。
失败时保留上次成功的输出，成功后以临时文件原子替换。
API Key 沿用 `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY`，也可显式提供 `--deepseek-api-key`。
两端都使用 `--deepseek-model` 对应的模型；CLI 人类可读输出和 JSON 都包含 Token 用量。
JSON 中字段名为 `optimized_text`、`optimized_transcript_path`、`optimized_token_usage`。
视频串联优化同样返回这些字段，Token 用量含输入、输出和总计。

视频转写的 GUI 停止按钮与 CLI `Ctrl+C` 使用相同取消事件。
下载或 API 请求正在执行时等待该步骤返回；识别按片段检查取消。
取消退出码为 130，已生成的文件保留；再次 Ctrl+C 强制中断等待。
直接下载仍沿用原下载器的完成机制，GUI 停止按钮不会立即中断当前下载请求。

## 任务中心

GUI、CLI 和 HTTP API 共用 `%LOCALAPPDATA%/VoiceToText/tasks.sqlite3`。
`VTT_TASK_DB` 可指定数据库，CLI 也支持 `--db`。
任务管理本身无需 GUI 或 HTTP 服务运行。

| 客户端操作 | CLI |
|---|---|
| 新增任务 | `task add --url URL` |
| 批量粘贴链接或分享文本 | `task batch-add --urls-file links.txt`，或 `--urls-file -` 从 stdin 读取 |
| 编辑标题、目录、处理要求 | `task edit ID --title TITLE --output-dir DIR --instructions TEXT` |
| 保留录音、校对稿、总结 | `--keep-audio/--no-keep-audio`、`--optimize/--no-optimize`、`--summarize/--no-summarize` |
| 切片与识别设置 | `--chunk-seconds`、`--model`、`--device`、`--compute-type`、`--language`、`--text-mode`、`--min-rms` |
| 允许重复链接 | `--allow-duplicate` |
| 搜索、状态筛选、分页 | `task list --query TEXT --status queued --page 1 --page-size 20` |
| 查看详情和进度 | `task get ID` |
| 查看原始稿、时间戳稿、校对稿、总结 | `task result ID --kind raw/timestamped/optimized/summary/all` |
| 查看执行日志 | `task logs ID` |
| 打开结果目录 | `task open ID` |
| 取消或请求停止 | `task stop ID` |
| 重新处理 | `task retry ID` |

`task add/edit/update --data task.json` 继续支持原来的完整 JSON 协议。
创建和编辑时，直接参数覆盖 JSON 中相应字段，未提供的参数保留 JSON 或已有设置。
批量添加与客户端一样逐条保存有效链接，返回 `added` 和 `errors`；部分失败退出码为 1。
不带过滤条件的 `task list` 仍返回数组；带搜索、状态或分页选项时返回 `items/total/page/pages/page_size/counts`。
`--page-size` 与客户端一致支持 10、20、50。
`task result/logs --json` 可供脚本处理，结果文件读取与客户端共用路径检查。

入队不会自动处理视频。两端仍由 Codex 执行器领取、播放、采集、校对和总结。
`task claim/heartbeat/update` 和 `serve/session` 保留执行器接口。
任务设置启用总结代表执行要求，不会让建任务命令调用总结模型。
执行器须响应停止请求，详细流程见 [转换任务协议](conversion-tasks.md)。

## 验证范围

`tests/test_cli_parity.py` 覆盖 CLI 命令、临时任务数据库、产物、取消、Token 用量，以及 GUI 接入共享实时生命周期。
测试使用模拟设备、识别结果和 API 响应，不录制用户音频、不下载模型、不调用付费 API。
真实硬件、平台登录和模型服务的可用性仍取决于本机环境。
