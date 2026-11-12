# H3 Native API fixtures

Probe time: 2026-11-11. These fixtures are redacted. They contain no API key.

## Live evidence

`GET http://192.168.10.104:8089/openapi.json` returned HTTP 200:

```json
{
  "openapi": "3.1.0",
  "info": {
    "title": "MiniMax H3 Native Video V2 (local)",
    "description": "Subset of MiniMax Video Generation V2 for the locally validated 5-second H3 workflow.",
    "version": "1.0.0"
  },
  "paths": {}
}
```

`GET /docs` returned Swagger UI with the same title and `url: '/openapi.json'`. The published schema does not list any operations.

Unauthenticated calls to these paths all returned HTTP 401 with the same body. Because authentication runs before routing, HTTP 401 does not prove that every route exists:

- `GET /v2/health`
- `POST /v2/video_generation`
- `GET /v2/query/video_generation/does-not-exist`
- `GET /v2/query/video_generation`
- `DELETE /v2/video_generation/does-not-exist`

```json
{
  "type": "error",
  "error": {
    "type": "authorized_error",
    "message": "Invalid or missing API key",
    "http_code": "401"
  }
}
```

SSH as `cengjiada` to `192.168.10.104` reached the host and was rejected with `Permission denied (publickey,password)`. `bjai-W580-G20` resolved through the local proxy to `198.18.0.15` and timed out during banner exchange. `~/ai-services/h3-minimax-native-v2/docs/AGENT_INTEGRATION.md`, `server.py`, and `minimax_v2_api.py` were not read from the server.

## Contract status

`VIDEO_NATIVE_CONTRACT_UNVERIFIED`

The request and response examples below come from the local integration note `h3-minimax-native-v2-agent-integration.md` (downloaded copy, not the file read from the 104 home directory). They match the live 401 envelope and the service title, and Studio's adapter is implemented from them. They are not a substitute for the server source or a non-empty OpenAPI document.

That same note says the native API submits work to the existing Studio at `:8088`. If that is still true, a Studio that submits to `:8089` will call back into itself. This repository does not change the 104 native API.

## Document-derived create request

`POST /v2/video_generation`

`Authorization: Bearer <API_KEY>`

`Content-Type: application/json`

Text to video:

```json
{
  "model": "MiniMax-H3",
  "content": [
    {"type": "text", "text": "a cat walking in the rain"}
  ],
  "resolution": "768P",
  "duration": 5,
  "ratio": "16:9"
}
```

Image to video adds one image item. First/last frame adds both image items. Images are PNG, JPEG, or WebP data URLs, not remote URLs.

```json
{
  "type": "image_url",
  "image_url": {"url": "data:image/png;base64,<REDACTED>"},
  "role": "first_frame"
}
```

`role` is `first_frame` or `last_frame`. Documented success body, HTTP 200:

```json
{"task_id": "0123456789abcdef0123456789abcdef"}
```

Documented request limits: `model` is `MiniMax-H3`, `resolution` is the compatibility value `768P` (actual output recorded as `608x352`), `duration` is `5`. Text-to-video `ratio` is `16:9`. Image-to-video and first/last-frame `ratio` may be `adaptive` or `16:9`.

## Document-derived query

`GET /v2/query/video_generation/{task_id}` with the same bearer header.

In progress:

```json
{
  "task": {
    "id": "0123456789abcdef0123456789abcdef",
    "model": "MiniMax-H3",
    "status": "running",
    "content": {},
    "resolution": "608x352",
    "duration": 5
  }
}
```

Success:

```json
{
  "task": {
    "id": "0123456789abcdef0123456789abcdef",
    "model": "MiniMax-H3",
    "status": "succeeded",
    "content": {"url": "http://192.168.10.104:8089/v2/files/<task_id>?expires=<expires>&signature=<signature>"}
  }
}
```

Failure:

```json
{
  "task": {
    "id": "0123456789abcdef0123456789abcdef",
    "status": "failed",
    "content": {},
    "error": {"code": "local_generation_failed", "message": "<redacted>"}
  }
}
```

Documented task statuses: `queued`, `running`, `succeeded`, `failed`, `cancelled`. Unknown statuses are not treated as success. The note says task listing and cancellation are not supported, so Studio does not call `GET /v2/query/video_generation` or `DELETE /v2/video_generation/{task_id}`.

## Document-derived download and errors

The result URL is a signed `GET`. The note says the download does not use the bearer token. Studio only downloads `http` or `https` URLs whose path starts with `/v2/files/` and whose host is the configured API host, `localhost`, loopback, or a private IP that is not link-local.

Documented error envelope:

```json
{
  "type": "error",
  "error": {
    "type": "unprocessable_entity_error",
    "message": "<redacted>",
    "http_code": "422"
  }
}
```

Documented HTTP statuses include 400, 401, 403, 404, 409, 413, 415, 422, 502, and 503. The live 401 type string is `authorized_error`.
