from tornado import (ioloop, web, httpclient, httputil, iostream)
import hashlib
import random
import string
import pexpect
import re
import os
import logging
import urllib
from pprint import pprint

db = {
    'student-port': {},
    'student-proc': {}
}

logger = logging.getLogger('tornado_proxy')

def parse_proxy(proxy):
    proxy_parsed = urlparse(proxy, scheme='http')
    return proxy_parsed.hostname, proxy_parsed.port


def fetch_request(uri, port, callback, **kwargs):
    if uri.find('/api/kernels/') != -1:
        protocol = 'ws'
    else:
        protocol = 'http'
    print '[protocol]', protocol
    req = httpclient.HTTPRequest('%s://localhost:%s%s' % (protocol, port, uri), **kwargs)
    client = httpclient.AsyncHTTPClient()
    client.fetch(req, callback, raise_error=False)


class LoginHandler(web.RequestHandler):
    def get(self, student):
        # this is a new student.
        # hence create a new notebook session.
        if student not in db['student-port']:
            child = pexpect.spawn('jupyter notebook --no-browser')
            child.expect('running at: http://localhost:(.*)/')
            print '[notebook]', child.after
            port = re.findall(r'http://localhost:(.*)/', child.after)[0]
            student_port = db['student-port']
            student_port[student] = port
            db['student-port'][student] = port
            db['student-proc'][student] = child
            #student_url = urllib.urlencode(student)
            #os.system('mkdir -p worksheet/%s' % )
            #os.system('cp -r worksheet/%s' % urllib.urlencode(student))
        self.set_cookie('port', db['student-port'][student])
        self.redirect('/tree')


class NotebookHandler(web.RequestHandler):
    SUPPORTED_METHODS = ['GET', 'POST', 'CONNECT']

    def compute_etag(self):
        return None # disable tornado Etag

    @web.asynchronous
    def get(self):
        port = self.get_cookie('port')
        if not port:
            self.set_status(500)
            self.write('error: please login first.')
            self.finish()
            return


        logger.debug('Handle %s request to %s', self.request.method,
                     self.request.uri)

        def handle_response(response):
            if (response.error and not
                    isinstance(response.error, httpclient.HTTPError)):
                self.set_status(500)
                self.write('Internal server error:\n' + str(response.error))
            else:
                self.set_status(response.code, response.reason)
                self._headers = httputil.HTTPHeaders() # clear tornado default header

                for header, v in response.headers.get_all():
                    if header not in ('Content-Length', 'Transfer-Encoding', 'Content-Encoding', 'Connection'):
                        self.add_header(header, v) # some header appear multiple times, eg 'Set-Cookie'

                if response.body:
                    self.set_header('Content-Length', len(response.body))
                    self.write(response.body)
            self.finish()

        body = self.request.body
        if not body:
            body = None
        try:
            if 'Proxy-Connection' in self.request.headers:
                del self.request.headers['Proxy-Connection']
            self.request.headers['Connection'] = 'Upgrade'
            fetch_request(
                self.request.uri, port, handle_response,
                method=self.request.method, body=body,
                headers=self.request.headers, follow_redirects=False,
                allow_nonstandard_methods=True)
        except httpclient.HTTPError as e:
            if hasattr(e, 'response') and e.response:
                handle_response(e.response)
            else:
                self.set_status(500)
                self.write('Internal server error:\n' + str(e))
                self.finish()


    @web.asynchronous
    def post(self):
        return self.get()


    @web.asynchronous
    def connect(self):
        logger.debug('Start CONNECT to %s', self.request.uri)
        host, port = self.request.uri.split(':')
        client = self.request.connection.stream

        def read_from_client(data):
            upstream.write(data)

        def read_from_upstream(data):
            client.write(data)

        def client_close(data=None):
            if upstream.closed():
                return
            if data:
                upstream.write(data)
            upstream.close()

        def upstream_close(data=None):
            if client.closed():
                return
            if data:
                client.write(data)
            client.close()

        def start_tunnel():
            logger.debug('CONNECT tunnel established to %s', self.request.uri)
            client.read_until_close(client_close, read_from_client)
            upstream.read_until_close(upstream_close, read_from_upstream)
            client.write(b'HTTP/1.0 200 Connection established\r\n\r\n')

        def on_proxy_response(data=None):
            if data:
                first_line = data.splitlines()[0]
                http_v, status, text = first_line.split(None, 2)
                if int(status) == 200:
                    logger.debug('Connected to upstream proxy %s', proxy)
                    start_tunnel()
                    return

            self.set_status(500)
            self.finish()

        def start_proxy_tunnel():
            upstream.write('CONNECT %s HTTP/1.1\r\n' % self.request.uri)
            upstream.write('Host: %s\r\n' % self.request.uri)
            upstream.write('Proxy-Connection: Keep-Alive\r\n\r\n')
            upstream.read_until('\r\n\r\n', on_proxy_response)

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
        upstream = iostream.IOStream(s)

        proxy = get_proxy(self.request.uri)
        if proxy:
            proxy_host, proxy_port = parse_proxy(proxy)
            upstream.connect((proxy_host, proxy_port), start_proxy_tunnel)
        else:
            upstream.connect((host, int(port)), start_tunnel)



handlers = [
    (r"/nlp/(.*)", LoginHandler),
    (r"/.*", NotebookHandler),
]


settings = {
    "autoreload": True,
    "debug": True,
    "template_path": "server/frontend/template/",
    "cookie_secret": hashlib.sha256(''.join([
        random.choice(string.ascii_uppercase) for i in range(100)
    ])).hexdigest()
}


if __name__ == "__main__":
    application = web.Application(handlers, **settings)
    port = int(os.environ.get("PORT", 5000))
    application.listen(port, address="0.0.0.0")
    ioloop.IOLoop.current().start()


