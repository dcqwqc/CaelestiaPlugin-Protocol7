"""Groq-only preview HTTP transport; no SDK retries or background dispatcher.

DNS/TCP/TLS preparation and response reads run on the existing request worker.
The first nonblocking TLS write attempt runs inside the lifecycle scheduler
(and durable ledger transaction). SSL can emit ciphertext before reporting
readiness, so this attempt is conservatively the irreversible initiation point.
SSL readiness waits never hold either lock. After that attempt, the request
is already in flight and must finish or time out; stop cannot retract it.
"""
import http.client
import json
import select
import ssl
import time
from urllib.parse import urlsplit

from streaming_stt import PreviewUnavailable


class PreviewHTTPError(Exception):
    def __init__(self, status, headers):
        super().__init__('Groq preview HTTP failure')
        self.status_code = status
        self.response = type('Response', (), {'headers': headers})()


def transcribe_preview(client, kwargs, dispatch):
    # Accept the production SDK type, not arbitrary credential-bearing objects.
    from groq import Groq
    import httpx
    if not isinstance(client, Groq):
        raise PreviewUnavailable('unsupported preview client')
    base = urlsplit(str(client.base_url))
    if base.scheme != 'https' or base.hostname != 'api.groq.com' or base.port not in (None, 443) or base.path not in ('', '/'):
        raise PreviewUnavailable('unsupported Groq preview endpoint')
    timeout = float(kwargs['timeout'])
    deadline = time.monotonic() + timeout
    data = {k: str(v) for k, v in kwargs.items() if k not in ('file', 'timeout')}
    data['timestamp_granularities[]'] = ['word', 'segment']
    request = httpx.Request('POST', str(client.base_url).rstrip('/') + '/openai/v1/audio/transcriptions',
                            headers=client.auth_headers, data=data,
                            files={'file': kwargs['file']})
    body = request.read()  # WAV is bounded; encode multipart before dispatch.
    # Explicit Connection: close avoids retained connections and hidden retries.
    headers = dict(request.headers)
    headers['connection'] = 'close'
    wire = (f'POST {request.url.raw_path.decode("ascii")} HTTP/1.1\r\n' +
            ''.join(f'{key}: {value}\r\n' for key, value in headers.items()) + '\r\n').encode('ascii') + body
    connection = http.client.HTTPSConnection(base.hostname, timeout=timeout)
    try:
        connection.connect()  # TLS handshake sends no transcription payload.
        sock = connection.sock
        sock.setblocking(False)
        sent = 0
        initiated = False
        def first_write(**_ignored):
            nonlocal initiated
            # SSLWantWrite may already have emitted ciphertext. Treat the
            # first TLS write attempt as irreversible, even without a count.
            initiated = True
            return sock.send(wire)
        while sent < len(wire):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('preview transport deadline')
            try:
                if not initiated:
                    count = dispatch(first_write)
                else:
                    count = sock.send(wire[sent:])
                if not count:
                    raise ConnectionError('preview transport closed')
                sent += count
            except (ssl.SSLWantWriteError, ssl.SSLWantReadError, BlockingIOError) as exc:
                # The guarded TLS write has started; these are continuations
                # of the same in-flight request, never a new preview.
                read = isinstance(exc, ssl.SSLWantReadError)
                select.select([sock] if read else [], [] if read else [sock], [], remaining)
        sock.settimeout(max(.001, deadline - time.monotonic()))
        # Payload was written directly; HTTPConnection's request state machine
        # was deliberately bypassed. Parse with HTTPResponse on the TLS socket.
        response = http.client.HTTPResponse(sock)
        response.begin()
        try:
            response_body = response.read(1024 * 1024 + 1)
            response_headers = dict(response.getheaders())
        finally:
            response.close()
        if response.status != 200:
            raise PreviewHTTPError(response.status, response_headers)
        if len(response_body) > 1024 * 1024:
            raise ValueError('preview response too large')
        return json.loads(response_body), response_headers
    finally:
        connection.close()
