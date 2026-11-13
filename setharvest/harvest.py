"""持续采集:集合成员关系、来源记录与元数据表示各有独立身份。

断点进度只随已接收的页在同一事务里确认;同页重放幂等,内容不一致会报错。
只有某集合的完整重扫(不带 from)确认缺席才撤销该处成员关系;增量窗口缺席不算退出。
只有协议中的 deleted header 才撤回来源记录,且不影响其他来源的同名 identifier。
"""

import hashlib
import sqlite3
import urllib.parse
from pathlib import Path

from .archive import write_object
from .parser import parse_page
from .transport import fetch

SCHEMA = """
create table if not exists scans (
    id integer primary key,
    source text not null,
    metadata_prefix text not null,
    set_spec text,
    window_from text,
    window_until text,
    kind text not null check(kind in ('full', 'incremental')),
    status text not null check(status in ('paging', 'complete', 'failed')),
    round_no integer not null default 0,
    page_no integer not null default 0,
    token text,
    failure text,
    started_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);
create index if not exists scan_window
    on scans(source, metadata_prefix, set_spec, window_from, window_until);
create table if not exists pages (
    id integer primary key,
    scan_id integer not null references scans(id),
    round_no integer not null,
    page_no integer not null,
    url text not null,
    http_status integer not null,
    content_type text not null,
    digest text not null,
    response_date text,
    outcome text not null,
    error_code text,
    received_at text not null default current_timestamp,
    unique(scan_id, round_no, page_no)
);
create table if not exists records (
    id integer primary key,
    source text not null,
    identifier text not null,
    unique(source, identifier)
);
create table if not exists observations (
    id integer primary key,
    record_id integer not null references records(id),
    page_id integer not null references pages(id),
    datestamp text not null,
    deleted integer not null check(deleted in (0, 1)),
    unique(record_id, page_id)
);
create table if not exists memberships (
    id integer primary key,
    record_id integer not null references records(id),
    set_spec text not null,
    status text not null check(status in ('present', 'revoked')),
    unique(record_id, set_spec)
);
create table if not exists membership_events (
    id integer primary key,
    membership_id integer not null references memberships(id),
    scan_id integer not null references scans(id),
    page_id integer references pages(id),
    kind text not null check(kind in ('affirmed', 'revoked')),
    datestamp text
);
create unique index if not exists membership_event_once
    on membership_events(membership_id, kind, scan_id, ifnull(page_id, -1));
create table if not exists representations (
    id integer primary key,
    record_id integer not null references records(id),
    metadata_prefix text not null,
    datestamp text not null,
    digest text not null,
    body text not null,
    first_page_id integer not null references pages(id),
    unique(record_id, metadata_prefix, datestamp, digest)
);
"""


def normalize_source(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'http' or not parsed.hostname:
        raise ValueError('来源必须是 http 地址')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('来源地址不允许凭据、查询或片段')
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip('/'), '', ''))


def request_url(source, metadata_prefix, set_spec, window_from, window_until, token):
    if token:
        params = {'verb': 'ListRecords', 'resumptionToken': token}
    else:
        params = {'verb': 'ListRecords', 'metadataPrefix': metadata_prefix}
        if set_spec is not None:
            params['set'] = set_spec
        if window_from is not None:
            params['from'] = window_from
        if window_until is not None:
            params['until'] = window_until
    return source + '?' + urllib.parse.urlencode(params)


def classify_page(http_status, body):
    if http_status != 200:
        return 'http_error', str(http_status), None, 'HTTP 状态 ' + str(http_status)
    try:
        parsed = parse_page(body)
    except Exception as error:
        return 'invalid', None, None, '响应解析失败: ' + str(error)
    if parsed['errors']:
        code = parsed['errors'][0]['code']
        if code == 'noRecordsMatch':
            return 'empty', code, parsed, None
        if code == 'badResumptionToken':
            return 'token_expired', code, parsed, None
        return 'error', code, parsed, '来源返回错误 ' + str(code)
    return 'records', None, parsed, None


def record_status(max_present, max_deleted):
    if max_deleted is not None and (max_present is None or max_deleted >= max_present):
        return 'deleted'
    return 'present'


