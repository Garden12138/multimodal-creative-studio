# Studio API Contract — V1

This is the Studio's own API, not an OpenAI-compatible API.

## 1. General

- Prefix: `/api/v1`
- Submission: `multipart/form-data`
- Queries: JSON
- Asset response: binary with correct MIME type
- IDs are opaque strings; provider IDs are never used as Studio IDs.
- Never expose API keys or absolute filesystem paths.

## 2. Submit task

### `POST /api/v1/tasks`

| Field | Required | Values |
|---|---:|---|
| `media_type` | yes | `image`, `video` |
| `mode` | yes | `t2i`, `t2v`, `i2v`, `flf` |
| `prompt` | yes | non-empty text |
| `resolution` | image | `1024x1024`, `2048x2048` |
| `first_frame` | I2V/FLF | file |
| `last_frame` | FLF | file |

Validation:

| media_type | mode | first_frame | last_frame |
|---|---|---|---|
| image | t2i | forbidden | forbidden |
| video | t2v | forbidden | forbidden |
| video | i2v | required | forbidden |
| video | flf | required | required |

Invalid combinations return HTTP 400.

### Image example

```bash
curl -X POST http://127.0.0.1:8088/api/v1/tasks \
  -F 'media_type=image' -F 'mode=t2i' \
  -F 'prompt=a cinematic mountain lake at sunrise' \
  -F 'resolution=1024x1024'
```

### Video I2V example

```bash
curl -X POST http://127.0.0.1:8088/api/v1/tasks \
  -F 'media_type=video' -F 'mode=i2v' \
  -F 'prompt=describe the motion' -F 'first_frame=@first.png'
```

### Video FLF example

```bash
curl -X POST http://127.0.0.1:8088/api/v1/tasks \
  -F 'media_type=video' -F 'mode=flf' \
  -F 'prompt=describe the transition' \
  -F 'first_frame=@first.png' -F 'last_frame=@last.png'
```

Accepted response: HTTP 202.

```json
{
  "task_id": "opaque-studio-task-id",
  "media_type": "image",
  "mode": "t2i",
  "status": "queued",
  "created_at": "ISO-8601 timestamp"
}
```

## 3. List tasks

### `GET /api/v1/tasks?media_type=image&limit=30`

Newest first. `media_type` is optional; `limit` is bounded (maximum 100).

```json
{
  "items": [
    {
      "task_id": "opaque-studio-task-id",
      "media_type": "image",
      "provider": "comfyui_h3_image",
      "model": "minimax-h3-image",
      "mode": "t2i",
      "prompt": "prompt text",
      "params": {"resolution": "1024x1024"},
      "status": "succeeded",
      "created_at": "ISO-8601 timestamp",
      "started_at": "ISO-8601 timestamp",
      "finished_at": "ISO-8601 timestamp",
      "error": null,
      "assets": [
        {
          "asset_id": "opaque-asset-id",
          "media_type": "image",
          "mime_type": "image/png",
          "size_bytes": 123456,
          "url": "/api/v1/assets/opaque-asset-id/content"
        }
      ]
    }
  ]
}
```

## 4. Get task

`GET /api/v1/tasks/{task_id}` returns one task in the same shape. Unknown IDs return 404.

## 5. Asset content

`GET /api/v1/assets/{asset_id}/content`

- Return bytes with correct `Content-Type`.
- Support inline preview and attachment download.
- Resolve path and verify it remains under configured storage root.
- Never accept a filesystem path from the caller.

## 6. Health

`GET /api/health` suggested shape:

```json
{
  "web": "ok",
  "database": "ok",
  "providers": {
    "video": {"provider": "minimax_h3_native", "status": "ok"},
    "image": {
      "provider": "comfyui_h3_image",
      "status": "ok",
      "workflows": {"t2i_1mp": true, "t2i_4mp": true}
    }
  }
}
```

Statuses: `ok`, `unreachable`, `misconfigured`, `degraded`. Use documented readiness requests with bounded timeouts. A configured URL alone is not health evidence.

## 7. Legacy routes

Keep until an approved breaking change:

- `POST /api/tasks` — legacy multipart video submission (`mode`, `prompt`, `first_frame`, `last_frame`).
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/video`

Map legacy submissions into the same Task Service. Retain legacy response keys such as `id` and `state`; do not maintain a second queue.

## 8. Error response

```json
{
  "error": {
    "code": "WORKFLOW_NOT_READY",
    "message": "图片生成工作流尚未配置，请检查服务端工作流文件。",
    "task_id": null
  }
}
```

Do not return API keys, bearer headers, absolute paths, or internal tracebacks.
