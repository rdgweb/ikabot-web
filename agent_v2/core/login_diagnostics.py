"""Login diagnostics without credentials, query values, source lines or locals."""
import json
import re
from urllib.parse import urlsplit, urlunsplit


def safe_url(value):
    try:
        parts = urlsplit(str(value))
        host = parts.hostname or ''
        if not host or parts.scheme not in ('http', 'https'):
            return '[invalid-url]'
        if ':' in host:
            host = f'[{host}]'
        if parts.port:
            host += f':{parts.port}'
        return urlunsplit((parts.scheme, host, parts.path, '', ''))
    except ValueError:
        return '[invalid-url]'


def safe_text(value, secrets=()):
    text = str(value)
    text = re.sub(r'https?://[^\s\'"<>]+', lambda m: safe_url(m[0]), text)
    text = re.sub(r'\?[^\s\'"<>]+', '?[redacted]', text)
    text = re.sub(r'(?i)Bearer\s+[^\s\'",;]+', 'Bearer [redacted]', text)
    text = re.sub(r'(?i)((?:password|token|authorization|cookie|secret)\s*[:=]\s*)[^\s,;]+', r'\1[redacted]', text)
    for secret in sorted({str(s) for s in secrets if s}, key=len, reverse=True):
        text = text.replace(secret, '[redacted]')
    return text


def login_diagnostic(exc, session, attempt, total, secrets=()):
    chain, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen and len(chain) < 6:
        seen.add(id(current))
        frames, tb = [], current.__traceback__
        while tb is not None:
            frames.append({'file': tb.tb_frame.f_code.co_filename.replace('\\', '/').rsplit('/', 1)[-1],
                           'function': tb.tb_frame.f_code.co_name, 'line': tb.tb_lineno})
            tb = tb.tb_next
        # Exception messages can contain arbitrary response bodies or secrets.
        chain.append({'type': type(current).__name__, 'frames': frames[-16:]})
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    data = {'stage': getattr(session, 'login_stage', 'login'), 'attempt': attempt,
            'total_attempts': total, 'proxy': f'reserved-{attempt}',
            'http': getattr(session, 'last_http_request', None), 'traceback': chain}

    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return safe_text(value, secrets) if isinstance(value, str) else value

    return json.dumps(clean(data), ensure_ascii=False)
