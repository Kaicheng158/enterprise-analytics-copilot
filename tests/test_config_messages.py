import json
import unittest
from unittest.mock import patch
from pydantic import ValidationError
from backend.config import LLMSettings, load_settings
from backend.llm import DeepSeekProvider, ProviderError, get_provider
from backend.prompts import SYSTEM_PROMPT_V1
from backend.main import ChatRequest, chat
from test_retry_cost import success


class ConfigMessageTests(unittest.TestCase):
    def settings(self, values):
        with patch.dict('os.environ', values, clear=True), patch('backend.config.load_dotenv'):
            return load_settings()

    def test_defaults_and_secret_redaction(self):
        settings=self.settings({'DEEPSEEK_API_KEY':'sensitive-key'})
        self.assertEqual(settings.model,'deepseek-flash')
        self.assertNotIn('sensitive-key',repr(settings))
        self.assertEqual(settings.max_retries,2)

    def test_overrides_and_legacy_model(self):
        settings=self.settings({'LLM_MODEL':'deepseek-flash','DEEPSEEK_MODEL':'obsolete',
            'LLM_TIMEOUT_SECONDS':'7','LLM_MAX_RETRIES':'0','LLM_MAX_OUTPUT_TOKENS':'64','LLM_SYSTEM_MESSAGE':'Be brief.'})
        self.assertEqual((settings.timeout_seconds,settings.max_retries,settings.max_output_tokens),(7,0,64))
        self.assertEqual(self.settings({'DEEPSEEK_MODEL':'deepseek-flash'}).model,'deepseek-flash')

    def test_invalid_configuration_rejected(self):
        for values in [{'LLM_PROVIDER':'other'},{'LLM_MODEL':'unknown'},{'LLM_MAX_OUTPUT_TOKENS':'0'},
                       {'LLM_MAX_RETRIES':'9'}]:
            with self.subTest(values=values), patch.dict('os.environ',values,clear=True), patch('backend.config.load_dotenv'):
                with self.assertRaises(ProviderError) as caught: get_provider()
                self.assertEqual(caught.exception.code,'llm_invalid_config')

    def test_roles_and_generation_settings(self):
        settings=LLMSettings(max_output_tokens=64)
        provider=DeepSeekProvider('key',settings.model,settings=settings)
        with patch('backend.llm.urlopen',return_value=success()) as call:
            provider.chat('User question')
        body=json.loads(call.call_args.args[0].data)
        self.assertEqual(body['messages'],[{'role':'system','content':SYSTEM_PROMPT_V1},{'role':'user','content':'User question'}])
        self.assertEqual(body['max_tokens'],64)
        self.assertNotIn('tools',body)

    def test_request_schema(self):
        self.assertEqual(ChatRequest(message='Legacy').user_message,'Legacy')
        self.assertEqual(ChatRequest(user_message='New').user_message,'New')
        for values in [{'user_message':'Hi','message':'Other'}, {'user_message':'Hi','system_message':' '},
                       {'user_message':'Hi','system_message':'x'*2001}, {'user_message':'Hi','provider':'other'},
                       {'system_message':'Only system'}]:
            with self.subTest(values=values), self.assertRaises(ValidationError): ChatRequest(**values)

    def test_route_preserves_roles(self):
        class Stub:
            def chat(self,message):
                return message
        self.assertEqual(chat(ChatRequest(user_message='User'),Stub()),'User')

    def test_defaults_not_duplicated_in_business_route(self):
        from pathlib import Path
        route=Path('backend/main.py').read_text()
        self.assertNotIn('deepseek-flash',route)
        self.assertNotIn('api.deepseek.com',route)
