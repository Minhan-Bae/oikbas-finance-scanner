"""Channel implementations and registry."""

from oikbas_finance.channels.base import Channel, Signal

__all__ = ["Channel", "Signal", "REGISTRY", "get_channel"]

# Lazy registry — channels register themselves when imported.
REGISTRY: dict[str, type[Channel]] = {}


def register(name: str):
    def decorator(cls: type[Channel]) -> type[Channel]:
        REGISTRY[name] = cls
        return cls
    return decorator


def get_channel(name: str) -> type[Channel]:
    # Trigger module import side-effects so registry populates.
    _ensure_loaded()
    if name not in REGISTRY:
        raise KeyError(f"Unknown channel: {name}. Available: {sorted(REGISTRY)}")
    return REGISTRY[name]


def list_channels() -> list[str]:
    _ensure_loaded()
    return sorted(REGISTRY)


def _ensure_loaded() -> None:
    # Import each known module so its @register decorator runs.
    # New channels: add an import here.
    from oikbas_finance.channels import edgar  # noqa: F401
    from oikbas_finance.channels import yahoo  # noqa: F401
    from oikbas_finance.channels import cowork_import  # noqa: F401
