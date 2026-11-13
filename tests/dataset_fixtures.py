"""Generated synthetic OOXML only; no customer files or external dependency."""
import io
import zipfile
from xml.sax.saxutils import escape

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P = 'http://schemas.openxmlformats.org/package/2006/relationships'


def workbook(rows, *, changes=None, extra=None):
    cells = []
    for ri, row in enumerate(rows, 1):
        content = ''.join(f'<c r="{chr(65+ci)}{ri}" t="inlineStr"><is><t>{escape(value)}</t></is></c>' for ci,value in enumerate(row))
        cells.append(f'<row r="{ri}">{content}</row>')
    parts = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        '_rels/.rels': f'<Relationships xmlns="{P}"><Relationship Id="office" Target="xl/workbook.xml" Type="{R}/officeDocument"/></Relationships>',
        'xl/workbook.xml': f'<workbook xmlns="{NS}" xmlns:r="{R}"><sheets><sheet name="合成数据" sheetId="1" r:id="sheet"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': f'<Relationships xmlns="{P}"><Relationship Id="sheet" Target="worksheets/sheet1.xml" Type="{R}/worksheet"/></Relationships>',
        'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{NS}"><sheetData>{"".join(cells)}</sheetData></worksheet>',
    }
    for name, transform in (changes or {}).items():
        parts[name] = transform(parts[name])
    parts.update(extra or {})
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, value in parts.items():
            z.writestr(name, value)
    return out.getvalue()
