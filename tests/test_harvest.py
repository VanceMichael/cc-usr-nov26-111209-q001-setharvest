import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from setharvest.harvest import HarvestStore, request_url, run_harvest
from setharvest.source_server import FixtureServer
from setharvest.transport import fetch


class HarvestTests(unittest.TestCase):
    def setUp(self):
        self.server = FixtureServer()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_address[1])
        self.source = self.base + '/oai'
        self.partner = self.base + '/partner/oai'
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / 'harvest.sqlite3'
        self.objects = Path(self.directory.name) / 'objects'
        self.store = HarvestStore(self.database, self.objects)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def phase(self, value):
        self.assertEqual(fetch(self.base + '/control/phase?value=' + value)[0], 200)

    def harvest(self, source=None, **kwargs):
        return run_harvest(self.store, source or self.source, 'oai_dc', **kwargs)

    def test_cross_set_withdraw_then_delete(self):
        self.harvest(set_spec='bridge')
        self.harvest(set_spec='materials')
        self.harvest(source=self.partner)
        records = {record['source']: record for record in self.store.current_records()}
        self.assertEqual(set(records), {self.source, self.partner})
        self.assertEqual(records[self.source]['sets'], ['bridge', 'materials'])
        self.assertEqual(records[self.partner]['sets'], ['bridge', 'materials'])
        self.phase('withdraw')
        self.harvest(set_spec='bridge')
        self.harvest(set_spec='materials')
        record = self.store.current_records(source=self.source)[0]
        self.assertEqual(record['sets'], ['materials'])
        self.assertEqual(record['status'], 'present')
        self.assertEqual(self.store.current_records(source=self.source, set_spec='bridge'), [])
        self.assertEqual(len(self.store.current_records(source=self.source, set_spec='materials')), 1)
        partner = self.store.current_records(source=self.partner)[0]
        self.assertEqual(partner['sets'], ['bridge', 'materials'])
        self.phase('deleted')
        self.harvest(set_spec='materials')
        self.assertEqual(self.store.current_records(source=self.source), [])
        remaining = self.store.current_records(identifier='report:7')
        self.assertEqual([record['source'] for record in remaining], [self.partner])
        detail = self.store.explain_record(self.source, 'report:7')
        self.assertEqual(detail['status'], 'deleted')
        bridge = next(m for m in detail['memberships'] if m['set'] == 'bridge')
        self.assertEqual(bridge['status'], 'revoked')
        revoked = next(e for e in bridge['events'] if e['kind'] == 'revoked')
        affirmed = [e for e in bridge['events'] if e['kind'] == 'affirmed']
        self.assertIsNone(revoked['page_id'])
        self.assertTrue(affirmed)
        self.assertTrue(all(e['page_id'] for e in affirmed))
        self.assertTrue(all(e['url'] for e in affirmed))
        materials = next(m for m in detail['memberships'] if m['set'] == 'materials')
        self.assertEqual(materials['status'], 'present')
        self.assertTrue(any(o['deleted'] for o in detail['observations']))

    def test_incremental_window_never_revokes_membership(self):
        self.harvest(set_spec='bridge')
        self.assertEqual(self.store.current_records(source=self.source)[0]['sets'],
                         ['bridge', 'materials'])
        summary = self.harvest(set_spec='bridge', window_from='2026-11-05', window_until='2026-11-06')
        self.assertEqual(summary['status'], 'complete')
        self.assertEqual(summary['kind'], 'incremental')
        self.assertEqual(self.store.current_records(source=self.source)[0]['sets'],
                         ['bridge', 'materials'])
        self.phase('withdraw')
        self.harvest(set_spec='bridge')
        record = self.store.current_records(source=self.source, include_withdrawn=True)[0]
        self.assertEqual(record['sets'], ['materials'])
        self.assertEqual(record['revoked_sets'], ['bridge'])
        self.assertEqual(record['status'], 'present')

    def test_restart_resume_and_same_page_replay(self):
        summary = self.harvest(set_spec='bridge', max_pages=1)
        self.assertEqual(summary['status'], 'paging')
        self.assertEqual(summary['pages'], 1)
        self.store.close()
        self.store = HarvestStore(self.database, self.objects)
        scan = self.store.get_scan(summary['scan_id'])
        self.assertEqual(scan['token'], 'next+1')
        pages = self.store.pages_of(summary['scan_id'])
        self.assertEqual(len(pages), 1)
        self.assertTrue((self.objects / pages[0]['digest']).exists())
        # 模拟中断的写方:页已确认但游标被重访,同样内容不再落账,不同内容报错
        self.store.connection.execute('update scans set page_no=0, token=null where id=?',
                                      (summary['scan_id'],))
        url = request_url(self.source, 'oai_dc', 'bridge', None, None, None)
        status, content_type, body = fetch(url)
        self.assertEqual(self.store.commit_page(summary['scan_id'], url, status, content_type, body),
                         'replay')
        self.assertEqual(len(self.store.pages_of(summary['scan_id'])), 1)
        with self.assertRaises(ValueError):
            self.store.commit_page(summary['scan_id'], url, 200, 'text/xml', b'<OAI-PMH/>')
        self.store.connection.execute("update scans set page_no=1, token='next+1' where id=?",
                                      (summary['scan_id'],))
        summary = self.harvest(set_spec='bridge')
        self.assertEqual(summary['status'], 'complete')
        pages = self.store.pages_of(summary['scan_id'])
        self.assertEqual(len(pages), 2)
        self.assertIn('resumptionToken=next%2B1', pages[1]['url'])
        record = self.store.current_records(source=self.source)[0]
        self.assertEqual(record['sets'], ['bridge', 'materials'])
        self.assertEqual(record['representations']['oai_dc']['datestamp'], '2026-11-02')

    def test_token_expiry_reopens_window_and_keeps_evidence(self):
        summary = self.harvest(set_spec='bridge', max_pages=1)
        scan_id = summary['scan_id']
        self.phase('expired')
        summary = self.harvest(set_spec='bridge', max_rounds=2)
        self.assertEqual(summary['status'], 'failed')
        self.assertIn('过期', summary['failure'])
        record = self.store.current_records(source=self.source)[0]
        self.assertEqual(record['sets'], ['bridge', 'materials'])
        detail = self.store.explain_record(self.source, 'report:7')
        bridge = next(m for m in detail['memberships'] if m['set'] == 'bridge')
        pages = {event['page_id'] for event in bridge['events']}
        self.assertGreater(len(pages), 1)
        self.assertEqual({event['scan_id'] for event in bridge['events']}, {scan_id})
        self.phase('initial')
        summary = self.harvest(set_spec='bridge')
        self.assertEqual(summary['status'], 'complete')
        outcomes = [page['outcome'] for page in self.store.pages_of(scan_id)]
        self.assertIn('token_expired', outcomes)
        self.assertEqual(self.store.current_records(source=self.source)[0]['sets'],
                         ['bridge', 'materials'])

    def test_failure_then_continue(self):
        def broken(url):
            raise OSError('connection reset')

        summary = run_harvest(self.store, self.source, 'oai_dc', set_spec='bridge', fetcher=broken)
        self.assertEqual(summary['status'], 'failed')
        self.assertIn('connection reset', summary['failure'])
        summary = self.harvest(set_spec='bridge')
        self.assertEqual(summary['status'], 'complete')
        self.assertEqual(self.store.current_records(source=self.source)[0]['identifier'], 'report:7')

    def test_bad_argument_is_failure_not_empty_window(self):
        summary = self.harvest(set_spec='bridge', window_from='invalid')
        self.assertEqual(summary['status'], 'failed')
        self.assertIn('badArgument', summary['failure'])
        summary = self.harvest(set_spec='bridge', window_from='2026-11-05', window_until='2026-11-06')
        self.assertEqual(summary['status'], 'complete')
        self.assertEqual(summary['failure'], None)

    def test_conflict_same_datestamp_kept(self):
        self.harvest(set_spec='bridge')
        self.phase('conflict')
        self.harvest(set_spec='bridge')
        record = self.store.current_records(source=self.source)[0]
        representation = record['representations']['oai_dc']
        self.assertEqual(representation['datestamp'], '2026-11-02')
        self.assertEqual(representation['variants'], 2)
        self.assertTrue(representation['conflict'])
        detail = self.store.explain_record(self.source, 'report:7')
        bodies = {row['body'] for row in detail['representations'] if row['datestamp'] == '2026-11-02'}
        self.assertEqual(len(bodies), 2)
        self.assertTrue(any('Changed content' in body for body in bodies))
        self.assertTrue(any('Bridge material' in body for body in bodies))
        self.assertTrue(all(row['conflict'] for row in detail['representations']))

    def test_late_older_record_keeps_current_representation(self):
        self.phase('withdraw')
        self.harvest(set_spec='materials')
        self.phase('initial')
        self.harvest(set_spec='materials')
        record = self.store.current_records(source=self.source)[0]
        representation = record['representations']['oai_dc']
        self.assertEqual(representation['datestamp'], '2026-11-03')
        self.assertFalse(representation['conflict'])
        detail = self.store.explain_record(self.source, 'report:7')
        self.assertEqual({row['datestamp'] for row in detail['representations']},
                         {'2026-11-02', '2026-11-03'})

    def test_window_endpoints_inclusive_and_overlap_idempotent(self):
        summary = self.harvest(set_spec='bridge', window_from='2026-11-02', window_until='2026-11-02')
        self.assertEqual(summary['status'], 'complete')
        self.assertEqual(len(self.store.current_records(source=self.source)), 1)
        summary = self.harvest(set_spec='bridge', window_from='2026-11-01', window_until='2026-11-03')
        self.assertEqual(summary['status'], 'complete')
        records = self.store.current_records(source=self.source)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['representations']['oai_dc']['variants'], 1)
        summary = self.harvest(source=self.partner, window_from='2026-11-02T10:00:00Z',
                               window_until='2026-11-02T10:00:00Z')
        self.assertEqual(summary['status'], 'complete')
        self.assertEqual(len(self.store.current_records(source=self.partner)), 1)
        summary = self.harvest(source=self.partner, window_from='2026-11-02T10:00:01Z',
                               window_until='2026-11-02T10:00:02Z')
        self.assertEqual(summary['status'], 'complete')
        self.assertEqual(len(self.store.current_records(source=self.partner)), 1)

    def test_cli_harvest_query_explain_across_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / 'h.sqlite3')
            objects = str(Path(directory) / 'objects')

            def cli(*args):
                result = subprocess.run([sys.executable, '-m', 'setharvest', *args],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            summary = cli('harvest', '--source', self.source, '--prefix', 'oai_dc', '--set', 'bridge',
                          '--max-pages', '1', '--db', database, '--objects', objects)
            self.assertEqual(summary['status'], 'paging')
            summary = cli('harvest', '--source', self.source, '--prefix', 'oai_dc', '--set', 'bridge',
                          '--db', database, '--objects', objects)
            self.assertEqual(summary['status'], 'complete')
            found = cli('query', '--set', 'bridge', '--db', database, '--objects', objects)
            self.assertEqual(found['records'][0]['identifier'], 'report:7')
            detail = cli('explain', '--source', self.source, '--identifier', 'report:7',
                         '--db', database, '--objects', objects)
            self.assertEqual(detail['status'], 'present')
            self.assertEqual(detail['memberships'][0]['set'], 'bridge')

    def test_demo_cli(self):
        result = subprocess.run([sys.executable, '-m', 'setharvest', 'demo'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        steps = {step['step']: step for step in json.loads(result.stdout)['steps']}
        self.assertIn('capture', steps)
        initial = {record['source'] for record in steps['query-initial']['records']}
        self.assertEqual(len(initial), 2)
        withdrawn = steps['query-after-withdraw']['records']
        by_source = {record['source']: record for record in withdrawn}
        self.assertEqual(len(by_source), 2)
        remaining = [record for record in steps['query-after-delete']['records']]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(steps['explain']['record']['status'], 'deleted')


if __name__ == '__main__':
    unittest.main()
