#!/usr/bin/env python
# encoding: utf-8
"""
downloader.py

Created by Thomas Mangin on 2011-12-01.
Copyright (c) 2011 Exa Networks. All rights reserved.
"""

from exaproxy.util.logger import logger
from exaproxy.network.functions import connect
from exaproxy.network.poller import errno_block
from exaproxy.http.response import http

import os
import socket
import errno

# http://tools.ietf.org/html/rfc2616#section-8.2.3
# Says we SHOULD keep track of the server version and deal with 100-continue
# I say I am too lazy - and if you want the feature use this software as as rev-proxy :D

DEFAULT_READ_BUFFER_SIZE = 4096

class Downloader (object):
	_connect = staticmethod(connect)

	def __init__(self):
		self.connections = {}
		self.connecting = {}
		self.byclientid = {}
		self.buffered = []


	def _read(self, sock, default_buffer_size=DEFAULT_READ_BUFFER_SIZE):
		"""Coroutine that reads data from our connection to the remote server"""

		bufsize = yield '' # start the coroutine

		while True:     # Enter the exception handler as infrequently as possible
			try:
				# If the end user connection dies before we finished downloading from it
				# then we send None to signal this coroutine to give up
				while bufsize is not None:
					r_buffer = sock.recv(bufsize or default_buffer_size)
					if not r_buffer:
						break

					bufsize = yield r_buffer

				break # exit the outer loop

			except socket.error, e:
				if e.errno in errno_block:
					logger.error('download','write failed as it would have blocked. Why were we woken up?')
					logger.error('download','Error %d: %s' % (e.errno, errno.errorcode.get(e.errno, '')))
					yield ''
				else:
					logger.error('download', 'bad downloaded ? %s %s' % (type(e),str(e)))
					break # stop downloading

		# XXX: should we indicate whether we downloaded the entire file
		# XXX: or encountered an error

		r_buffer = None
		# signal that there is nothing more to download
		yield None
				

	def _write(self, sock):
		"""Coroutine that manages data to be sent to the remote server"""

		# XXX:
		# TODO: use a file for buffering data rather than storing
		#       it in memory

		data = yield None # start the coroutine
		logger.info('download', 'writer started with %s bytes %s' % (len(data) if data is not None else None, sock))
		w_buffer = ''

		while True: # enter the exception handler as infrequently as possible
			try:
				while True:
					had_buffer = True if w_buffer else False

					if data is not None:
						 w_buffer = (w_buffer + data) if data else w_buffer
					else:
						if had_buffer: # we'll be back
							yield None
						break

					if not had_buffer or not data:
						sent = sock.send(w_buffer)
						logger.info('download', 'sent %s of %s bytes of data : %s' % (sent, len(data), sock))
						w_buffer = w_buffer[sent:]

					data = yield (True if w_buffer else False), had_buffer

				break	# break out of the outer loop as soon as we leave the inner loop
					# through normal execution

			except socket.error, e:
				if e.errno in errno_block:
					logger.error('download', 'Write failed as it would have blocked. Why were we woken up? Error %d: %s' % (e.errno, errno.errorcode.get(e.errno, '')))
					data = yield (True if w_buffer else False), had_buffer
				else:
					break

		yield None # close the connection

	def newConnection(self, client_id, host, port, request):
		sock = self._connect(host, port)

		logger.info('download', 'new download socket for client %s %s' % (client_id, sock))

		# sock will be None if there was a temporary error
		if sock is not None:
			self.connecting[sock] = client_id, request

		return True if sock is not None else None

	def start(self, sock):
		# the socket is now open
		res = self.connecting.pop(sock, None)
		if res is not None:
			client_id, request = res
			logger.info('download', 'download socket is not open for client %s %s' % (client_id, sock))
			logger.info('download', 'going to send %s bytes, request for client %s %s' % (len(request or ''), client_id, sock))
			fetcher = self._read(sock)
			fetcher.next()       # start the fetcher coroutine

			sender = self._write(sock)
			sender.next()        # start the sender coroutine

			# XXX: We MUST send method to newConnection rather than checking for a null request
			if request is not None:
				sender.send(request) # immediately send the request

			self.connections[sock] = fetcher, sender, client_id
			self.byclientid[client_id] = fetcher, sender, sock
			
			# XXX: We MUST send method to newConnection rather than checking for a null request
			response='HTTP/1.1 200 Connection Established\r\n\r\n' if request is None else ''
			result = client_id, response
		else:
			result = None, None

		return result


	def sendClientData(self, client_id, data):
		fetcher, sender, sock = self.byclientid.get(client_id, (None, None, None))
		if sock is None:
			logger.error('download', 'Fatal? Received data from a client we do not recognise: %s' % client_id)
			return None

		logger.info('download', 'going to send %s bytes of data for client %s %s' % (len(data) if data is not None else None, client_id, sock))
		res = sender.send(data)

		if res is None:
			if sock not in self.buffered:
				self._terminate(sock)
			else:
				logger.warning('download', 'socket was closed before we could empty its buffer %s' % sock)
			return None

		buffered, had_buffer = res

		if buffered:
			if sock not in self.buffered:
				self.buffered.append(sock)
		elif had_buffer and sock in self.buffered:
			self.buffered.remove(sock)

		return True

	def sendSocketData(self, sock, data):
		fetcher, sender, client_id = self.connections.get(sock, (None, None, None))
		if client_id is None:
			logger.critical('download', 'Sending data on a socket we do not recognise: %s' % sock)
			logger.critical('download', '#connections %s, socket in connections ? %s' % (len(self.connections), sock in self.connections))
			return None

		logger.info('flushing data with %s bytes for client %s %s' % (len(data) if data is not None else None, client_id, sock))

		res = sender.send(data)

		if res is None:
			if sock in self.buffered:
				self.buffered.remove(sock)
			logger.error('download','could not send data to socket')
			self._terminate(sock) # XXX: should return None - check that 'fixing' _terminate doesn't break anything
			return None

		buffered, had_buffer = res

		if buffered:
			if sock not in self.buffered:
				self.buffered.append(sock)
		elif had_buffer and sock in self.buffered:
			self.buffered.remove(sock)

		return True


	def _terminate(self, sock):
		try:
			sock.shutdown(socket.SHUT_RDWR)
			sock.close()
		except socket.error:
			pass

		fetcher, sender, client_id = self.connections.pop(sock, None)
		logger.info('download','closing download socket used by client %s %s' % (client_id, sock))
		# XXX: log something if we did not have the client_id in self.byclientid
		if client_id is not None:
			self.byclientid.pop(client_id, None)

		if sock in self.buffered:
			self.buffered.remove(sock)

		return fetcher is not None

	# XXX: track the total number of bytes read in the content
	# XXX: (not including headers)
	def readData(self, sock, bufsize=0):
		fetcher, sender, client_id = self.connections.get(sock, (None, None, None))
		if client_id is None:
			logger.error('download', 'Fatal? Trying to read data on a socket we do not recognise: %s' % sock)

		if fetcher is not None:
			data = fetcher.send(bufsize)
		else:
			logger.error('download','no fetcher for %s' % sock)
			data = None

		logger.info('download','downloaded %s bytes of data for client %s %s' % (len(data) if data is not None else None, client_id, sock))

		if fetcher and data is None:
			self._terminate(sock)
		elif data is None:
			logger.info('download','not terminating because there is no fetcher')

		return client_id, data

	def endClientDownload(self, client_id):
		fetcher, sender, sock = self.byclientid.get(client_id, (None, None, None))
		logger.info('download','ending download for client %s %s' % (client_id, sock))
		if fetcher is not None:
			res = fetcher.send(None)
			response = res is None

			# XXX: written in a hurry - check this is right
			self._terminate(sock)
		else:
			response = None

		return response

	def cleanup(self, sock):
		res = self.connecting.pop(sock, None)
		return res is not None