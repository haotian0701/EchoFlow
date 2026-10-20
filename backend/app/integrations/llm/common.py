import math
import time


def response_metadata(response, started):
    """Allowlist only accounting fields. Never persist an upstream error body."""
    metadata = {"latency_ms": round((time.monotonic() - started) * 1000)}
    try:
        data = response.json()
        if not isinstance(data, dict):
            return metadata
        for target, source in (
            ("provider_request_id", "id"),
            ("actual_model", "model"),
        ):
            if isinstance(data.get(source), str):
                metadata[target] = data[source][:200]
        if "provider_request_id" not in metadata and response.headers.get(
            "x-request-id"
        ):
            metadata["provider_request_id"] = response.headers["x-request-id"][:200]
        usage = data.get("usage") or {}
        if isinstance(usage, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost"):
                value = usage.get(key)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    and value >= 0
                ):
                    metadata[key] = value
    except (ValueError, TypeError):
        pass
    return metadata
