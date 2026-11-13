DESCRIPTION = '机构资料 OAI-PMH 响应的查看、原文留存与请求实验工具。'
DEMO_PATH = '/sample'

import argparse
import json
import tempfile
import threading
from pathlib import Path

from .archive import Archive
from .harvest import HarvestStore, run_harvest
from .parser import inspect_document
from .source_server import FixtureServer
from .transport import fetch


def main():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    commands = parser.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('path')
    capture = commands.add_parser('capture')
    capture.add_argument('url')
    capture.add_argument('--db', default='captures.sqlite3')
    capture.add_argument('--objects', default='objects')
    show = commands.add_parser('show')
    show.add_argument('--db', default='captures.sqlite3')
    show.add_argument('--objects', default='objects')
    harvest = commands.add_parser('harvest')
    harvest.add_argument('--source', required=True)
    harvest.add_argument('--prefix', required=True)
    harvest.add_argument('--set', dest='set_spec')
    harvest.add_argument('--from', dest='window_from')
    harvest.add_argument('--until', dest='window_until')
    harvest.add_argument('--max-pages', type=int, default=0)
    harvest.add_argument('--max-rounds', type=int, default=4)
    harvest.add_argument('--db', default='captures.sqlite3')
    harvest.add_argument('--objects', default='objects')
    query = commands.add_parser('query')
    query.add_argument('--source')
    query.add_argument('--identifier')
    query.add_argument('--set', dest='set_spec')
    query.add_argument('--all', action='store_true')
    query.add_argument('--db', default='captures.sqlite3')
    query.add_argument('--objects', default='objects')
    explain = commands.add_parser('explain')
    explain.add_argument('--source', required=True)
    explain.add_argument('--identifier', required=True)
    explain.add_argument('--db', default='captures.sqlite3')
    explain.add_argument('--objects', default='objects')
    commands.add_parser('demo')
    arguments = parser.parse_args()
    if arguments.command == 'inspect':
        result = inspect_document(Path(arguments.path).read_bytes())
    elif arguments.command == 'capture':
        archive = Archive(arguments.db, arguments.objects)
        try:
            status, content_type, body = fetch(arguments.url)
            result = {'capture_id': archive.save(arguments.url, status, content_type, body), 'http_status': status}
        finally:
            archive.close()
    elif arguments.command == 'show':
        archive = Archive(arguments.db, arguments.objects)
        try:
            result = archive.rows()
        finally:
            archive.close()
    elif arguments.command == 'harvest':
        store = HarvestStore(arguments.db, arguments.objects)
        try:
            result = run_harvest(store, arguments.source, arguments.prefix, arguments.set_spec,
                                 arguments.window_from, arguments.window_until,
                                 max_pages=arguments.max_pages, max_rounds=arguments.max_rounds)
        finally:
            store.close()
    elif arguments.command == 'query':
        store = HarvestStore(arguments.db, arguments.objects)
        try:
            result = {'records': store.current_records(source=arguments.source,
                                                       identifier=arguments.identifier,
                                                       set_spec=arguments.set_spec,
                                                       include_withdrawn=arguments.all)}
        finally:
            store.close()
    elif arguments.command == 'explain':
        store = HarvestStore(arguments.db, arguments.objects)
        try:
            result = store.explain_record(arguments.source, arguments.identifier)
        finally:
            store.close()
    else:
        result = run_demo()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def run_demo():
    steps = []
    with FixtureServer() as server, tempfile.TemporaryDirectory() as directory:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = 'http://127.0.0.1:' + str(server.server_address[1])
            source, partner = base + '/oai', base + '/partner/oai'
            database, objects = Path(directory) / 'demo.sqlite3', Path(directory) / 'objects'
            archive = Archive(database, objects)
            try:
                status, content_type, body = fetch(base + DEMO_PATH)
                capture_id = archive.save(base + DEMO_PATH, status, content_type, body)
                steps.append({'step': 'capture', 'capture_id': capture_id, 'http_status': status,
                              'observation': inspect_document(archive.read(capture_id))})
            finally:
                archive.close()
            store = HarvestStore(database, objects)
            try:
                def harvest(source_url, set_spec=None):
                    return run_harvest(store, source_url, 'oai_dc', set_spec=set_spec)

                steps.append({'step': 'initial-harvest', 'scans': [
                    harvest(source, 'bridge'), harvest(source, 'materials'), harvest(partner)]})
                steps.append({'step': 'query-initial', 'records': store.current_records()})
                fetch(base + '/control/phase?value=withdraw')
                steps.append({'step': 'withdraw-harvest', 'scans': [
                    harvest(source, 'bridge'), harvest(source, 'materials')]})
                steps.append({'step': 'query-after-withdraw', 'records': store.current_records()})
                fetch(base + '/control/phase?value=deleted')
                steps.append({'step': 'delete-harvest', 'scans': [harvest(source, 'materials')]})
                steps.append({'step': 'query-after-delete', 'records': store.current_records()})
                steps.append({'step': 'explain', 'record': store.explain_record(source, 'report:7')})
            finally:
                store.close()
        finally:
            server.shutdown()
            thread.join(timeout=2)
    return {'steps': steps}


if __name__ == '__main__':
    main()
