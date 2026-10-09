from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Iterable

from .db import Task


class AIRankingError(RuntimeError):
    pass


@dataclass(frozen=True)
class AIRanking:
    task_ids: list[int]
    probabilities: dict[int, float]
    model: str
    load_ms: float | None = None
    inference_ms: float | None = None
    device: str | None = None


def build_kev_request(tasks: list[Task], *, today: date) -> dict:
    criteria = {
        str(task.id): {
            "text": task.text,
            "priority": task.priority,
            "due_at": task.due_at,
            "created_at": task.created_at,
            "created_by": task.created_by,
        }
        for task in tasks
    }
    return {
        "state": {
            "today": today.isoformat(),
            "goal": "Choose the task that should be worked on first today.",
        },
        "question": {
            "type": "choice",
            "instructions": (
                "Which task should be prioritized highest for today? "
                "Respect explicit priority and deadlines, and use the task meaning "
                "when those factual signals do not fully decide the order."
            ),
            "criteria": criteria,
        },
    }


def _validate_probabilities(
    raw: object,
    *,
    expected_ids: Iterable[int],
) -> dict[int, float]:
    expected = {int(task_id) for task_id in expected_ids}
    if not isinstance(raw, dict):
        raise AIRankingError("Kev returned probabilities in an unexpected shape")

    try:
        actual = {int(key) for key in raw}
    except (TypeError, ValueError) as exc:
        raise AIRankingError("Kev returned a non-task option ID") from exc

    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        parts = []
        if missing:
            parts.append(f"missing IDs {missing}")
        if unknown:
            parts.append(f"unknown IDs {unknown}")
        raise AIRankingError("Kev result does not match candidates: " + ", ".join(parts))

    probabilities: dict[int, float] = {}
    for key, value in raw.items():
        try:
            probability = float(value)
        except (TypeError, ValueError) as exc:
            raise AIRankingError(f"Kev returned a non-numeric probability for task {key}") from exc
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise AIRankingError(f"Kev returned an invalid probability for task {key}")
        probabilities[int(key)] = probability

    total = sum(probabilities.values())
    if probabilities and not 0.98 <= total <= 1.02:
        raise AIRankingError(f"Kev probabilities sum to {total:.4f}, expected approximately 1")

    return probabilities


def rank_from_probabilities(
    raw: object,
    *,
    expected_ids: Iterable[int],
    model: str,
    load_ms: float | None = None,
    inference_ms: float | None = None,
    device: str | None = None,
) -> AIRanking:
    probabilities = _validate_probabilities(raw, expected_ids=expected_ids)
    task_ids = sorted(probabilities, key=lambda task_id: (-probabilities[task_id], task_id))
    return AIRanking(
        task_ids=task_ids,
        probabilities=probabilities,
        model=model,
        load_ms=load_ms,
        inference_ms=inference_ms,
        device=device,
    )


def _safe_child_environment(repo_root: Path) -> dict[str, str]:
    allowed = {
        "COMSPEC",
        "CUDA_PATH",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in allowed or key.upper().startswith("CUDA_PATH_")
    }
    env["HF_HOME"] = str(repo_root / ".ai" / "cache" / "huggingface")
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _is_system_account() -> bool:
    if os.name != "nt":
        return False
    return os.environ.get("USERNAME", "").strip().upper() == "SYSTEM"


def run_kev_ranking(
    tasks: list[Task],
    *,
    today: date,
    python_path: Path,
    bridge_path: Path,
    model: str,
    device: str = "auto",
    timeout_seconds: int = 180,
    repo_root: Path | None = None,
) -> AIRanking:
    if not tasks:
        return AIRanking(task_ids=[], probabilities={}, model=model)

    if _is_system_account():
        raise AIRankingError(
            "local AI is disabled under the Windows SYSTEM account; "
            "use the limited daily service account"
        )

    repo_root = (repo_root or Path.cwd()).resolve()
    python_path = python_path if python_path.is_absolute() else repo_root / python_path
    bridge_path = bridge_path if bridge_path.is_absolute() else repo_root / bridge_path

    if not python_path.exists():
        raise AIRankingError(f"Kev Python runtime not found: {python_path}")
    if not bridge_path.exists():
        raise AIRankingError(f"Kev bridge not found: {bridge_path}")

    request = build_kev_request(tasks, today=today)
    payload = {
        "model": model,
        "device": device,
        "requests": [{"id": "daily", **request}],
    }

    try:
        completed = subprocess.run(
            [str(python_path), str(bridge_path)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=repo_root,
            env=_safe_child_environment(repo_root),
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AIRankingError(f"Kev timed out after {timeout_seconds} seconds") from exc
    except OSError as exc:
        raise AIRankingError(f"Kev could not start: {exc}") from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        suffix = detail[-1][:240] if detail else f"exit code {completed.returncode}"
        raise AIRankingError(f"Kev failed: {suffix}")

    try:
        body = json.loads(completed.stdout)
        results = body["results"]
        result = results[0]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise AIRankingError("Kev returned malformed bridge output") from exc

    if result.get("id") != "daily":
        raise AIRankingError("Kev bridge returned the wrong request ID")

    return rank_from_probabilities(
        result.get("probabilities"),
        expected_ids=[task.id for task in tasks],
        model=str(body.get("model") or model),
        load_ms=_optional_float(body.get("load_ms")),
        inference_ms=_optional_float(result.get("inference_ms")),
        device=str(body.get("device") or device),
    )


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None
