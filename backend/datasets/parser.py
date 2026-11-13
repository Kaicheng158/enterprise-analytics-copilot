"""Bounded deterministic parser; supported OOXML is deliberately conservative."""
import csv
from datetime import date
from decimal import Decimal
import hashlib
import io
import json
import posixpath
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from .models import Cell, Column, Dataset, FileSpec, Issue, Location, ParseSettings, Table

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
DOCREL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NUM = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z')
REF = re.compile(r'([A-Z]{1,3})([1-9][0-9]{0,6})\Z')


class ParseFailure(Exception):
    """Fixed codes only; do not attach source exceptions or values."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def text(value, limits):
    if len(value) > limits.max_field_chars:
        raise ParseFailure('field_limit')
    value = unicodedata.normalize('NFC', value.replace('\r\n', '\n').replace('\r', '\n'))
    if any((ord(c) < 32 and c not in '\n\t') or ord(c) == 127 for c in value):
        raise ParseFailure('invalid_text')
    return value


def csv_tables(data, limits):
    try:
        decoded = data.decode('utf-8-sig', errors='strict')
    except UnicodeError:
        raise ParseFailure('unsupported_encoding') from None
    csv.field_size_limit(limits.max_field_chars)
    rows = []
    previous = 0
    try:
        reader = csv.reader(io.StringIO(decoded, newline=''), strict=True)
        cell_count = 0
        for row in reader:
            cell_count += len(row)
            if cell_count > limits.max_cells:
                raise ParseFailure('cell_limit')
            if len(rows) > limits.max_rows:
                raise ParseFailure('row_limit')
            if len(row) > limits.max_columns:
                raise ParseFailure('column_limit')
            rows.append((len(rows)+1, [(text(v, limits), False) for v in row], previous+1, reader.line_num))
            previous = reader.line_num
    except csv.Error:
        raise ParseFailure('invalid_csv') from None
    return [(None, rows)]


def xml(data):
    # Reject declarations even in UTF-16/32. No DTD/entity expansion or XInclude.
    probe = data.replace(b'\x00', b'').upper()
    if b'<!DOCTYPE' in probe or b'<!ENTITY' in probe:
        raise ParseFailure('unsafe_xml')
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise ParseFailure('invalid_xml') from None
    if any(e.tag.startswith('{http://www.w3.org/2001/XInclude}') for e in root.iter()):
        raise ParseFailure('unsafe_xml')
    return root


def coordinate(ref, limits):
    match = REF.fullmatch(ref or '')
    if not match:
        raise ParseFailure('invalid_cell_reference')
    col = 0
    for char in match[1]:
        col = col*26 + ord(char)-64
    row = int(match[2])
    if col > limits.max_columns or row > limits.max_rows+1:
        raise ParseFailure('dimension_limit')
    return row, col


def xlsx_tables(data, limits):
    if not data.startswith(b'PK\x03\x04'):
        raise ParseFailure('format_mismatch')
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        raise ParseFailure('invalid_archive') from None
    with archive:
        infos = archive.infolist()
        if len(infos) > limits.max_members:
            raise ParseFailure('archive_member_limit')
        names = set()
        total = 0
        for info in infos:
            name = info.filename
            if name in names or name.startswith('/') or '\\' in name or '..' in name.split('/') or info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ParseFailure('unsafe_archive_path')
            names.add(name)
            total += info.file_size
            if info.flag_bits & 1 or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ParseFailure('unsupported_archive')
            if info.file_size > limits.max_member_bytes or total > limits.max_expanded_bytes or info.file_size > max(info.compress_size, 1)*limits.max_ratio:
                raise ParseFailure('archive_expansion_limit')
            if not (name in {'[Content_Types].xml', '_rels/.rels', 'xl/workbook.xml', 'xl/_rels/workbook.xml.rels', 'xl/styles.xml', 'xl/sharedStrings.xml', 'docProps/core.xml', 'docProps/app.xml'} or re.fullmatch(r'xl/worksheets/sheet[1-9][0-9]*\.xml', name) or re.fullmatch(r'xl/theme/theme[1-9][0-9]*\.xml', name)):
                raise ParseFailure('unsupported_workbook_part')
        roots = {}
        for name in sorted(names):
            with archive.open(name) as stream:
                payload = stream.read(limits.max_member_bytes+1)
            if len(payload) > limits.max_member_bytes:
                raise ParseFailure('archive_expansion_limit')
            roots[name] = xml(payload)
        required = {'[Content_Types].xml', '_rels/.rels', 'xl/workbook.xml', 'xl/_rels/workbook.xml.rels'}
        if not required <= names:
            raise ParseFailure('invalid_workbook')
        types = roots['[Content_Types].xml']
        if types.tag != '{http://schemas.openxmlformats.org/package/2006/content-types}Types':
            raise ParseFailure('invalid_workbook')
        declared = {}
        for element in types:
            content_type = element.get('ContentType', '')
            if any(word in content_type.lower() for word in ('macro', 'vba', 'ole', 'activex')):
                raise ParseFailure('active_content')
            if element.tag.endswith('}Override'):
                part = element.get('PartName', '').lstrip('/')
                if part in declared or part not in names:
                    raise ParseFailure('invalid_content_types')
                declared[part] = content_type
        if declared.get('xl/workbook.xml') != 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml':
            raise ParseFailure('invalid_content_types')
        relations = {}
        for name in ('_rels/.rels', 'xl/_rels/workbook.xml.rels'):
            root = roots[name]
            if root.tag != '{'+REL+'}Relationships':
                raise ParseFailure('invalid_relationship')
            entries = {}
            for rel in root:
                target = rel.get('Target', '')
                if rel.tag != '{'+REL+'}Relationship' or rel.get('TargetMode', 'Internal') != 'Internal' or ':' in target or '\\' in target or '?' in target or '#' in target:
                    raise ParseFailure('external_relationship')
                resolved = posixpath.normpath(posixpath.join('xl' if name.startswith('xl/') else '', target))
                if target.startswith('/'):
                    resolved = target[1:]
                ident = rel.get('Id')
                if not ident or ident in entries or resolved not in names:
                    raise ParseFailure('invalid_relationship')
                entries[ident] = (resolved, rel.get('Type', ''))
            relations[name] = entries
        root_office = [target for target, typ in relations['_rels/.rels'].values() if typ == DOCREL+'/officeDocument']
        if root_office != ['xl/workbook.xml']:
            raise ParseFailure('invalid_relationship')
        workbook = roots['xl/workbook.xml']
        if workbook.tag != '{'+NS+'}workbook':
            raise ParseFailure('invalid_workbook')
        if any(e.tag.rsplit('}', 1)[-1] in {'externalReferences', 'definedNames', 'extLst', 'AlternateContent', 'rPh', 'phoneticPr'} for root in roots.values() for e in root.iter()):
            raise ParseFailure('unsupported_workbook_feature')
        shared = []
        if 'xl/sharedStrings.xml' in roots:
            sr = roots['xl/sharedStrings.xml']
            if sr.tag != '{'+NS+'}sst':
                raise ParseFailure('invalid_shared_strings')
            for si in sr:
                if si.tag != '{'+NS+'}si' or len(shared) >= limits.max_cells:
                    raise ParseFailure('shared_string_limit')
                shared.append(text(''.join(e.text or '' for e in si.iter('{'+NS+'}t')), limits))
        sheets = workbook.findall('./{'+NS+'}sheets/{'+NS+'}sheet')
        if not sheets or len(sheets) > limits.max_sheets:
            raise ParseFailure('sheet_limit')
        tables = []
        used = set()
        sheet_names = set()
        total_grid_cells = 0
        for sheet in sheets:
            sheet_name = text(sheet.get('name', ''), limits)
            if not sheet_name or len(sheet_name) > 128 or sheet_name in sheet_names or sheet.get('state', 'visible') != 'visible':
                raise ParseFailure('unsupported_sheet')
            sheet_names.add(sheet_name)
            relation = relations['xl/_rels/workbook.xml.rels'].get(sheet.get('{'+DOCREL+'}id'))
            if not relation or relation[1] != DOCREL+'/worksheet' or relation[0] in used:
                raise ParseFailure('invalid_relationship')
            path = relation[0]
            used.add(path)
            if declared.get(path) != 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml':
                raise ParseFailure('invalid_content_types')
            root = roots[path]
            if root.tag != '{'+NS+'}worksheet':
                raise ParseFailure('invalid_sheet')
            for e in root.iter():
                local = e.tag.rsplit('}', 1)[-1]
                if local in {'f', 'mergeCells', 'hyperlinks', 'drawing', 'legacyDrawing', 'oleObjects', 'controls', 'tableParts', 'autoFilter'}:
                    raise ParseFailure('formula_or_unsupported_sheet')
                if (local == 'row' and e.get('s', '0') != '0') or (local == 'col' and e.get('style', '0') != '0'):
                    raise ParseFailure('unsupported_inherited_style')
                if e.get('hidden', '0') not in {'0', 'false'}:
                    raise ParseFailure('hidden_data')
            dimension = root.find('{'+NS+'}dimension')
            if dimension is not None:
                for end in dimension.get('ref', '').split(':'):
                    coordinate(end, limits)
            if len(root.findall('{'+NS+'}sheetData')) != 1:
                raise ParseFailure('invalid_sheet')
            allowed_sheet = {'sheetPr', 'dimension', 'sheetViews', 'sheetFormatPr', 'cols', 'sheetData', 'printOptions', 'pageMargins', 'pageSetup', 'headerFooter'}
            if any(e.tag not in {'{'+NS+'}'+n for n in allowed_sheet} for e in root):
                raise ParseFailure('unsupported_workbook_feature')
            sheet_data = root.find('{'+NS+'}sheetData')
            if any(e.tag != '{'+NS+'}row' for e in sheet_data):
                raise ParseFailure('invalid_sheet')
            grid = {}
            prior_row = 0
            count = 0
            for row in root.findall('./{'+NS+'}sheetData/{'+NS+'}row'):
                try:
                    ri = int(row.get('r', '0'))
                except ValueError:
                    raise ParseFailure('invalid_cell_reference') from None
                if not prior_row < ri <= limits.max_rows+1:
                    raise ParseFailure('row_limit')
                prior_row = ri
                grid[ri] = {}
                last_col = 0
                for cell in row:
                    if cell.tag != '{'+NS+'}c':
                        raise ParseFailure('invalid_sheet')
                    cr, ci = coordinate(cell.get('r'), limits)
                    if cr != ri or ci <= last_col:
                        raise ParseFailure('invalid_cell_reference')
                    last_col = ci
                    count += 1
                    if count > limits.max_cells:
                        raise ParseFailure('cell_limit')
                    if any(e.tag not in {'{'+NS+'}v', '{'+NS+'}is'} for e in cell):
                        raise ParseFailure('invalid_cell')
                    typ = cell.get('t', 'n')
                    if (typ == 'inlineStr' and (len(cell.findall('{'+NS+'}is')) != 1 or cell.find('{'+NS+'}v') is not None)) or (typ != 'inlineStr' and cell.find('{'+NS+'}is') is not None):
                        raise ParseFailure('invalid_cell')
                    vals = cell.findall('{'+NS+'}v')
                    if len(vals) > 1:
                        raise ParseFailure('invalid_cell')
                    value = (vals[0].text or '') if vals else ''
                    if typ == 's':
                        if not value.isascii() or not value.isdigit() or len(value) > 8 or int(value) >= len(shared):
                            raise ParseFailure('invalid_shared_strings')
                        value = shared[int(value)]
                    elif typ == 'inlineStr':
                        value = ''.join(e.text or '' for e in cell.findall('./{'+NS+'}is//{'+NS+'}t'))
                    elif typ == 'b':
                        if value not in {'0', '1'}:
                            raise ParseFailure('invalid_cell')
                        value = 'true' if value == '1' else 'false'
                    elif typ not in {'n', 'd'}:
                        raise ParseFailure('unsupported_cell_type')
                    grid[ri][ci] = (text(value, limits), cell.get('s', '0') != '0')
            if not grid or 1 not in grid or not grid[1]:
                raise ParseFailure('empty_file')
            width = max(grid[1])
            if set(grid[1]) != set(range(1, width+1)) or any(max(cols, default=0) > width for cols in grid.values()):
                raise ParseFailure('invalid_row_width')
            total_grid_cells += max(grid) * width
            if total_grid_cells > limits.max_cells:
                raise ParseFailure('cell_limit')
            rows = []
            for ri in range(1, max(grid)+1):
                rows.append((ri, [grid.get(ri, {}).get(ci, ('', False)) for ci in range(1, width+1)], None, None))
            tables.append((sheet_name, rows))
        worksheet_parts = {n for n in names if n.startswith('xl/worksheets/')}
        if used != worksheet_parts:
            raise ParseFailure('orphan_sheet')
        return tables


def parse_core(data: bytes, spec: FileSpec, limits: ParseSettings) -> Dataset:
    if not data:
        raise ParseFailure('empty_file')
    if len(data) > limits.max_bytes:
        raise ParseFailure('file_limit')
    source_hash = hashlib.sha256(data).hexdigest()
    fmt = spec.filename.rsplit('.', 1)[1]
    if fmt == 'csv' and (data.startswith(b'PK') or data.startswith(b'\xd0\xcf')):
        raise ParseFailure('format_mismatch')
    raw_tables = csv_tables(data, limits) if fmt == 'csv' else xlsx_tables(data, limits)
    issues = []
    tables = []
    cells = 0
    def issue(code, severity, loc):
        if len(issues) >= limits.max_issues:
            raise ParseFailure('issue_limit')
        issues.append(Issue(code=code, severity=severity, location=loc))
    for ti, (sheet, rows) in enumerate(raw_tables, 1):
        if len(rows) < 2 or not rows[0][1]:
            raise ParseFailure('empty_file')
        if len(rows)-1 > limits.max_rows:
            raise ParseFailure('row_limit')
        if any(len(row[1]) != len(rows[0][1]) for row in rows):
            raise ParseFailure('invalid_row_width')
        headers = [v.strip() for v, _ in rows[0][1]]
        if any(not h or len(h) > 128 for h in headers) or len(set(headers)) != len(headers):
            raise ParseFailure('invalid_header')
        columns = spec.columns or tuple(Column(name=h) for h in headers)
        if [c.name for c in columns] != headers:
            raise ParseFailure('schema_mismatch')
        output = []
        for ri, values, line_start, line_end in rows:
            cells += len(values)
            if cells > limits.max_cells:
                raise ParseFailure('cell_limit')
            row = []
            for ci, ((value, styled), column) in enumerate(zip(values, columns), 1):
                loc = Location(source_sha256=source_hash, table=ti, sheet=sheet, row=ri, column=ci, line_start=line_start, line_end=line_end)
                if ri == 1:
                    if value.lstrip().startswith(('=', '+', '-', '@')):
                        issue('formula_like_text', 'blocking', loc)
                    if value != column.name:
                        issue('header_normalized', 'warning', loc)
                    if not spec.columns:
                        issue('schema_confirmation_required', 'blocking', loc)
                    continue
                kind = column.kind
                if styled:
                    issue('styled_cell_requires_review', 'blocking', loc)
                if value == '':
                    kind, value = 'null', None
                    if column.required:
                        issue('required_missing', 'blocking', loc)
                elif column.kind in {'decimal', 'money'}:
                    if len(value) > 128 or not NUM.fullmatch(value):
                        kind, value = 'blocked', None
                        issue('numeric_ambiguous', 'blocking', loc)
                    else:
                        # Decimal text formatting is exact and independent of context precision.
                        value = format(Decimal(value), 'f')
                        if '.' in value:
                            value = value.rstrip('0').rstrip('.')
                        if Decimal(value) == 0:
                            value = '0'
                elif column.kind == 'date':
                    try:
                        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
                            raise ValueError
                        value = date.fromisoformat(value).isoformat()
                    except ValueError:
                        kind, value = 'blocked', None
                        issue('date_ambiguous', 'blocking', loc)
                elif column.kind == 'boolean':
                    if value not in {'true', 'false'}:
                        kind, value = 'blocked', None
                        issue('boolean_ambiguous', 'blocking', loc)
                elif value.lstrip().startswith(('=', '+', '-', '@')):
                    issue('formula_like_text', 'blocking', loc)
                row.append(Cell(value=value, kind=kind, location=loc))
            if ri != 1:
                output.append(tuple(row))
        tables.append(Table(sheet=sheet, columns=tuple(columns), rows=tuple(output)))
    normalized = digest([{'columns':[c.model_dump() for c in t.columns], 'rows':[[{'value':c.value, 'kind':c.kind} for c in row] for row in t.rows]} for t in tables])
    contract_hash = digest({'policy':'tabular-v1', 'columns':[c.model_dump() for c in spec.columns]})
    revision = digest({'project':spec.project_id,'dataset':spec.dataset_id,'source':source_hash,'normalized':normalized,'contract':contract_hash})
    return Dataset(project_id=spec.project_id, dataset_id=spec.dataset_id, source_sha256=source_hash, normalized_sha256=normalized, parse_contract_sha256=contract_hash, revision_id=revision, source_format=fmt, status='needs_review' if any(i.severity == 'blocking' for i in issues) else 'validated', tables=tuple(tables), issues=tuple(issues))
