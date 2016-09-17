# -*- coding: utf-8 -*-
#
# imap.py
#
# Copyright 2011 - 2016 Patrick Ulbrich <zulu99@gmx.net>
# Copyright 2016 Timo Kankare <timo.kankare@iki.fi>
# Copyright 2016 Thomas Haider <t.haider@deprecate.de>
# Copyright 2011 Ralf Hersel <ralf.hersel@gmx.net>#
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA 02110-1301, USA.
#

"""Implementation for IMAP mailbox connection."""

import email
import logging
import re

from Mailnag.backends.base import MailboxBackend
import Mailnag.common.imaplib2 as imaplib
from Mailnag.common.imaplib2 import AUTH
from Mailnag.common.exceptions import InvalidOperationException


class IMAPMailboxBackend(MailboxBackend):
	"""Implementation of IMAP mail boxes."""

	def __init__(self, name = '', user = '', password = '', oauth2string = '',
				 server = '', port = '', ssl = True, folders = []):
		self.name = name
		self.user = user
		self.password = password
		self.oauth2string = oauth2string
		self.server = server
		self.port = port
		self.ssl = ssl # bool
		self.folders = folders
		self._conn = None


	def open(self):
		if self._conn != None:
			raise InvalidOperationException("Account is aready open")
		
		conn = None
		
		try:
			if self.ssl:
				if self.port == '':
					conn = imaplib.IMAP4_SSL(self.server)
				else:
					conn = imaplib.IMAP4_SSL(self.server, int(self.port))
			else:
				if self.port == '':
					conn = imaplib.IMAP4(self.server)
				else:
					conn = imaplib.IMAP4(self.server, int(self.port))
				
				if 'STARTTLS' in conn.capabilities:
					conn.starttls()
				else:
					logging.warning("Using unencrypted connection for account '%s'" % self.name)
				
			if self.oauth2string != '':
				conn.authenticate('XOAUTH2', lambda x: self.oauth2string)
			else:
				conn.login(self.user, self.password)
			
			self._conn = conn
		except:
			try:
				if conn != None:
					# conn.close() # allowed in SELECTED state only
					conn.logout()
			except:	pass
			raise # re-throw exception
		
		# notify_next_change() (IMAP IDLE) requires a selected folder
		if self._conn.state == AUTH:
			self._select()


	def close(self):
		# if conn has already been closed, don't try to close it again
		if self._conn != None:
			self._conn.close()
			self._conn.logout()
			self._conn = None


	def is_open(self):
		return (self._conn != None) and \
				(self._conn.state != imaplib.LOGOUT) and \
				(not self._conn.Terminate)


	def list_messages(self):
		conn = self._conn
		if len(self.folders) == 0:
			folder_list = [ 'INBOX' ]
		else:
			folder_list = self.folders

		for folder in folder_list:
			# select IMAP folder
			conn.select(folder, readonly = True)
			try:
				status, data = conn.search(None, 'UNSEEN') # ALL or UNSEEN
			except:
				logging.warning('Folder %s does not exist.', folder)
				continue

			if status != 'OK' or None in [d for d in data]:
				logging.debug('Folder %s in status %s | Data: %s', (folder, status, data))
				continue # Bugfix LP-735071
			for num in data[0].split():
				typ, msg_data = conn.fetch(num, '(BODY.PEEK[HEADER])') # header only (without setting READ flag)
				for response_part in msg_data:
					if isinstance(response_part, tuple):
						try:
							msg = email.message_from_string(response_part[1])
						except:
							logging.debug("Couldn't get IMAP message.")
							continue
						yield (folder, msg)


	def request_folders(self):
		lst = []
		
		# Always create a new connection as an existing one may
		# be used for IMAP IDLE.
		self.open()

		try:
			status, data = self._conn.list('', '*')
		finally:
			# self._conn.close() # allowed in SELECTED state only
			self._conn.logout()
		
		for d in data:
			match = re.match('.+\s+("."|"?NIL"?)\s+"?([^"]+)"?$', d)

			if match == None:
				logging.warning("Folder format not supported.")
			else:
				folder = match.group(2)
				lst.append(folder)
		
		return lst


	def _select(self):
		if len(self.folders) == 1:
			self._conn.select(self.folders[0])
		else:
			self._conn.select("INBOX")


	def notify_next_change(self, callback=None, timeout=None):
		# register idle callback that is called whenever an idle event
		# arrives (new mail / mail deleted).
		# the callback is called after <idle_timeout> minutes at the latest.
		# gmail sends keepalive events every 5 minutes.

		# idle callback (runs on a further thread)
		def _idle_callback(args):
			# check if the connection has been reset by provider
			if (args[2] != None) and (args[2][0] is self._conn.abort):
				# conn has already been closed, don't try to close it again
				# self._conn.close() # (calls idle_callback)
				
				# shutdown existing callback thread
				self._conn.logout()
				self._conn = None
			
			# call actual callback
			callback(args)
		
		self._conn.idle(callback = _idle_callback, timeout = timeout)


	def cancel_notifications(self):
		try:
			if self._conn != None:
				# Exit possible active idle state.
				# (also calls idle_callback)
				self._conn.noop()
		except:
			pass

