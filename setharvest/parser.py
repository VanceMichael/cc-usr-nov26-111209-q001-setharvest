import xml.etree.ElementTree as ET

NAMESPACE = 'http://www.openarchives.org/OAI/2.0/'
PREFIX = '{' + NAMESPACE + '}'


def inspect_document(body):
    root = ET.fromstring(body)
    if root.tag != PREFIX + 'OAI-PMH':
        raise ValueError('不是 OAI-PMH 2.0 响应')
    errors = [{'code': item.get('code'), 'text': item.text or ''} for item in root.findall(PREFIX + 'error')]
    records = []
    for item in root.findall('.//' + PREFIX + 'header'):
        identifier = item.findtext(PREFIX + 'identifier')
        datestamp = item.findtext(PREFIX + 'datestamp')
        if not identifier or not datestamp:
            raise ValueError('header 缺少标识或日期')
        records.append({'identifier': identifier, 'datestamp': datestamp,
                        'deleted': item.get('status') == 'deleted',
                        'sets': [entry.text for entry in item.findall(PREFIX + 'setSpec')]})
    token = root.find('.//' + PREFIX + 'resumptionToken')
    return {'records': records, 'errors': errors, 'token': None if token is None else token.text or '',
            'response_date': root.findtext(PREFIX + 'responseDate')}
