# V1 验收报告

- 日期：2026-11-11
- 分支：`main`（实现尚未提交）
- 基线提交：`57839f8` `init`
- 结论：**`BLOCKED_BY_PROVIDER_CONTRACT`**

图片两个分辨率和视频三个模式都还没有在 104 上真实跑通，因此不能标成 `READY_FOR_USER_TRIAL`。

## 自动化 / mock

在仓库根目录、Python 3.10.9 下实际执行：

```bash
.venv/bin/python check_workflows.py
.venv/bin/python -m unittest discover -s tests -t .
```

结果：

- `check_workflows.py`：`t2v`、`i2v`、`flf`、`t2i_1mp`、`t2i_4mp` 全部 OK。
- `unittest`：15 项，`Ran 15 tests in 2.160s`，`OK`。
- 覆盖迁移与旧行保留、运行中任务的选择性恢复、路径穿越、视频契约样例、未知状态、超时不重提、图片 history / `/view`、workflow 标记、旧 `/api/tasks` 与 `/api/v1` 共用一张表、健康检查，以及对本机 mock 8089/8190 的图片两档和视频 T2V / I2V / FLF 往返。
- mock 往返里，重启后的 Studio 仍能读到已完成图片。

这些通过不等于 104 真生成通过。

另外在本机用 mock 的 8089/8190 打开了 `http://127.0.0.1:8099`，实际点过页面：切到图片并选择 2048×2048 后，结果卡片出现预览和“下载图片”；切到视频后历史仍在，图生视频出现单张上传，文生视频提交后出现播放器和“下载视频”；刷新后两条记录都还在；首末帧同时显示首帧和末帧上传。播放器里的 0:00 来自 mock 字节，不是 104 上的真实 MP4。

## 104 只读证据

| 检查 | 结果 |
|---|---|
| `192.168.10.104:8089/openapi.json` | 200，标题为本地 H3 Native V2，`paths` 为空 |
| 未带密钥访问 `/v2/health` 与视频路由 | 401，`authorized_error` / `Invalid or missing API key` |
| SSH 读取 104 上的 Native API 源码 | 失败，公钥认证被拒绝 |
| `192.168.10.104:8190/system_stats` | 200，ComfyUI 0.45.0 |
| `8190/history` 与 `/view` | 读到已成功的 1024 和 2048 输出 |
| `8190/object_info/<class>` | workflow 中的 class 均存在 |
| 用标记后的 workflow 重新提交 8190 | **NOT RUN** |
| 通过 Studio 真实生成 T2V / I2V / FLF | **NOT RUN** |

## 真实验收矩阵

| ID | 项目 | 结果 |
|---|---|---|
| RT-01 | Video Native API 可认证 | NOT RUN（只有未认证的 401） |
| RT-02 | Image `/system_stats` | 只读成功；不是一次生成 |
| RT-03 | Video T2V | NOT RUN |
| RT-04 | Video I2V | NOT RUN |
| RT-05 | Video FLF | NOT RUN |
| RT-06 | Image 1024×1024 | NOT RUN |
| RT-07 | Image 2048×2048 | NOT RUN |
| RT-08 | 刷新后历史仍在 | 仅 mock，104 NOT RUN |
| RT-09 | 重启后素材仍在 | 仅 mock，104 NOT RUN |
| RT-10 | 图片/视频队列隔离 | 仅 mock，104 NOT RUN |

## 尚未执行的 104 命令

先确认 8089 不会再把任务打回这个 Studio。确认并获得生成授权后，在 104 的 Studio 已监听 8088 时执行。下面的命令会真实占用 GPU 0 和 GPU 2，不要并行堆很多条。

```bash
curl -fsS http://127.0.0.1:8088/api/health

curl -fsS -X POST http://127.0.0.1:8088/api/v1/tasks \
  -F 'media_type=image' -F 'mode=t2i' \
  -F 'prompt=a quiet mountain lake at sunrise, square composition' \
  -F 'resolution=1024x1024'

curl -fsS -X POST http://127.0.0.1:8088/api/v1/tasks \
  -F 'media_type=image' -F 'mode=t2i' \
  -F 'prompt=a quiet mountain lake at sunrise, square composition' \
  -F 'resolution=2048x2048'

curl -fsS -X POST http://127.0.0.1:8088/api/v1/tasks \
  -F 'media_type=video' -F 'mode=t2v' \
  -F 'prompt=a red paper boat moving across a still pond'

curl -fsS -X POST http://127.0.0.1:8088/api/v1/tasks \
  -F 'media_type=video' -F 'mode=i2v' \
  -F 'prompt=the subject blinks and turns slightly' \
  -F 'first_frame=@first.png'

curl -fsS -X POST http://127.0.0.1:8088/api/v1/tasks \
  -F 'media_type=video' -F 'mode=flf' \
  -F 'prompt=the scene moves from the first frame to the last frame' \
  -F 'first_frame=@first.png' -F 'last_frame=@last.png'
```

记录返回的 `task_id` 后轮询 `GET /api/v1/tasks/<task_id>`，直到 `succeeded` 或 `failed`。对 `assets[].url` 再发一次 GET，确认图片能打开、视频能下载。然后重启 Studio 进程（不要重启 8089 / 8189 / 8190），再 GET 同一任务和素材。

参考图需要是 PNG/JPEG/WebP，边长 256–5760，宽高比 0.4–2.5，单张不超过 12MB。

## 阻塞

1. 视频字段契约没有从 104 源码或非空 OpenAPI 核实，标记 `VIDEO_NATIVE_CONTRACT_UNVERIFIED`。
2. 现有集成说明中的调用方向是 8089 → Studio 8088，与本版本的 Studio → 8089 相反。Studio 启动脚本不改 Native API 的部署；如果 8089 仍把任务打回 8088，替换 Studio 后视频任务可能互相等待。
3. 用户未授权真实生成，因此 RT-03 至 RT-07 不能写成 PASS。
