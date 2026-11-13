"""Independent, strict tabular contracts. Never an Agent task extension."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import unicodedata


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True, hide_input_in_errors=True)


class Column(Contract):
    name: str = Field(min_length=1, max_length=128)
    kind: Literal['text', 'decimal', 'money', 'date', 'boolean'] = 'text'
    required: bool = False
    unit: str | None = Field(default=None, max_length=32)

    @field_validator('name')
    @classmethod
    def canonical_name(cls, value):
        if value != unicodedata.normalize('NFC', value).strip() or any(ord(c) < 32 for c in value):
            raise ValueError('invalid column name')
        return value

    @model_validator(mode='after')
    def currency(self):
        if self.kind == 'money' and (not self.unit or len(self.unit) != 3 or not self.unit.isascii() or not self.unit.isalpha() or not self.unit.isupper()):
            raise ValueError('money requires explicit currency')
        return self


class FileSpec(Contract):
    project_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    dataset_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    filename: str = Field(min_length=1, max_length=128)
    columns: tuple[Column, ...] = ()

    @field_validator('filename')
    @classmethod
    def safe_basename(cls, value):
        if '/' in value or '\\' in value or ':' in value or value.startswith('.') or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('filename must be a basename')
        if not value.endswith(('.csv', '.xlsx')):
            raise ValueError('unsupported extension')
        return value

    @field_validator('columns')
    @classmethod
    def unique_columns(cls, values):
        if len(values) > 64 or len({c.name for c in values}) != len(values):
            raise ValueError('invalid column mapping')
        return values


class ParseSettings(Contract):
    enabled: bool = False
    max_bytes: int = Field(default=2*1024*1024, ge=1, le=2*1024*1024)
    max_expanded_bytes: int = Field(default=12*1024*1024, ge=1, le=12*1024*1024)
    max_member_bytes: int = Field(default=4*1024*1024, ge=1, le=4*1024*1024)
    max_members: int = Field(default=128, ge=1, le=128)
    max_ratio: int = Field(default=100, ge=1, le=100)
    max_sheets: int = Field(default=4, ge=1, le=4)
    max_rows: int = Field(default=5000, ge=1, le=5000)
    max_columns: int = Field(default=64, ge=1, le=64)
    max_cells: int = Field(default=100000, ge=1, le=100000)
    max_field_chars: int = Field(default=4096, ge=1, le=4096)
    max_issues: int = Field(default=2000, ge=1, le=2000)
    wall_seconds: float = Field(default=5.0, gt=0, le=5.0)


class Location(Contract):
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    table: int = Field(ge=1)
    sheet: str | None = Field(default=None, max_length=128, repr=False)
    row: int = Field(ge=1)
    column: int = Field(ge=1, le=64)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)


class Cell(Contract):
    value: str | None = Field(default=None, max_length=4096, repr=False)
    kind: Literal['text', 'decimal', 'money', 'date', 'boolean', 'null', 'blocked']
    location: Location

    @model_validator(mode='after')
    def value_presence(self):
        if (self.kind in {'null', 'blocked'}) != (self.value is None):
            raise ValueError('inconsistent cell')
        return self


class Issue(Contract):
    code: Literal['schema_confirmation_required', 'required_missing', 'numeric_ambiguous', 'date_ambiguous', 'boolean_ambiguous', 'formula_like_text', 'header_normalized', 'styled_cell_requires_review']
    severity: Literal['warning', 'blocking']
    location: Location


class Table(Contract):
    sheet: str | None = None
    columns: tuple[Column, ...]
    rows: tuple[tuple[Cell, ...], ...] = Field(repr=False)

    @model_validator(mode='after')
    def rectangular(self):
        if not self.rows or len(self.columns) > 64 or len({c.name for c in self.columns}) != len(self.columns):
            raise ValueError('invalid table contract')
        if not self.columns or any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError('nonrectangular table')
        return self


class Dataset(Contract):
    contract_version: Literal['dataset-v1'] = 'dataset-v1'
    policy_version: Literal['tabular-v1'] = 'tabular-v1'
    project_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    dataset_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    normalized_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    parse_contract_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    revision_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_format: Literal['csv', 'xlsx']
    status: Literal['validated', 'needs_review']
    tables: tuple[Table, ...] = Field(repr=False)
    issues: tuple[Issue, ...]

    @model_validator(mode='after')
    def coherent(self):
        if not self.tables:
            raise ValueError('empty dataset')
        if (self.status == 'needs_review') != any(i.severity == 'blocking' for i in self.issues):
            raise ValueError('inconsistent status')
        for ti, table in enumerate(self.tables, 1):
            for ri, row in enumerate(table.rows, 2):
                for ci, cell in enumerate(row, 1):
                    if cell.location.row != ri or (cell.kind not in {'null', 'blocked'} and cell.kind != table.columns[ci-1].kind):
                        raise ValueError('inconsistent cell contract')
                    if cell.location.source_sha256 != self.source_sha256 or cell.location.table != ti or cell.location.column != ci or cell.location.sheet != table.sheet:
                        raise ValueError('inconsistent provenance')
        return self
