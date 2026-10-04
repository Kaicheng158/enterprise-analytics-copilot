"""Internal grounded generation. No endpoint, retrieval, or embedding side effects."""
from copy import deepcopy
import hashlib
import logging
import re
from typing import Protocol
from backend.config import LLMSettings
from backend.llm import ChatResult, ProviderError
from backend.rag.context import render, serialize
from backend.rag import grounded_prompt as prompt
from backend.rag.token_budget import DeepSeekTokenCounter, TokenBudget
from backend.rag.response import RagResponse

logger=logging.getLogger("uvicorn.error")


class GenerationProvider(Protocol):
    def generate_messages(self, messages: list[dict[str,str]], metadata: dict[str,str]) -> ChatResult: ...


def validate_sources(answer, blocks):
    sources={b["source_label"]:b for b in blocks}
    citations=[]
    fields=answer.model_dump()
    for field, values in fields.items():
        for index,text in enumerate([values] if isinstance(values,str) else values):
            # Reserved syntax: reject unknown/malformed labels rather than silently discard.
            labels=re.findall(r"\[S-[^\]]*(?:\]|$)",text)
            for token in labels:
                label=token[1:-1] if token.endswith("]") else ""
                if not re.fullmatch(r"S-[0-9a-f]{16}",label) or label not in sources:
                    raise ProviderError(502,"Invalid RAG source citation","rag_invalid_citation")
                citations.append({"field":field,"index":index,"source_label":label})
            if field=="facts" and not labels and "[user]" not in text:
                raise ProviderError(502,"Missing RAG fact attribution","rag_missing_attribution")
    used={c["source_label"] for c in citations}
    return citations,[deepcopy(b) for b in blocks if b["source_label"] in used]


class GroundedGenerator:
    def __init__(self, provider: GenerationProvider, settings: LLMSettings, budget=TokenBudget(), counter=None):
        self.provider=provider;self.settings=settings;self.budget=budget
        self.counter=counter or DeepSeekTokenCounter()
        # Prevent separately configured transport from invalidating the reserved output budget.
        if hasattr(provider,"settings") and provider.settings != settings:
            raise ValueError("Generation settings mismatch")

    def generate(self, query, context):
        if not isinstance(query,str) or not query.strip() or len(query)>8000:
            raise ValueError("Invalid RAG query")
        context=deepcopy(context)
        if context.get("version")!="evidence-json-char-v1" or render(context["blocks"])!=context["context"]:
            raise ValueError("Inconsistent server-built context")
        labels=[b["source_label"] for b in context["blocks"]]
        if len(set(labels))!=len(labels) or any(not re.fullmatch(r"S-[0-9a-f]{16}",x) for x in labels):
            raise ValueError("Invalid context labels")
        # Preserve Phase 3.6 as source of truth. Reject oversize input, never silently re-chunk it.
        wire=prompt.messages(query,context["context"])
        input_tokens=self.counter.count(wire,self.settings)
        baseline=self.counter.count(prompt.messages("",""),self.settings)
        with_query=self.counter.count(prompt.messages(query,""),self.settings)
        total=input_tokens+self.settings.max_output_tokens+self.budget.safety_tokens
        budget={"input_tokens":input_tokens,"fixed_prefix_and_envelope_tokens":baseline,
                "query_increment_tokens":with_query-baseline,"context_increment_tokens":input_tokens-with_query,
                "output_reserve":self.settings.max_output_tokens,"safety_reserve":self.budget.safety_tokens,
                "total_reserved":total,"max_total_tokens":self.budget.max_total_tokens,
                "method":"deepseek-recipe-v41-chat-encoding", "tokenizer":self.counter.manifest}
        if total>self.budget.max_total_tokens:
            raise ProviderError(413,"RAG model token budget exceeded","rag_token_budget_exceeded")
        result=self.provider.generate_messages(wire,{"prompt_version":prompt.VERSION,"prompt_sha256":prompt.SHA256})
        try:
            citations,sources=validate_sources(result.answer,context["blocks"])
        except ProviderError as error:
            error.telemetry=result.model_dump(exclude={"answer"})
            logger.info("rag_validation %s",serialize({"status":"error","code":error.code,**error.telemetry}))
            raise
        logger.info("rag_validation %s",serialize({"status":"success","request_id":result.request_id,
                    "prompt_version":prompt.VERSION,"prompt_sha256":prompt.SHA256,"citations":len(citations)}))
        return RagResponse.model_validate({"contract_version":"rag-response-v1","answer":result.answer.model_dump(),
                "citations":citations,"sources":sources,
                "generation":result.model_dump(exclude={"answer"}),"token_budget":budget,
                "context_sha256":hashlib.sha256(context["context"].encode()).hexdigest()}).model_dump()
