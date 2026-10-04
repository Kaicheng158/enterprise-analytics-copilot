"""Versioned deterministic character chunks with paragraph/Markdown boundaries."""
from dataclasses import asdict, dataclass
from bisect import bisect_left, bisect_right
import hashlib
import json
import re
import uuid
from .models import Chunk, Document


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


@dataclass(frozen=True)
class ChunkConfig:
    algorithm: str = 'structure-char-v1'
    max_chars: int = 600
    overlap_chars: int = 80

    def __post_init__(self):
        if self.algorithm != 'structure-char-v1':
            raise ValueError('Unsupported chunk algorithm')
        if type(self.max_chars) is not int or not 16 <= self.max_chars <= 8000:
            raise ValueError('max_chars must be an integer between 16 and 8000')
        if type(self.overlap_chars) is not int or not 0 <= self.overlap_chars < self.max_chars // 2:
            raise ValueError('overlap_chars must be nonnegative and less than half max_chars')

    @property
    def sha256(self):
        return digest(canonical(asdict(self)))


def boundaries(text):
    """Blank paragraphs and ATX headings outside fenced code; keep exact offsets."""
    offsets = set()
    headings = []
    offset = 0
    fence = None
    heading_pending = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
        elif marker:
            offsets.add(offset)
            fence = (marker[1][0], len(marker[1]))
            heading_pending = False
        else:
            heading = re.match(r'^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$', line)
            if heading:
                offsets.add(offset)
                headings.append((offset, heading[2]))
                heading_pending = True
            elif not stripped:
                if not heading_pending:
                    offsets.add(offset + len(line))
            else:
                heading_pending = False
        offset += len(line)
    offsets.add(len(text))
    return sorted(offsets), headings


class StructureChunker:
    def __init__(self, config=ChunkConfig()):
        self.config = config

    def split(self, document: Document) -> tuple[Chunk, ...]:
        if document.metadata.get('config_sha256') != self.config.sha256:
            raise ValueError('Document revision belongs to another chunk config')
        text = document.text
        ends, headings = boundaries(text)
        newline_offsets = [i for i, char in enumerate(text) if char == "\n"]
        heading_offsets = [offset for offset, _ in headings]
        chunks = []
        start = previous_end = 0
        while start < len(text):
            hard_end = min(start + self.config.max_chars, len(text))
            boundary_index = bisect_right(ends, hard_end) - 1
            boundary = ends[boundary_index] if boundary_index >= 0 else 0
            end = boundary if boundary > max(previous_end, start + self.config.overlap_chars) else hard_end
            piece = text[start:end]
            ordinal = len(chunks)
            identity = canonical([document.document_id, document.revision, ordinal, start, end, digest(piece)])
            section_index = bisect_right(heading_offsets, start) - 1
            section = headings[section_index][1] if section_index >= 0 else None
            meta = {'char_start': start, 'char_end': end, 'offset_unit': 'unicode_codepoint',
                    'line_start': bisect_left(newline_offsets, start) + 1,
                    'line_end': bisect_left(newline_offsets, end - 1) + 1,
                    'section_at_start': section, 'config_sha256': self.config.sha256}
            chunks.append(Chunk(str(uuid.uuid5(uuid.NAMESPACE_URL, identity)), document.document_id,
                                document.revision, document.tenant_id, ordinal, piece,
                                f'chars:{start}:{end}', self.config.algorithm, digest(piece), meta))
            if end == len(text):
                break
            previous_end = end
            start = end - self.config.overlap_chars
        return tuple(chunks)