class HarvestStore:
    def __init__(self, database, objects):
        self.objects = Path(objects)
        self.objects.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('pragma foreign_keys=on')
        self.connection.executescript(SCHEMA)

    def close(self):
        self.connection.close()

    def begin_scan(self, source, metadata_prefix, set_spec=None, window_from=None, window_until=None):
        source = normalize_source(source)
        row = self.connection.execute(
            "select id from scans where source=? and metadata_prefix=? and set_spec is ? "
            "and window_from is ? and window_until is ? and status != 'complete' "
            "order by id desc limit 1",
            (source, metadata_prefix, set_spec, window_from, window_until)).fetchone()
        if row is not None:
            return row['id']
        kind = 'full' if window_from is None else 'incremental'
        with self.connection:
            cursor = self.connection.execute(
                'insert into scans(source, metadata_prefix, set_spec, window_from, window_until, kind, status) '
                'values(?,?,?,?,?,?,?)',
                (source, metadata_prefix, set_spec, window_from, window_until, kind, 'paging'))
        return cursor.lastrowid

    def get_scan(self, scan_id):
        row = self.connection.execute('select * from scans where id=?', (scan_id,)).fetchone()
        if row is None:
            raise KeyError(scan_id)
        return dict(row)

    def pages_of(self, scan_id):
        return [dict(row) for row in self.connection.execute(
            'select * from pages where scan_id=? order by round_no, page_no', (scan_id,))]

    def mark_failed(self, scan_id, failure):
        with self.connection:
            self.connection.execute(
                "update scans set status='failed', failure=?, updated_at=current_timestamp "
                "where id=? and status != 'complete'", (failure, scan_id))

    def commit_page(self, scan_id, url, http_status, content_type, body, max_rounds=4):
        digest = write_object(self.objects, body)
        with self.connection:
            scan = self.get_scan(scan_id)
            if scan['status'] == 'complete':
                raise ValueError('扫描已完成,不能继续落页')
            existing = self.connection.execute(
                'select id, digest from pages where scan_id=? and round_no=? and page_no=?',
                (scan_id, scan['round_no'], scan['page_no'])).fetchone()
            if existing is not None:
                if existing['digest'] != digest:
                    raise ValueError('同页重放内容不一致')
                return 'replay'
            outcome, error_code, parsed, failure = classify_page(http_status, body)
            cursor = self.connection.execute(
                'insert into pages(scan_id, round_no, page_no, url, http_status, content_type, digest, '
                'response_date, outcome, error_code) values(?,?,?,?,?,?,?,?,?,?)',
                (scan_id, scan['round_no'], scan['page_no'], url, http_status, content_type, digest,
                 None if parsed is None else parsed['response_date'], outcome, error_code))
            page_id = cursor.lastrowid
            if outcome == 'records':
                for entry in parsed['records']:
                    self._apply_record(scan, page_id, entry)
                if parsed['token']:
                    self._move(scan_id, token=parsed['token'], round_no=scan['round_no'],
                               page_no=scan['page_no'] + 1)
                else:
                    self._finish(scan)
            elif outcome == 'empty':
                self._finish(scan)
            elif outcome == 'token_expired':
                round_no = scan['round_no'] + 1
                if round_no > max_rounds:
                    self._move(scan_id, token=scan['token'], round_no=scan['round_no'],
                               page_no=scan['page_no'] + 1, status='failed',
                               failure='resumptionToken 反复过期')
                else:
                    self._move(scan_id, token=None, round_no=round_no, page_no=0)
            else:
                self._move(scan_id, token=scan['token'], round_no=scan['round_no'],
                           page_no=scan['page_no'] + 1, status='failed', failure=failure)
            return outcome

    def _move(self, scan_id, *, token, round_no, page_no, status='paging', failure=None):
        self.connection.execute(
            'update scans set token=?, round_no=?, page_no=?, status=?, failure=?, '
            'updated_at=current_timestamp where id=?',
            (token, round_no, page_no, status, failure, scan_id))

    def _apply_record(self, scan, page_id, entry):
        row = self.connection.execute('select id from records where source=? and identifier=?',
                                      (scan['source'], entry['identifier'])).fetchone()
        if row is None:
            cursor = self.connection.execute('insert into records(source, identifier) values(?,?)',
                                             (scan['source'], entry['identifier']))
            record_id = cursor.lastrowid
        else:
            record_id = row['id']
        self.connection.execute(
            'insert or ignore into observations(record_id, page_id, datestamp, deleted) values(?,?,?,?)',
            (record_id, page_id, entry['datestamp'], 1 if entry['deleted'] else 0))
        if entry['deleted']:
            return
        for set_spec in entry['sets']:
            row = self.connection.execute('select id, status from memberships where record_id=? and set_spec=?',
                                          (record_id, set_spec)).fetchone()
            if row is None:
                cursor = self.connection.execute(
                    "insert into memberships(record_id, set_spec, status) values(?,?,'present')",
                    (record_id, set_spec))
                membership_id = cursor.lastrowid
            else:
                membership_id = row['id']
                if row['status'] != 'present':
                    self.connection.execute("update memberships set status='present' where id=?",
                                            (membership_id,))
            self.connection.execute(
                "insert or ignore into membership_events(membership_id, scan_id, page_id, kind, datestamp) "
                "values(?,?,?,'affirmed',?)",
                (membership_id, scan['id'], page_id, entry['datestamp']))
        if entry['metadata'] is not None:
            body = entry['metadata']
            digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
            self.connection.execute(
                'insert or ignore into representations(record_id, metadata_prefix, datestamp, digest, body, '
                'first_page_id) values(?,?,?,?,?,?)',
                (record_id, scan['metadata_prefix'], entry['datestamp'], digest, body, page_id))

    def _finish(self, scan):
        if scan['kind'] == 'full' and scan['set_spec'] is not None:
            seen = {row['record_id'] for row in self.connection.execute(
                'select distinct o.record_id from observations o join pages p on o.page_id = p.id '
                'where p.scan_id=?', (scan['id'],))}
            current = self.connection.execute(
                "select m.id, m.record_id from memberships m join records r on m.record_id = r.id "
                "where r.source=? and m.set_spec=? and m.status='present'",
                (scan['source'], scan['set_spec'])).fetchall()
            for row in current:
                if row['record_id'] not in seen:
                    self.connection.execute("update memberships set status='revoked' where id=?", (row['id'],))
                    self.connection.execute(
                        "insert or ignore into membership_events(membership_id, scan_id, page_id, kind, datestamp) "
                        "values(?,?,null,'revoked',null)", (row['id'], scan['id']))
        self._move(scan['id'], token=None, round_no=scan['round_no'], page_no=scan['page_no'], status='complete')

    def scan_summary(self, scan_id):
        scan = self.get_scan(scan_id)
        pages = self.connection.execute('select count(*) from pages where scan_id=?', (scan_id,)).fetchone()[0]
        return {'scan_id': scan['id'], 'source': scan['source'], 'metadata_prefix': scan['metadata_prefix'],
                'set': scan['set_spec'], 'from': scan['window_from'], 'until': scan['window_until'],
                'kind': scan['kind'], 'status': scan['status'], 'round': scan['round_no'],
                'pages': pages, 'failure': scan['failure']}

    def current_records(self, source=None, identifier=None, set_spec=None, include_withdrawn=False):
        sql = ('select r.id, r.source, r.identifier,'
               ' (select max(o.datestamp) from observations o where o.record_id=r.id and o.deleted=0) as max_present,'
               ' (select max(o.datestamp) from observations o where o.record_id=r.id and o.deleted=1) as max_deleted'
               ' from records r')
        conditions, params = [], []
        if source is not None:
            conditions.append('r.source=?')
            params.append(normalize_source(source))
        if identifier is not None:
            conditions.append('r.identifier=?')
            params.append(identifier)
        if conditions:
            sql += ' where ' + ' and '.join(conditions)
        sql += ' order by r.source, r.identifier'
        records = []
        for row in self.connection.execute(sql, params):
            status = record_status(row['max_present'], row['max_deleted'])
            if status == 'deleted' and not include_withdrawn:
                continue
            present, revoked = [], []
            for membership in self.connection.execute(
                    'select set_spec, status from memberships where record_id=? order by set_spec', (row['id'],)):
                (present if membership['status'] == 'present' else revoked).append(membership['set_spec'])
            if set_spec is not None and set_spec not in present:
                continue
            entry = {'source': row['source'], 'identifier': row['identifier'], 'status': status,
                     'sets': present, 'representations': self._representation_summary(row['id'])}
            if include_withdrawn:
                entry['revoked_sets'] = revoked
                entry['present_datestamp'] = row['max_present']
                entry['deleted_datestamp'] = row['max_deleted']
            records.append(entry)
        return records

    def _representation_summary(self, record_id):
        rows = self.connection.execute(
            'select metadata_prefix, datestamp, digest from representations where record_id=?',
            (record_id,)).fetchall()
        grouped = {}
        for row in rows:
            grouped.setdefault(row['metadata_prefix'], []).append(row)
        summary = {}
        for prefix, group in grouped.items():
            latest = max(row['datestamp'] for row in group)
            variants = {row['digest'] for row in group if row['datestamp'] == latest}
            summary[prefix] = {'datestamp': latest, 'variants': len(variants), 'conflict': len(variants) > 1}
        return summary

    def explain_record(self, source, identifier):
        source = normalize_source(source)
        row = self.connection.execute('select id from records where source=? and identifier=?',
                                      (source, identifier)).fetchone()
        if row is None:
            return {'source': source, 'identifier': identifier, 'status': 'unknown'}
        record_id = row['id']
        dates = self.connection.execute(
            'select max(case when deleted=0 then datestamp end) as max_present,'
            ' max(case when deleted=1 then datestamp end) as max_deleted'
            ' from observations where record_id=?', (record_id,)).fetchone()
        memberships = []
        for membership in self.connection.execute(
                'select id, set_spec, status from memberships where record_id=? order by set_spec', (record_id,)):
            events = [dict(event) for event in self.connection.execute(
                'select e.kind, e.scan_id, e.page_id, e.datestamp, p.url'
                ' from membership_events e left join pages p on e.page_id = p.id'
                ' where e.membership_id=? order by e.id', (membership['id'],))]
            memberships.append({'set': membership['set_spec'], 'status': membership['status'], 'events': events})
        observations = [{'page_id': row['page_id'], 'scan_id': row['scan_id'], 'datestamp': row['datestamp'],
                         'deleted': bool(row['deleted'])}
                        for row in self.connection.execute(
                            'select o.page_id, p.scan_id, o.datestamp, o.deleted from observations o'
                            ' join pages p on o.page_id = p.id where o.record_id=? order by o.id', (record_id,))]
        rows = self.connection.execute(
            'select metadata_prefix, datestamp, digest, body, first_page_id from representations'
            ' where record_id=? order by metadata_prefix, datestamp, digest', (record_id,)).fetchall()
        latest = {}
        for row in rows:
            latest[row['metadata_prefix']] = max(row['datestamp'], latest.get(row['metadata_prefix'], ''))
        representations = []
        for row in rows:
            same = [other for other in rows if other['metadata_prefix'] == row['metadata_prefix']
                    and other['datestamp'] == row['datestamp']]
            representations.append({
                'prefix': row['metadata_prefix'], 'datestamp': row['datestamp'], 'digest': row['digest'],
                'body': row['body'], 'current': row['datestamp'] == latest[row['metadata_prefix']],
                'conflict': len({other['digest'] for other in same}) > 1,
                'first_page_id': row['first_page_id']})
        return {'source': source, 'identifier': identifier,
                'status': record_status(dates['max_present'], dates['max_deleted']),
                'present_datestamp': dates['max_present'], 'deleted_datestamp': dates['max_deleted'],
                'memberships': memberships, 'observations': observations, 'representations': representations}


def run_harvest(store, source, metadata_prefix, set_spec=None, window_from=None, window_until=None,
                max_pages=0, max_rounds=4, fetcher=fetch):
    scan_id = store.begin_scan(source, metadata_prefix, set_spec, window_from, window_until)
    fetched = 0
    while True:
        scan = store.get_scan(scan_id)
        if scan['status'] == 'complete':
            break
        if max_pages and fetched >= max_pages:
            break
        url = request_url(scan['source'], scan['metadata_prefix'], scan['set_spec'],
                          scan['window_from'], scan['window_until'], scan['token'])
        try:
            http_status, content_type, body = fetcher(url)
        except Exception as error:
            store.mark_failed(scan_id, '请求失败: ' + repr(error))
            break
        store.commit_page(scan_id, url, http_status, content_type, body, max_rounds=max_rounds)
        fetched += 1
        if store.get_scan(scan_id)['status'] == 'failed':
            break
    return store.scan_summary(scan_id)
