import xml.etree.ElementTree as ET

NAMESPACE = 'http://www.openarchives.org/OAI/2.0/'
PREFIX = '{' + NAMESPACE + '}'


def parse_page(body):
    root = ET.fromstring(body)
    if root.tag != PREFIX + 'OAI-PMH':
        raise ValueError('不是 OAI-PMH 2.0 响应')
    errors = [{'code': item.get('code'), 'text': item.text or ''} for item in root.findall(PREFIX + 'error')]
    records = []
    for record in root.findall('.//' + PREFIX + 'record'):
        records.append(_entry(record.find(PREFIX + 'header'), record.find(PREFIX + 'metadata')))
    if not records:
        for header in root.findall('.//' + PREFIX + 'header'):
            records.append(_entry(header, None))
    token = root.find('.//' + PREFIX + 'resumptionToken')
    return {'records': records, 'errors': errors, 'token': None if token is None else token.text or '',
            'response_date': root.findtext(PREFIX + 'responseDate')}


def _entry(header, metadata):
    if header is None:
        raise ValueError('record 缺少 header')
    identifier = header.findtext(PREFIX + 'identifier')
    datestamp = header.findtext(PREFIX + 'datestamp')
    if not identifier or not datestamp:
        raise ValueError('header 缺少标识或日期')
    return {'identifier': identifier, 'datestamp': datestamp,
            'deleted': header.get('status') == 'deleted',
            'sets': [entry.text for entry in header.findall(PREFIX + 'setSpec')],
            'metadata': None if metadata is None else ET.tostring(metadata, encoding='unicode')}


def inspect_document(body):
    page = parse_page(body)
    for entry in page['records']:
        del entry['metadata']
    return page
