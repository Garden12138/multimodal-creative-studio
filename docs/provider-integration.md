# Provider 集成

## 视频 `comfyui_h3_video`

页面和旧的 `/api/tasks` 都把视频任务直接提交到 `VIDEO_COMFYUI_BASE_URL`（默认 `http://127.0.0.1:8189`）。8089 的现有实现会把任务再提交回 Studio；如果 Studio 也去调用 8089，唯一的视频 worker 会卡在查询上，模型日志不会前进，同一次提交还会多出一条排队任务。

Studio 读取 `workflows/t2v_api.json`、`i2v_api.json`、`flf_api.json`，替换 `__H3_STUDIO_PROMPT__`，图生和首末帧再替换 `STUDIO_FIRST.png` / `STUDIO_LAST.png`，然后调用：

- `POST /upload/image`（仅图生和首末帧）
- `POST /prompt`
- `GET /history/{prompt_id}`
- `GET /view?filename=...&subfolder=...&type=output`

输出取 history 里后缀为 mp4、webm 或 mkv 的文件。`SaveVideo.filename_prefix` 会改成 `video/studio_{task_id}`。步数、模型和分辨率节点保持工作流里的原值。

8089 继续单独部署。它的集成说明写着下游是 Studio 8088，所以 Studio 不把页面任务再提交给 8089。

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
