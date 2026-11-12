from __future__ import annotations

import copy
import json
from pathlib import Path

PROMPT_MARKER = "__H3_IMAGE_STUDIO_PROMPT__"

RESOLUTIONS = {
    "1024x1024": 1.0,
    "2048x2048": 4.0,
}

BASELINE = {
    "unet": "minimax_h3_image_pruned_w4a8.safetensors",
    "clip_vl": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "clip_text": "qwen3.5_9b_w4a8_gptq.safetensors",
    "vae": "minimax_h3_image_vae_fp16.safetensors",
    "attention": "pytorch attention",
    "steps": 6,
    "cfg": 1.0,
    "sampler_name": "euler",
    "scheduler": "simple",
    "denoise": 1.0,
    "aspect_ratio": "1:1 (Square)",
    "multiple": 32,
}


def _count_marker(value) -> int:
    if isinstance(value, str):
        return value.count(PROMPT_MARKER)
    if isinstance(value, dict):
        return sum(_count_marker(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_marker(item) for item in value)
    return 0


def _one(nodes_by_type: dict, class_type: str) -> dict:
    nodes = nodes_by_type.get(class_type) or []
    if len(nodes) != 1:
        raise ValueError(f"工作流必须恰好包含一个 {class_type} 节点")
    return nodes[0]


def validate_workflow(data, megapixels: float) -> None:
    if not isinstance(data, dict) or not data:
        raise ValueError("工作流文件不是 ComfyUI API 格式")
    nodes_by_type: dict[str, list] = {}
    for node in data.values():
        if not isinstance(node, dict) or "class_type" not in node or not isinstance(node.get("inputs"), dict):
            raise ValueError("工作流文件不是 ComfyUI API 格式")
        nodes_by_type.setdefault(node["class_type"], []).append(node)

    save = _one(nodes_by_type, "SaveImage")
    sampler = _one(nodes_by_type, "KSampler")
    resolution = _one(nodes_by_type, "ResolutionSelector")
    attention = _one(nodes_by_type, "ModelAttentionBackend")
    switch = _one(nodes_by_type, "ComfySwitchNode")
    unet = _one(nodes_by_type, "UNETLoader")
    vae = _one(nodes_by_type, "VAELoader")
    clips = nodes_by_type.get("CLIPLoader") or []
    if len(clips) != 2:
        raise ValueError("工作流 CLIP 模型与已验证基线不一致")
    clip_names = {node["inputs"].get("clip_name") for node in clips}
    if clip_names != {BASELINE["clip_vl"], BASELINE["clip_text"]}:
        raise ValueError("工作流 CLIP 模型与已验证基线不一致")
    if unet["inputs"].get("unet_name") != BASELINE["unet"]:
        raise ValueError("工作流 DiT 模型与已验证基线不一致")
    if vae["inputs"].get("vae_name") != BASELINE["vae"]:
        raise ValueError("工作流 VAE 与已验证基线不一致")
    if attention["inputs"].get("attention") != BASELINE["attention"]:
        raise ValueError("工作流注意力后端必须保持 pytorch attention")
    if switch["inputs"].get("switch") is not False:
        raise ValueError("工作流提示词扩展开关必须保持关闭")
    if sampler["inputs"].get("steps") != BASELINE["steps"] or float(sampler["inputs"].get("cfg")) != BASELINE["cfg"]:
        raise ValueError("工作流采样步数或 CFG 与已验证基线不一致")
    if (
        sampler["inputs"].get("sampler_name") != BASELINE["sampler_name"]
        or sampler["inputs"].get("scheduler") != BASELINE["scheduler"]
        or float(sampler["inputs"].get("denoise")) != BASELINE["denoise"]
    ):
        raise ValueError("工作流采样器与已验证基线不一致")
    if resolution["inputs"].get("aspect_ratio") != BASELINE["aspect_ratio"]:
        raise ValueError("工作流宽高比与已验证基线不一致")
    if float(resolution["inputs"].get("megapixels")) != float(megapixels):
        raise ValueError("工作流分辨率与所选档位不一致")
    if resolution["inputs"].get("multiple") != BASELINE["multiple"]:
        raise ValueError("工作流对齐倍数与已验证基线不一致")
    if "filename_prefix" not in save["inputs"]:
        raise ValueError("SaveImage 缺少 filename_prefix")
    if _count_marker(data) != 1:
        raise ValueError("工作流正向提示词标记缺失或重复")
    hosts = [
        node
        for node in data.values()
        if node.get("class_type") == "PrimitiveStringMultiline"
        and isinstance(node["inputs"].get("value"), str)
        and PROMPT_MARKER in node["inputs"]["value"]
    ]
    if len(hosts) != 1:
        raise ValueError("提示词标记不在正向提示词节点")


def load_workflow(path: Path, megapixels: float) -> dict:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_workflow(data, megapixels)
    return data


def assert_execution_baseline(data: dict, megapixels: float) -> None:
    """Check verified knobs on a prompt that may already contain the user text."""
    cloned = copy.deepcopy(data)
    restored = False
    for node in cloned.values():
        title = (node.get("_meta") or {}).get("title")
        if title == "Text (user_prompt)" and node.get("class_type") == "PrimitiveStringMultiline":
            node["inputs"]["value"] = PROMPT_MARKER
            restored = True
            break
    if not restored:
        raise ValueError("工作流缺少已验证的正向提示词节点")
    validate_workflow(cloned, megapixels)


def prepare_workflow(path: Path, megapixels: float, prompt: str, task_id: str, seed: int) -> dict:
    workflow = copy.deepcopy(load_workflow(path, megapixels))
    replaced = 0
    for node in workflow.values():
        inputs = node["inputs"]
        if (
            node.get("class_type") == "PrimitiveStringMultiline"
            and isinstance(inputs.get("value"), str)
            and PROMPT_MARKER in inputs["value"]
        ):
            inputs["value"] = inputs["value"].replace(PROMPT_MARKER, prompt, 1)
            replaced += 1
        elif node.get("class_type") == "SaveImage":
            inputs["filename_prefix"] = f"studio_{task_id}"
        elif node.get("class_type") == "KSampler":
            inputs["seed"] = int(seed)
    if replaced != 1:
        raise ValueError("替换提示词失败")
    assert_execution_baseline(workflow, megapixels)
    return workflow
