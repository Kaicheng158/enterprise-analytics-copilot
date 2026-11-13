import multiprocessing
import unittest
from pydantic import ValidationError
from backend.datasets import Column, FileSpec, ParseSettings, ParseFailure, parse_file, preview
from backend.datasets.parser import parse_core
from dataset_fixtures import workbook

ON = ParseSettings(enabled=True)


def spec(ext='csv', columns=None, **kw):
    return FileSpec(project_id=kw.get('project','p1'), dataset_id='d1', filename='synthetic.'+ext,
                    columns=columns if columns is not None else (Column(name='name'),))


class DatasetParsingTests(unittest.TestCase):
    def parse(self, data=b'name\nalpha\n', ext='csv', columns=None, **limits):
        return parse_core(data, spec(ext, columns), ParseSettings(enabled=True, **limits))

    def rejected(self, data, ext='csv', code=None, **limits):
        with self.assertRaises(ParseFailure) as err:
            self.parse(data, ext, **limits)
        if code:
            self.assertEqual(err.exception.code, code)
        return err.exception.code

    def test_csv_xlsx_equivalence_and_exact_decimal(self):
        cols=(Column(name='地区'),Column(name='金额',kind='money',unit='USD'),Column(name='日期',kind='date'))
        rows=[['地区','金额','日期'],['华东','12345678901234567890123456789.123400','2026-10-29']]
        csv=self.parse((','.join(rows[0])+'\n'+','.join(rows[1])+'\n').encode(),columns=cols)
        xlsx=self.parse(workbook(rows),'xlsx',cols)
        self.assertEqual(csv.normalized_sha256,xlsx.normalized_sha256)
        self.assertNotEqual(csv.source_sha256,xlsx.source_sha256)
        self.assertEqual(csv.status,'validated')
        self.assertEqual(csv.tables[0].rows[0][1].value,'12345678901234567890123456789.1234')
        self.assertEqual(xlsx.tables[0].rows[0][0].location.sheet,'合成数据')

    def test_bom_nfc_and_physical_lines(self):
        result=self.parse(b'\xef\xbb\xbfname\r\n"a\r\nb"\r\n')
        cell=result.tables[0].rows[0][0]
        self.assertEqual(cell.value,'a\nb')
        self.assertEqual((cell.location.line_start,cell.location.line_end),(2,3))
        self.assertEqual(self.parse('name\ne\u0301\n'.encode()).normalized_sha256,self.parse('name\né\n'.encode()).normalized_sha256)

    def test_null_not_zero_and_no_na_guess(self):
        result=self.parse(b'name\n""\nNA\nNULL\n0\n')
        self.assertEqual([r[0].value for r in result.tables[0].rows],[None,'NA','NULL','0'])

    def test_ambiguities_collected_once(self):
        columns=(Column(name='date',kind='date'),Column(name='amount',kind='money',unit='USD'),Column(name='required',required=True))
        result=self.parse(b'date,amount,required\n01/02/2026,"1,000",\n',columns=columns)
        self.assertEqual({i.code for i in result.issues},{'date_ambiguous','numeric_ambiguous','required_missing'})
        self.assertEqual(result.status,'needs_review')

    def test_duplicate_revisions_and_project_isolation(self):
        a=self.parse()
        self.assertEqual(len({self.parse().revision_id for _ in range(3)}),1)
        other=parse_core(b'name\nalpha\n',spec(project='other'),ON)
        self.assertNotEqual(a.revision_id,other.revision_id)
        bom=self.parse(b'\xef\xbb\xbfname\nalpha\n')
        self.assertEqual(a.normalized_sha256,bom.normalized_sha256)
        self.assertNotEqual(a.revision_id,bom.revision_id)
        self.assertNotEqual(a.revision_id,self.parse(b'name\nbeta\n').revision_id)

    def test_mapping_is_explicit(self):
        result=self.parse(columns=())
        self.assertEqual(result.status,'needs_review')
        self.assertEqual(result.issues[0].code,'schema_confirmation_required')
        self.rejected(b'other\nalpha\n',code='schema_mismatch')
        with self.assertRaises(ValidationError): Column(name='money',kind='money')
        with self.assertRaises(ValidationError): FileSpec(project_id='p',dataset_id='d',filename='x.csv',unknown=True)
        for path in ('../x.csv','/x.xlsx','x.xlsm','x.CSV','x.csv.exe'):
            with self.subTest(path=path),self.assertRaises(ValidationError): spec_obj=FileSpec(project_id='p',dataset_id='d',filename=path)

    def test_headers_and_width(self):
        self.assertEqual(self.parse(b' name \nalpha\n').issues[0].code,'header_normalized')
        for data in (b'name,name\na,b\n',b'name, name \na,b\n',b',other\na,b\n'):
            self.rejected(data,code='invalid_header')
        self.rejected(b'name\na,b\n',code='invalid_row_width')

    def test_bad_files_and_encoding(self):
        for data in (b'',b'name\n'):
            self.rejected(data,code='empty_file')
        self.rejected(b'name\n\xff\n',code='unsupported_encoding')
        self.rejected(b'name\n"unterminated',code='invalid_csv')
        self.rejected(b'name\n\x00\n',code='invalid_text')
        self.rejected(b'PKbad',code='format_mismatch')
        self.rejected(b'not zip','xlsx',code='format_mismatch')
        self.rejected(b'PK\x03\x04bad','xlsx',code='invalid_archive')

    def test_limits(self):
        tests=[(b'name\na\n',{'max_bytes':3}),(b'name\na\nb\n',{'max_rows':1}),
               (b'name,x\na,b\n',{'max_columns':1}),(b'name\na\n',{'max_cells':1}),
               (b'name\nlongvalue\n',{'max_field_chars':5})]
        for data,limits in tests:
            with self.subTest(limits=limits): self.rejected(data,**limits)
        self.rejected(workbook([['name'],['alpha']]),'xlsx',max_members=2)
        self.rejected(workbook([['name'],['alpha']]),'xlsx',max_expanded_bytes=200)
        self.rejected(workbook([['name'],['alpha']]),'xlsx',max_member_bytes=100)
        with self.assertRaises(ValidationError): ParseSettings(max_bytes=3*1024*1024)

    def test_zip_bomb_and_path(self):
        self.rejected(workbook([['name'],['a']],extra={'xl/styles.xml':'x'*100000}),'xlsx',code='archive_expansion_limit')
        self.rejected(workbook([['name'],['a']],extra={'../escape':'bad'}),'xlsx',code='unsafe_archive_path')
        self.rejected(workbook([['name'],['a']],extra={'xl/vbaProject.bin':'bad'}),'xlsx',code='unsupported_workbook_part')

    def test_external_and_xml_entities(self):
        payload=workbook([['name'],['a']],changes={'xl/_rels/workbook.xml.rels':lambda s:s.replace('Target="worksheets/sheet1.xml"','Target="https://invalid.example/secret" TargetMode="External"')})
        self.rejected(payload,'xlsx',code='external_relationship')
        payload=workbook([['name'],['a']],extra={'xl/styles.xml':'<!DOCTYPE a [<!ENTITY x "bad">]><a>&x;</a>'})
        self.rejected(payload,'xlsx',code='unsafe_xml')

    def test_formula_cache_never_trusted(self):
        payload=workbook([['name'],['a']],changes={'xl/worksheets/sheet1.xml':lambda s:s.replace('<is><t>a</t></is>','<f>1+1</f><v>2</v>')})
        self.rejected(payload,'xlsx',code='formula_or_unsupported_sheet')
        result=self.parse(b'name\n=HYPERLINK("invalid")\n')
        self.assertEqual(result.issues[0].code,'formula_like_text')
        self.assertEqual(result.status,'needs_review')

    def test_hidden_merged_and_styled(self):
        for before,after in (('<row r="2">','<row r="2" hidden="1">'),('</sheetData>','</sheetData><mergeCells/>')):
            self.rejected(workbook([['name'],['a']],changes={'xl/worksheets/sheet1.xml':lambda s:s.replace(before,after)}),'xlsx')
        payload=workbook([['name'],['a']],changes={'xl/worksheets/sheet1.xml':lambda s:s.replace('r="A2"','r="A2" s="1"')})
        self.assertEqual(self.parse(payload,'xlsx').issues[0].code,'styled_cell_requires_review')

    def test_decimal_date_boolean_and_units(self):
        for value in ('1e3','1,000','$10','12%',' 10','+10','NaN'):
            import csv,io
            f=io.StringIO(); csv.writer(f).writerows([['name'],[value]])
            result=self.parse(f.getvalue().encode(),columns=(Column(name='name',kind='decimal'),))
            self.assertEqual(result.issues[0].code,'numeric_ambiguous')
        self.assertEqual(self.parse(b'name\n-0.00\n',columns=(Column(name='name',kind='decimal'),)).tables[0].rows[0][0].value,'0')
        for value in ('45200','2026-02-30'):
            self.assertEqual(self.parse(('name\n'+value+'\n').encode(),columns=(Column(name='name',kind='date'),)).status,'needs_review')
        self.assertEqual(self.parse(b'name\nyes\n',columns=(Column(name='name',kind='boolean'),)).issues[0].code,'boolean_ambiguous')

    def test_preview_redaction_bounds_and_no_mutation(self):
        result=self.parse(b'name\n<script>alert(1)</script>\n'+b'alpha\n'*8)
        before=result.model_dump_json()
        self.assertNotIn('script',str(preview(result)))
        shown=preview(result,reveal_columns=(1,))
        self.assertIn('&lt;script&gt;',str(shown))
        self.assertEqual(len(shown['tables'][0]['rows']),5)
        self.assertNotIn('script',repr(result))
        self.assertEqual(before,result.model_dump_json())
        with self.assertRaises(ParseFailure): preview(result,reveal_columns=(9,))

    def test_process_default_off_and_recovery(self):
        with self.assertRaisesRegex(ParseFailure,'parsing_disabled'): parse_file(b'name\na\n',spec())
        with self.assertRaises(ParseFailure): parse_file(b'name\n\xff\n',spec(),ON)
        result=parse_file(b'name\na\n',spec(),ON)
        self.assertEqual(result.status,'validated')
        self.assertFalse(multiprocessing.active_children())

    def test_process_wall_timeout_and_cleanup(self):
        with self.assertRaisesRegex(ParseFailure,'parse_timeout'):
            parse_file(b'name\na\n',spec(),ParseSettings(enabled=True,wall_seconds=0.000001))
        self.assertFalse(multiprocessing.active_children())
        self.assertEqual(parse_file(b'name\na\n',spec(),ON).status,'validated')

    def test_xlsx_shared_numeric_boolean_and_iso_date(self):
        cols=(Column(name='name',kind='decimal'),)
        for content,expected in (('<c r="A2"><v>12.30</v></c>','12.3'),):
            import re
            blob=workbook([['name'],['a']],changes={'xl/worksheets/sheet1.xml':lambda s:re.sub(r'<c r="A2".*?</c>',content,s)})
            self.assertEqual(self.parse(blob,'xlsx',cols).tables[0].rows[0][0].value,expected)
        blob=workbook([['name'],['a']],changes={'xl/worksheets/sheet1.xml':lambda s:s.replace('<c r="A2" t="inlineStr"><is><t>a</t></is></c>','<c r="A2" t="s"><v>0</v></c>')},extra={'xl/sharedStrings.xml':'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>中文</t></si></sst>'})
        self.assertEqual(self.parse(blob,'xlsx').tables[0].rows[0][0].value,'中文')

    def test_sheet_count_reference_and_issue_caps(self):
        blob=workbook([['name'],['a']],changes={'xl/workbook.xml':lambda s:s.replace('</sheets>','<sheet name="second" sheetId="2"/></sheets>')})
        self.rejected(blob,'xlsx',code='sheet_limit',max_sheets=1)
        blob=workbook([['name'],['a']],changes={'xl/worksheets/sheet1.xml':lambda s:s.replace('r="A2"','r="ZZ999999"')})
        self.rejected(blob,'xlsx',code='dimension_limit')
        self.rejected(b'name\n=x\n=y\n',code='issue_limit',max_issues=1)

    def test_unknown_cell_content_not_silently_ignored(self):
        blob=workbook([['name'],['a']],changes={'xl/worksheets/sheet1.xml':lambda s:s.replace('</sheetData>','</sheetData><unknown/>')})
        self.rejected(blob,'xlsx',code='unsupported_workbook_feature')
        blob=workbook([['name'],['a']],changes={'xl/worksheets/sheet1.xml':lambda s:s.replace('<is><t>a</t></is>','<is><t>a</t></is><v>secret</v>')})
        self.rejected(blob,'xlsx',code='invalid_cell')

    def test_worker_errors_never_reveal_content(self):
        import contextlib,io
        output=io.StringIO()
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):
            with self.assertRaises(ParseFailure) as error:
                parse_file(b'name\n"SYNTHETIC_PRIVATE_UNCLOSED',spec(),ON)
        self.assertNotIn('SYNTHETIC_PRIVATE',str(error.exception))
        self.assertEqual(output.getvalue(),'')

    def test_mac_memory_guard_stops_worker(self):
        import sys
        from unittest.mock import patch
        from types import SimpleNamespace
        if sys.platform != 'darwin': self.skipTest('macOS RSS guard')
        with patch('backend.datasets.service.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'999999')):
            with self.assertRaisesRegex(ParseFailure,'resource_limit'): parse_file(b'name\na\n',spec(),ON)
        self.assertFalse(multiprocessing.active_children())

    def test_worker_output_and_allocation_failure_are_safe(self):
        from unittest.mock import Mock,patch
        from backend.datasets.service import _worker
        for failure in ('output','memory','unexpected'):
            channel=Mock()
            result=Mock()
            result.model_dump_json.return_value='x'*32
            with patch('resource.setrlimit'),patch('backend.datasets.service.MAX_RESULT',16),patch('backend.datasets.service.parse_core') as core:
                if failure=='output': core.return_value=result
                elif failure=='memory': core.side_effect=MemoryError('sensitive')
                else: core.side_effect=ValueError('sensitive')
                _worker(channel,b'',spec(),ON)
            expected={'output':b'-result_limit','memory':b'-resource_limit','unexpected':b'-invalid_file'}[failure]
            channel.send_bytes.assert_called_once_with(expected)
            channel.close.assert_called_once()

    def test_phonetic_annotations_are_not_cell_values(self):
        blob=workbook([['name'],['中文']],changes={'xl/worksheets/sheet1.xml':lambda s:s.replace('<t>中文</t>','<t>中文</t><rPh sb="0" eb="2"><t>zhongwen</t></rPh>')})
        self.rejected(blob,'xlsx',code='unsupported_workbook_feature')
        blob=workbook([['name'],['a']],extra={'xl/sharedStrings.xml':'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>中文</t><rPh><t>annotation</t></rPh></si></sst>'})
        self.rejected(blob,'xlsx',code='unsupported_workbook_feature')

    def test_inherited_style_cannot_bypass_review(self):
        for transform in (lambda s:s.replace('<row r="2">','<row r="2" s="1">'),lambda s:s.replace('<sheetData>','<cols><col min="1" max="1" style="1"/></cols><sheetData>')):
            blob=workbook([['name'],['45200']],changes={'xl/worksheets/sheet1.xml':transform})
            self.rejected(blob,'xlsx',code='unsupported_inherited_style')

    def test_sheet_data_unknown_children_fail_closed(self):
        blob=workbook([['name'],['a']],changes={'xl/worksheets/sheet1.xml':lambda s:s.replace('</sheetData>','<unknown>not silently ignored</unknown></sheetData>')})
        self.rejected(blob,'xlsx',code='invalid_sheet')


if __name__ == '__main__': unittest.main()
