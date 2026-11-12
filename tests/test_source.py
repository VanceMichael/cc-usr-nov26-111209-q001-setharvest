import base64
import json
import threading
import unittest
import urllib.parse
import urllib.request

from setharvest.source_server import FixtureServer
from setharvest.transport import fetch


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.server = FixtureServer()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def get(self, path):
        return fetch(self.base + path)

    def phase(self, value):
        self.assertEqual(self.get('/control/phase?value=' + value)[0], 200)

    def test_collection_withdrawal_is_not_source_deletion(self):
        self.phase('withdraw')
        self.assertIn(b'noRecordsMatch', self.get('/oai?verb=ListRecords&set=bridge')[2])
        self.assertIn(b'report:7', self.get('/oai?verb=ListRecords&set=materials')[2])
        self.phase('deleted')
        self.assertIn(b'status="deleted"', self.get('/oai?verb=ListRecords&set=materials')[2])
        self.assertNotIn(b'status="deleted"', self.get('/partner/oai?verb=ListRecords&set=materials')[2])

    def test_expired_token_is_protocol_error(self):
        self.phase('expired')
        status, _, body = self.get('/oai?verb=ListRecords&resumptionToken=next%2B1')
        self.assertEqual(status, 200)
        self.assertIn(b'badResumptionToken', body)

    def test_window_endpoints_and_precision(self):
        self.assertIn(b'noRecordsMatch', self.get('/oai?verb=ListRecords&from=2026-11-01&until=2026-11-01')[2])
        self.assertIn(b'report:7', self.get('/oai?verb=ListRecords&from=2026-11-02&until=2026-11-02')[2])
        self.assertIn(b'badArgument', self.get('/oai?verb=ListRecords&from=invalid')[2])
        self.assertIn(b'YYYY-MM-DDThh:mm:ssZ', self.get('/partner/oai?verb=Identify')[2])

    def test_identifiers_have_headers_not_metadata(self):
        body = self.get('/oai?verb=ListIdentifiers')[2]
        self.assertIn(b'ListIdentifiers', body)
        self.assertNotIn(b'metadata', body)
