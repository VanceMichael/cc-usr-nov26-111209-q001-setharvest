import urllib.parse


def handle(server, path, headers):
    parsed = urllib.parse.urlsplit(path)
    if parsed.path not in {'/oai', '/partner/oai'}:
        return None
    query = urllib.parse.parse_qs(parsed.query)
    verb = query.get('verb', [''])[0]
    if parsed.path == '/partner/oai' and verb != 'Identify':
        name = 'partner.xml'
    elif verb == 'Identify':
        name = 'identify.xml'
    elif verb not in {'ListRecords', 'ListIdentifiers'}:
        return 400, 'text/plain', b'unsupported verb'
    elif query.get('resumptionToken'):
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
        name = 'partner.xml' if parsed.path.startswith('/partner') else 'raw.xml'
    return 200, 'text/xml', (server.fixtures / name).read_bytes()
