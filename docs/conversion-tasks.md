# 转换任务：Codex 执行协议

桌面“03 转换任务”提前保存单条或批量链接。数据库默认位于
`%LOCALAPPDATA%\VoiceToText\tasks.sqlite3`；GUI、CLI 和 HTTP API 共用数据库。
用 `VTT_TASK_DB` 可指定独立数据库。任务不会自行唤醒 Codex 或创建聊天。
添加链接后，在 Codex 中说“处理转换队列”，由当前会话执行。

## 命令行

在项目目录、voice-to-text Python 环境下：

```powershell
python -m voice_to_text task add --url "https://www.douyin.com/video/..."
python -m voice_to_text task list
python -m voice_to_text task claim --owner codex
python -m voice_to_text task get task_xxx
python -m voice_to_text task heartbeat task_xxx --token <lease_token>
python -m voice_to_text task update task_xxx --token <lease_token> --data update.json
python -m voice_to_text task stop task_xxx
python -m voice_to_text task retry task_xxx
```

add/edit/update 支持 `--data` UTF-8 JSON；claim 可带 task_id 指定任务。
令牌也可放在 `VTT_TASK_TOKEN`，不要写入日志或结果稿。
HTTP 提供 GET `/tasks`、GET `/tasks/{id}`、POST `/tasks`、POST `/tasks/claim`、
POST `/tasks/{id}/{edit|update|heartbeat|stop|retry}`。更新 JSON 使用 `lease_token` 字段。
服务仅供可信本机使用，不应暴露到公网。

## 执行步骤

1. 领取一个任务；没有任务则停止。读取设置、独立输出目录和租约令牌。
2. 至少每 30 秒心跳，租约有效期 120 秒。浏览器等待、模型加载期间也要心跳。
   每次检查 stop_requested。停止请求后暂停自己控制的视频，停止自己的采集会话，
   等待落盘，再回写 cancelled 或 needs_review。GUI 请求停止不代表后台录音已经停止。
3. 通过浏览器读取标题、时长和播放状态，不要求用户手动抄写。
   阶段依次回写 inspecting、capturing、transcribing、proofreading、summarizing。
4. 使用现有 session start/wait-ready/status/stop/wait 执行采集。
   task.payload.config.model 须映射到 SpeakerServiceRequest.model_size；
   output_dir 使用任务专属目录。将会话 ID 回写 progress.session_id。
   等 ready 后才从视频开头播放，不抢占其他会话。
5. 视频结束后停止采集，等待最后一片识别及排空队列，检查丢片、错误和完整性。
   不确定完整性时写 needs_review，不能写 completed。
6. 在任务目录生成原始稿、时间戳稿，以及按选项生成校对稿和总结。
   不保留录音时仅删除本任务录音，不清理其他会话。
7. 回写绝对路径、状态及完整性：

```json
{
  "status": "completed",
  "progress": {"message": "识别、校对和总结已完成"},
  "result": {
    "raw_transcript_path": "C:/.../task_xxx/transcript_raw.md",
    "timestamped_transcript_path": "C:/.../task_xxx/transcript_timestamped.md",
    "optimized_transcript_path": "C:/.../task_xxx/transcript_optimized.md",
    "summary_path": "C:/.../task_xxx/summary.md",
    "integrity": {"complete": true, "dropped_chunks": 0}
  }
}
```

完成状态检查必需文件真实存在且位于任务目录、明确完整性标记，以及 WAV 已按设置清理。
这不自动证明内容真实，执行器仍须核实视频覆盖和校对质量。
失败写 failed + error；需登录等用户介入时写 waiting + message 并继续心跳。
心跳超时变 interrupted；重试保留历史日志和旧文件，使用新目录，旧令牌不能回写。
