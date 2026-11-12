import datetime
import urllib.parse
import xml.etree.ElementTree as ET

PREFIX = '{http://www.openarchives.org/OAI/2.0/}'


def error(code):
    return 200, 'text/xml', ('<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="' + code + '">Request rejected</error></OAI-PMH>').encode()


def handle(server, path, headers):
    parsed = urllib.parse.urlsplit(path)
    if parsed.path not in {'/oai', '/partner/oai'}:
        return None
    query = urllib.parse.parse_qs(parsed.query)
    verb = query.get('verb', [''])[0]
    partner = parsed.path == '/partner/oai'
    granularity = '%Y-%m-%dT%H:%M:%SZ' if partner else '%Y-%m-%d'
    if verb == 'Identify':
        root = ET.fromstring((server.fixtures / 'identify.xml').read_bytes())
        identify = root.find(PREFIX + 'Identify')
        if partner:
            identify.find(PREFIX + 'granularity').text = 'YYYY-MM-DDThh:mm:ssZ'
        ET.SubElement(identify, PREFIX + 'earliestDatestamp').text = '2026-11-01T00:00:00Z' if partner else '2026-11-01'
        return 200, 'text/xml', ET.tostring(root)
    if verb not in {'ListRecords', 'ListIdentifiers'}:
        return error('badVerb')
    token = query.get('resumptionToken', [''])[0]
    if token and set(query) != {'verb', 'resumptionToken'}:
        return error('badArgument')
    if token and token != 'next+1':
        return error('badResumptionToken')
    if not token:
        for field in ['from', 'until']:
            if field in query:
                try:
                    datetime.datetime.strptime(query[field][0], granularity)
                except ValueError:
                    return error('badArgument')
        if query.get('from', [''])[0] > query.get('until', ['9999'])[0]:
            return error('badArgument')
    if partner:
        name = 'partner.xml'
    elif token:
        name = 'expired.xml' if server.phase == 'expired' else ('withdraw-last.xml' if server.phase == 'withdraw' else 'last.xml')
    elif server.phase == 'deleted':
        name = 'deleted.xml'
    elif server.phase == 'withdraw' and query.get('set') == ['bridge']:
        name = 'empty.xml'
    elif server.phase == 'withdraw':
        name = 'withdraw.xml'
    elif server.phase == 'conflict':
        name = 'conflict.xml'
    else:
        name = 'raw.xml'
    root = ET.fromstring((server.fixtures / name).read_bytes())
    records = root.find(PREFIX + 'ListRecords')
    if records is not None:
        if partner:
            continuation = records.find(PREFIX + 'resumptionToken')
            if continuation is not None:
                records.remove(continuation)
        if not token:
            for record in list(records.findall(PREFIX + 'record')):
                date = record.find(PREFIX + 'header').findtext(PREFIX + 'datestamp')
                if date < query.get('from', [''])[0] or date > query.get('until', ['9999'])[0]:
                    records.remove(record)
        if not records.findall(PREFIX + 'record'):
            return error('noRecordsMatch')
        if verb == 'ListIdentifiers':
            records.tag = PREFIX + 'ListIdentifiers'
            for record in list(records.findall(PREFIX + 'record')):
                header = record.find(PREFIX + 'header')
                records.remove(record)
                records.insert(0, header)
    return 200, 'text/xml', ET.tostring(root)
