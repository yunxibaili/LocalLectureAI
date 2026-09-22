import json
from typing import Any, Dict, List


class ParseError(Exception):
    pass


def _clip01000(v: float) -> float:
    try:
        v = float(v)
    except Exception:
        return 0.0
    return max(0.0, min(1000.0, v))


def _norm_bbox(bbox: Any) -> List[float]:
    if not (isinstance(bbox, list) and len(bbox) == 4):
        raise ParseError(f"bbox must be list[4], got: {bbox}")
    x1, y1, x2, y2 = [_clip01000(v) for v in bbox]
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return [x1, y1, x2, y2]


def parse(raw: str) -> Dict[str, Any]:
    try:
        obj = json.loads(raw)
    except Exception as e:
        raise ParseError(f"Invalid JSON: {e}\nRaw: {raw[:500]}")

    if not isinstance(obj, dict) or "task" not in obj:
        raise ParseError(f"Missing 'task' field. Raw: {raw[:500]}")

    task = obj["task"]

    if task == "localization":
        return {
            "task": "localization",
            "object": str(obj.get("object", "")),
            "bbox": _norm_bbox(obj.get("bbox")),
            "confidence": float(obj.get("confidence", 0.0) or 0.0),
        }

    if task == "multi_localization":
        objects_in = obj.get("objects") or []
        objects: List[Dict[str, Any]] = []
        for it in objects_in:
            if not isinstance(it, dict):
                continue
            try:
                bbox = _norm_bbox(it.get("bbox"))
            except ParseError:
                continue
            objects.append({
                "name": str(it.get("name") or it.get("object") or ""),
                "bbox": bbox,
                "confidence": float(it.get("confidence", 0.0) or 0.0),
            })
        return {"task": "multi_localization", "objects": objects}

    if task == "counting":
        instances_in = obj.get("instances") or []
        instances: List[Dict[str, Any]] = []
        for i, inst in enumerate(instances_in, start=1):
            pt = inst.get("point") if isinstance(inst, dict) else None
            if not (isinstance(pt, list) and len(pt) == 2):
                continue
            instances.append({"id": int(inst.get("id", i)), "point": [_clip01000(pt[0]), _clip01000(pt[1])]})
        return {
            "task": "counting",
            "object": str(obj.get("object", "")),
            "count": int(obj.get("count", len(instances)) or len(instances)),
            "instances": instances,
        }

    raise ParseError(f"Unknown task: {task}")
