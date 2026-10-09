from __future__ import annotations

import contextlib
import json
import sys
import time


def _choose_device(torch, requested: str) -> str:
    if requested not in {"auto", "cpu", "cuda"}:
        raise ValueError("device must be auto, cpu, or cuda")
    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false")
    return requested


def main() -> None:
    payload = json.load(sys.stdin)
    model_name = str(payload.get("model", "")).strip()
    if not model_name:
        raise ValueError("model is required")

    requests = payload.get("requests")
    if not isinstance(requests, list) or not requests:
        raise ValueError("requests must be a non-empty list")

    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from kev.api import SystemOneRequest, to_answers, to_record
        from kev.checkpoint import Checkpoint, LoadOptions
        from kev.model import admit

        device = _choose_device(torch, str(payload.get("device", "auto")))

        load_started = time.perf_counter()
        checkpoint = Checkpoint(model_name)
        tokenizer, model = checkpoint.load(
            device,
            LoadOptions(
                dtype=torch.float32,
                attn="sdpa" if device == "cuda" else "eager",
            ),
        )
        load_ms = (time.perf_counter() - load_started) * 1000

        results = []
        for item in requests:
            request_id = str(item.get("id", "")).strip()
            if not request_id:
                raise ValueError("each request requires an id")

            request = SystemOneRequest(
                state=item.get("state"),
                model="kev-latest",
                questions={"priority": item.get("question")},
            )
            record, meta = to_record(request)
            encoded = admit(model, tokenizer, record)

            if device == "cuda":
                torch.cuda.synchronize()
            started = time.perf_counter()
            with torch.no_grad():
                probabilities = model.probs(encoded)
            if device == "cuda":
                torch.cuda.synchronize()
            inference_ms = (time.perf_counter() - started) * 1000

            answers = to_answers([values.tolist() for values in probabilities], meta)
            choice = answers["priority"]
            results.append(
                {
                    "id": request_id,
                    "probabilities": choice["probabilities"],
                    "inference_ms": round(inference_ms, 3),
                }
            )

    print(
        json.dumps(
            {
                "model": model_name,
                "device": device,
                "load_ms": round(load_ms, 3),
                "results": results,
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
