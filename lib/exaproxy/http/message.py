# encoding: utf-8
"""
parser.py

Created by Thomas Mangin on 2012-02-27.
Copyright (c) 2011-2013 Exa Networks. All rights reserved.
"""

import traceback

from exaproxy.util.log.logger import Logger
from exaproxy.network.functions import isip

from .request import Request
from .headers import Headers,ExpectationFailed,InvalidRequest

class HostMismatch(Exception):
	pass

class HTTP (object):
	http_versions = ('1.0', '1.1')

	def __init__(self,configuration,headers,remote_ip):
		self.raw = headers
		self.client = remote_ip
		self.proxy_name = "X-Proxy-Version: ExaProxy version %s" % configuration.proxy.version
		self.forward = configuration.http.forward
		self.log = Logger('header', configuration.log.header)
		self.response = 0

	def parse (self,transparent):
		self.log.debug('parsing header [[%s]]' % str(self.raw).replace('\r','\\r').replace('\n','\\n\n'))

		try:
			first, remaining = self.raw.split('\n',1)
			if '\r' in self.raw:
				self.separator = '\r\n'
			else:
				self.separator = '\n'

			self.request = Request(first.rstrip('\r')).parse()
			self.headers = Headers(self.request.version,self.separator).parse(transparent,remaining)

			self.headerhost = self.extractHost()

			if self.request.host and self.request.host != '*':
				self.host = self.request.host
			else:
				# can raise KeyError, but host is a required header
				self.host = self.headerhost
			self.port = self.request.port

			if not self.host:
				raise InvalidRequest('Can not forward the rquest, we have not host information')

			# That does not let us use ICAP and connect to redirect things :p
			#if self.headerhost:
			#	if self.host != self.headerhost and self.request.method != 'OPTIONS' and self.host != '*':
			#		raise HostMismatch, 'Make up your mind: %s - %s' % (self.host, self.headerhost)

			# Is this the best place to add headers?
			self.headers.replace('x-proxy-version',self.proxy_name)

			if self.forward:
				client = self.headers.get(self.forward, ':%s' % self.client)[0].split(':', 1)[1].split(',')[-1].strip()
				if isip(client):
					self.client = client
				#else:
				#	self.log.info('Invalid address in Client identifier header: %s' % client)

			encoding = self.headers.get('transfer-encoding', [':'])[0].split(':', 1)[1].strip()
			content_length = int(self.headers.get('content-length', [':0'])[0].split(':',1)[1].strip())

			self.content_length = encoding if encoding == 'chunked' else content_length
			self.url = self.host + ((':%s' % self.port) if self.port != '80' else '') + self.request.path
			self.url_noport = self.host + self.request.path

			connection = self.headers.get('connection', [':'])[0].split(':',1)[1].strip()

			if connection.lower() == 'upgrade':
				upgrade = self.headers.get('upgrade', [':'])[0].split(':',1)[1].strip()
			else:
				upgrade = ''

			self.upgrade = upgrade

		except KeyboardInterrupt:
			raise
		except ExpectationFailed,e:
			self.response = 417
			return self
		except InvalidRequest,e:
			self.log.warning('invalid request received, %s' % str(e))
			self.response = 400
			return self
		except Exception, e:
			self.log.error('could not parse header %s %s' % (type(e),str(e)))
			for line in traceback.format_exc().split('\n'):
				self.log.warning(line)
			return None
		return self

	def redirect (self, host, path):
		self.host = host if host is not None else self.host
		self.path = path if path is not None else self.path

		if path is not None:
			self.request = Request(self.request.method + ' ' + path + ' HTTP/' + self.request.version).parse()

		if host is not None:
			if host.count(':') > 1:
				host = '['+host+']'

			if self.port != 80:
				host = host + ':' + str(self.port)

			self.header.replace('host','Host: ' + host)


	def extractHost(self):
		hoststring = self.headers.get('host',[':'])[0].split(':',1)[1].strip()

		if ':' in hoststring:
			# check to see if we have an IPv6 address
			if hoststring.startswith('[') and ']' in hoststring:
				host = hoststring[1:].split(']', 1)[0]
			else:
				host = hoststring.split(':', 1)[0]
		else:
			host = hoststring
		return host


	def __str__ (self):
		if self.request.version in self.http_versions:
			res = str(self.request) + self.separator + str(self.headers) + self.separator + self.separator
		else:
			res = self.raw

		return res


#if __name__ == '__main__':
#	class Conf (object):
#		class Proxy (object):
#			name = 'proxy'
#			version = '1'
#		proxy = Proxy()
#		class Http (object):
#			x_forwarded_for = True
#		http = Http()
#	conf = Conf()
#
#	r = """\
#GET http://thomas.mangin.com/ HTTP/1.1
#Host: thomas.mangin.com
#User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10.7; rv:9.0.1) Gecko/20100101 Firefox/9.0.1
#Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
#Accept-Language: en-us,en;q=0.5
#Accept-Encoding: gzip, deflate
#Accept-Charset: ISO-8859-1,utf-8;q=0.7,*;q=0.7
#Proxy-Connection: keep-alive
#Cookie: txtMainTab=Timeline
#
#"""
#	h = Header(conf,r,'127.0.0.1')
#	if h.parse():
#		print "[%s]" % h
#	else:
#		print 'parsing failed'
