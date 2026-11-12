import unittest
from pathlib import Path
from setharvest.parser import inspect_document

class ParserTests(unittest.TestCase):
    def test_sets_and_token(self):
        result = inspect_document(Path('setharvest/fixtures/raw.xml').read_bytes())
        self.assertEqual(result['token'], 'next+1')
        self.assertEqual(result['records'][0]['sets'], ['bridge', 'materials'])

    def test_deleted_and_error(self):
        result = inspect_document(Path('setharvest/fixtures/deleted.xml').read_bytes())
        self.assertTrue(result['records'][0]['deleted'])
        result = inspect_document(Path('setharvest/fixtures/expired.xml').read_bytes())
        self.assertEqual(result['errors'][0]['code'], 'badResumptionToken')

    def test_namespace_required(self):
        with self.assertRaises(ValueError):
            inspect_document(b'<OAI-PMH/>')

    def test_header_requires_identity(self):
        with self.assertRaises(ValueError):
            inspect_document(b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><header/></OAI-PMH>')
