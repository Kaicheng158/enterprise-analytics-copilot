"""Offline model-token counting with pinned official tokenizer and chat encoding."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from deepseek_recipe import ChatCompletionRequest, ConversionOptions, DeepseekV41Encoding, Tokenizer


@dataclass(frozen=True)
class TokenBudget:
    # Conservative application cap, NOT a claim about maximum provider context length.
    max_total_tokens: int = 8192
    safety_tokens: int = 256

    def __post_init__(self):
        if type(self.max_total_tokens) is not int or self.max_total_tokens < 1:
            raise ValueError("Invalid token budget")
        if type(self.safety_tokens) is not int or self.safety_tokens < 0:
            raise ValueError("Invalid safety reserve")


class DeepSeekTokenCounter:
    def __init__(self):
        root=Path(__file__).with_name("tokenizer")
        self.manifest=json.loads((root/"manifest.json").read_text())
        data=(root/"tokenizer.json").read_bytes()
        if hashlib.sha256(data).hexdigest()!=self.manifest["sha256"]:
            raise ValueError("Tokenizer checksum mismatch")
        self.encoding=DeepseekV41Encoding().with_tokenizer(Tokenizer.from_file(str(root/"tokenizer.json")))

    def count(self, messages, settings):
        if settings.provider != "deepseek" or settings.model != "deepseek-flash":
            raise ValueError("No tokenizer mapping for model")
        request=ChatCompletionRequest({"model":settings.model,"messages":messages,
                                      "thinking":{"type":settings.thinking}})
        return len(self.encoding.encode(request.convert(ConversionOptions()).conversation))
