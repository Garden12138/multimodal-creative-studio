from __future__ import annotations

from studio.errors import StudioError
from studio.providers.base import Provider


class ProviderRegistry:
    def __init__(self, providers: list[Provider]):
        self._providers = {provider.provider_id: provider for provider in providers}

    def get(self, provider_id: str) -> Provider:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise StudioError("INTERNAL_ERROR", "没有可用的生成服务。", 500)
        return provider

    def for_media(self, media_type: str) -> Provider:
        for provider in self._providers.values():
            if media_type in provider.supported_media_types:
                return provider
        raise StudioError("INTERNAL_ERROR", "没有可用的生成服务。", 500)

    def all(self) -> list[Provider]:
        return list(self._providers.values())
