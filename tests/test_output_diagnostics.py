import io
import json
import unittest
from unittest.mock import patch
from pydantic import ValidationError
from backend.config import LLMSettings, load_settings
from backend.llm import DeepSeekProvider, ProviderError
from backend.output import parse_answer, OutputFailure
from eval.run_uncertainty import collect
from test_retry_cost import success


class OutputDiagnosticsTests(unittest.TestCase):
    def test_categories_and_no_sensitive_values(self):
        valid={'summary':'s','facts':[],'interpretation':[],'limitations':[]}
        cases=[('', 'empty_content'),('private secret prose','non_json'),('{"private-key":','json_parse_error'),
               (json.dumps({'summary':'private secret'}),'schema_field_missing'),
               (json.dumps({**valid,'facts':'private secret'}),'type_error'),
               (json.dumps({**valid,'private-key':'private secret'}),'extra_field'),
               ('{"summary":1,"summary":2}','invalid_json_structure')]
        for content,expected in cases:
            with self.subTest(expected=expected),self.assertRaises(OutputFailure) as caught:
                parse_answer(content)
            self.assertEqual(caught.exception.diagnostic['reason'],expected)
            self.assertNotIn('private',str(caught.exception.diagnostic))

    def test_transport_diagnostic_usage_eval_logging_and_no_retry(self):
        for content,finish,reason in [('', 'stop','empty_content'),('{','stop','json_parse_error'),
                                      ('private secret','length','truncation'),('{}','private secret','incomplete_output')]:
            data=json.loads(success().getvalue())
            data['choices'][0]['message']['content']=content
            data['choices'][0]['finish_reason']=finish
            with self.subTest(reason=reason),patch('backend.llm.urlopen',return_value=io.BytesIO(json.dumps(data).encode())) as call,patch('backend.llm.time.sleep') as sleep,self.assertLogs('uvicorn.error',level='INFO') as logs:
                report=collect(DeepSeekProvider('private-key','deepseek-flash'),[{'id':'synthetic','input':'Synthetic'}])
            row=report['records'][0]
            self.assertEqual(row['error_code'],'llm_invalid_output')
            self.assertEqual(row['output_diagnostic']['reason'],reason)
            self.assertEqual(row['finish_reason'],finish if finish in ['stop','length'] else 'unknown')
            self.assertIsNotNone(row['usage'])
            self.assertEqual(row['retry_count'],0)
            self.assertNotIn('private',json.dumps(row)+str(logs.output))
            self.assertEqual(call.call_count,1);sleep.assert_not_called()

    def test_temperature_is_explicit_validated_and_serialized(self):
        for value in [-0.1,2.1,float('nan'),float('inf')]:
            with self.assertRaises(ValidationError):LLMSettings(temperature=value)
        with patch.dict('os.environ',{'LLM_TEMPERATURE':'0.2'},clear=True),patch('backend.config.load_dotenv'):
            settings=load_settings()
        self.assertEqual(settings.temperature,0.2)
        with patch('backend.llm.urlopen',return_value=success()) as call:
            DeepSeekProvider('key','deepseek-flash',settings=settings).chat('Synthetic')
        self.assertEqual(json.loads(call.call_args.args[0].data)['temperature'],0.2)
        self.assertEqual(settings.model_dump(exclude={'api_key'})['temperature'],0.2)
