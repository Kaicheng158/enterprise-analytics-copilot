"""Small server-owned registry. Add releases; never overwrite published entries."""
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from backend.prompt_analytics_v1 import PREFIX
from backend.prompt_analytics_v2 import PREFIX as V2_PREFIX


def prompt_sha256(messages):
    # Retain the historical eval hash convention: prefix plus empty final user.
    normalized = [*messages[:-1], {"role": "user", "content": ""}]
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class PromptRelease:
    version: str
    prefix: tuple[tuple[str, str], ...]
    sha256: str

    def messages(self, user_message: str) -> list[dict[str, str]]:
        messages = [{"role": role, "content": content} for role, content in self.prefix]
        messages.append({"role": "user", "content": user_message})
        if prompt_sha256(messages) != self.sha256:
            raise ValueError("Published prompt checksum mismatch")
        return messages


RELEASES = MappingProxyType({
    "analytics-v1": PromptRelease("analytics-v1", PREFIX, "bc480e10f14ae9d6158a70014eb4aef2b2ee0c00137f655e8cb05955a349bfb8"),
    "analytics-v2": PromptRelease("analytics-v2", V2_PREFIX, "b04bd2ea98f0e007b00c7a76e4b6a7e7ef8cd28478f350589e81f1f7b52d3fd6"),
})

CANDIDATES = MappingProxyType({})

# Deployment-owned selection. Roll back by selecting a retained release and redeploying.
ACTIVE_PROMPT_VERSION = "analytics-v2"


def active_release() -> PromptRelease:
    try:
        return RELEASES[ACTIVE_PROMPT_VERSION]
    except KeyError:
        raise ValueError("Unknown active prompt version") from None


def prompt_metadata(messages):
    digest = prompt_sha256(messages)
    # Label exact known releases/candidates, without making candidates active.
    version = next((release.version for release in (*RELEASES.values(), *CANDIDATES.values())
                    if digest == release.sha256), "unpublished")
    return {"prompt_version": version, "prompt_sha256": digest}
