# Provider 集成

## 视频 `minimax_h3_native`

Studio 向 `VIDEO_NATIVE_BASE_URL`（默认 `http://127.0.0.1:8089`）发送：

- `POST /v2/video_generation`
- `GET /v2/query/video_generation/{task_id}`
- 成功后下载 `task.content.url`
- 健康检查 `GET /v2/health`

Bearer token 只放在提交、查询和健康检查上。下载不附带 token。请求体按本地集成说明组装：`model=MiniMax-H3`、`resolution=768P`、`duration=5`；文生视频 `ratio=16:9`，图生和首末帧 `ratio=adaptive`。图片使用 `data:image/png;base64,...`，角色为 `first_frame` / `last_frame`。

适配器把 `queued` / `running` 视为进行中，把 `failed` / `cancelled` 视为失败，把缺少结果 URL 的 `succeeded` 视为 `RESULT_NOT_FOUND`。其他状态继续轮询直到超时，不会记成成功，也不会再提交一次。

这份字段约定来自本机的集成说明，不是 104 上的非空 OpenAPI，也不是服务器源码。现场 OpenAPI 的 `paths` 为空。因此视频契约标记为 `VIDEO_NATIVE_CONTRACT_UNVERIFIED`。样例见 `docs/contracts/h3-native-api-fixtures.md`。

该说明还写着 8089 的下游是 Studio 8088。Studio 的启动脚本不管理这套服务，只在视频请求时调用已经启动的 8089。在这个下游关系被源码推翻之前，不能把“视频 Native API 已在 104 上接通”写成完成。

## 图片 `comfyui_h3_image`

Studio 读取两份 API workflow，替换唯一标记 `__H3_IMAGE_STUDIO_PROMPT__`，再调用：

- `POST /prompt`
- `GET /history/{prompt_id}`
- `GET /view?filename=...&subfolder=...&type=output`

节点不靠写死的 id 查找。正向提示词是标记所在的 `PrimitiveStringMultiline`。输出文件来自 history 里 `outputs.*.images`，并且文件名不能包含路径跳转。

两份 workflow 来自 2026-11-11 对 `192.168.10.104:8190` 的只读历史，不是手写节点图：

| 档位 | history prompt | 已下载结果 |
|---|---|---|
| 1024×1024 | `8b05e78f-1ff7-4904-b4d2-afff212a1cb9` | `minimax_h3_image_t2i_00001_.png`，1024×1024 PNG |
| 2048×2048 | `c555f2e1-55da-4596-b116-46d9f87f6a30` | `minimax_h3_image_t2i_4mp_00004_.png`，2048×2048 PNG |

两边的 API 图只有 `ResolutionSelector.megapixels` 不同，分别是 `1.0` 和 `4.0`。已核对并保留：

- DiT `minimax_h3_image_pruned_w4a8.safetensors`
- CLIP `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`
- 文本 CLIP `qwen3.5_9b_w4a8_gptq.safetensors`
- VAE `minimax_h3_image_vae_fp16.safetensors`
- `ModelAttentionBackend=pytorch attention`
- `ComfySwitchNode.switch=false`（提示词扩展关闭）
- `KSampler` 6 steps、CFG 1、euler、simple、denoise 1

`/object_info/<class>` 对图中的 16 种 class type 都返回了定义。服务器 `user/workflows` 里同名的 `*api.json` 经读取前 200 字节确认是带 `nodes/links` 的 UI JSON，没有当成 API workflow 使用。

提交时只改三处：正向提示词、`SaveImage.filename_prefix`、`KSampler.seed`。后两项让每次任务的文件名和画面可以区分；步数、CFG、模型、注意力和分辨率档位保持不变。

带有标记的 workflow 没有再次提交到 8190。那一次真实执行需要单独授权。历史里的成功图证明的是替换标记之前的图曾经跑通。

## 错误码

对浏览器返回固定中文说明，不返回密钥、绝对路径或上游响应原文。使用的代码包括 `INPUT_INVALID`、`WORKFLOW_NOT_READY`、`WORKFLOW_INVALID`、`PROVIDER_UNAVAILABLE`、`PROVIDER_AUTH_FAILED`、`PROVIDER_REJECTED`、`PROVIDER_TIMEOUT`、`RESULT_NOT_FOUND`、`ASSET_STORAGE_FAILED`、`INTERNAL_ERROR`。
