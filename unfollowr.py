#!/usr/bin/env python2.6
# -*- coding: utf-8 -*-

# unfollowr bot to calculate unfollows and dm users about them

# Copyright 2009 Ivan Babroŭ (email : ibobrik@gmail.com)

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the license, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; see the file COPYING.  If not, write to
# the Free Software Foundation, Inc., 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.

# WARNING: python-2.6 required because of json module

# Grammar nazi — Va1en0k (c) 2010

# TODO: settinngs in django style (settings.py?)
# TODO: stats!!11
# TODO: check if user blocked another user
# TODO: make Logger class threadsafe to use in listwatcher with different fname

import os
import urllib
import urllib2
import base64
import json
import time
import copy
import ConfigParser
from oauth import oauth
import MySQLdb


class Logger(object):
	"""Logs events and writes stats to the logfile and console. Singleton"""

	instance      = None
	logfile       = 'unfollowr.log'
	print_events  = True
	show_info     = True
	show_warnings = True
	show_debug    = True

	def __new__(self, *dt, **mp):
		if self.instance == None:
			self.instance = object.__new__(self, *dt, **mp)
		return self.instance

	def set_loglevel(self, loglevel):
		levels = {'info': 1, 'warning': 2, 'debug': 3}
		self.show_info = True
		if levels[loglevel] >= levels['warning']:
			self.show_warnings = True
		else:
			self.show_warnings = False
		if levels[loglevel] >= levels['debug']:
			self.show_debug = True
		else:
			self.show_debug = False

	def info(self, message):
		if self.show_info:
			self.write('[Info]  '+str(message))

	def warning(self, message):
		if self.show_warnings:
			self.write('[Warn]  '+str(message))

	def debug(self, message):
		if self.show_debug:
			self.write('[Debug] '+str(message))

	def timestamp(self):
		return time.strftime('%Y-%m-%d %H:%M:%S')

	def write(self, message):
		event_line = self.timestamp()+' '+message
		if self.print_events:
			print event_line
		with open(self.logfile, 'a') as log:
			log.write(event_line+'\n')

class DBStore:
	"""Class to store unfollows information to database"""
	def __init__(self, host, user, passwd, db):
		self.host = host
		self.user = user
		self.passwd = passwd
		self.db = db
		self.__connect()

	def __connect(self):
		while True:
			try:
				conn = MySQLdb.connect(
					host   = self.host,
					user   = self.user,
					passwd = self.passwd,
					db     = self.db)
				Logger().warning('Successfully [re]connected to MySQL server '+self.host+' as '+self.user)
				break
			except MySQLdb.Error, e:
				Logger().warning('Couldn\'t connect to MySQL database. %d: %s' % (e.args[0], e.args[1]))
				time.sleep(10)
		self.cursor = conn.cursor(MySQLdb.cursors.DictCursor)

	def execute(self, query):
		try:
			self.cursor.execute(query)
		except MySQLdb.Error, e:
			# trying to reconnect to make another query
			self.__connect()
			self.cursor.execute(query)

	def save_unfollows(self, user_id, unfollowers):
		for unfollower_id in unfollowers.keys():
			self.execute("INSERT INTO unfollowr_unfollows "+
				"(`user_id`, `unfollower_id`, `unfollower_name`, `date`) "+
				" VALUES ('%d', '%d', '%s', NOW())" % (user_id, unfollower_id, unfollowers[unfollower_id]))

	def start_timer(self, followers_count):
		self.execute('INSERT INTO unfollowr_iterations (start_time, stop_time, followers_count) VALUES (NOW(), NOW(), %d)' % followers_count)
		return self.cursor.lastrowid

	def stop_timer(self, timer):
		self.execute('UPDATE unfollowr_iterations SET stop_time = "'+time.strftime('%Y-%m-%d %H:%M:%S')+'" WHERE id = %d' % timer)

	def update_user(self, user_id, data):
		if len(data) > 0:
			sql  = 'REPLACE INTO unfollowr_users SET '
			sql += ', '.join(map(lambda name: str(name)+' = "'+MySQLdb.escape_string(str(data[name]))+'"', data.keys()))
			sql += ', id = %d' % user_id
			self.execute(sql)
		else:
			Logger().warning('No data for update provided')


class Twitter:
	"""Twitter API communication class."""
	check_rate_limit = True
	rate_limit_checking_interval = 500 # in requests
	min_available_api_requests = 100 # basicly for unlimited (=20k requests/h)
	recorded_requests = 0 # don't change, it's for counting
	rate_checking_sleep = 120
	request_sleep = 0
	errors_sleep = 5

	def __init__(self):
		Logger().warning('You must not use Twitter class directly, use its descedants')
		exit()

	def verify_credentials(self):
		"""Verify if user credentials correct"""
		url = 'https://twitter.com/account/verify_credentials.json'
		if self.get_api_data(url, True) == False:
			return False
		else:
			return True

	def send_notification(self, user_id, message):
		"""Send direct message to user_id. Must be implemented in descendant"""
		pass

	def get_followers(self, user):
		"""User followers"""
		url = 'https://twitter.com/followers/ids/%s.json' % user
		followers = set()
		next_cursor = -1
		while next_cursor != 0:
			page_url = url+'?cursor=%d' % next_cursor
			data = self.get_api_data(page_url)
			if data == False or not data.has_key('next_cursor') or not data.has_key('ids'):
				return False
			else:
				next_cursor = data['next_cursor']
				followers = followers.union(set(data['ids']))
		return followers

	def get_friends(self, user):
		"""User friends (followings)"""
		url = 'https://twitter.com/friends/ids/%s.json' % user
		friends = set()
		next_cursor = -1
		while next_cursor != 0:
			page_url = url+'?cursor=%d' % next_cursor
			data = self.get_api_data(page_url)
			if data == False or not data.has_key('next_cursor') or not data.has_key('ids'):
				return False
			else:
				next_cursor = data['next_cursor']
				friends = friends.union(set(data['ids']))
		return friends

	def get_screen_name(self, user_id):
		"""User screen_name by id"""
		url = 'https://twitter.com/users/show/%d.json' % int(user_id)
		data = self.get_api_data(url)
		if data != False  and data.has_key('screen_name'):
			return data['screen_name']
		else:
			Logger().debug('No username for user %d' % int(user_id))
			return False

	def get_remaining_hits(self):
		"""Returns remaining hits"""
		url = 'https://twitter.com/account/rate_limit_status.json'
		data = self.get_api_data(url, True)
		if data.has_key('remaining_hits'):
			return data['remaining_hits']
		else:
			return False

	def check_hourly_limit(self):
		"""Checks if hourly request limit reached. In this case, falls asleep suddenly"""
		url = 'https://twitter.com/account/rate_limit_status.json'
		while True:
			data = self.get_api_data(url, True)
			if data == False:
				Logger().debug('Got nothing while checking rate limit, assuming status is ok')
				return
			elif data['remaining_hits'] > self.min_available_api_requests+self.rate_limit_checking_interval:
				Logger().debug('Twitter api rate limit checked: %d requests remaining' % data['remaining_hits'])
				return
			else:
				Logger().warning('Hourly twitter api rate limit reached (%d requests remaining). Sleeping for %d seconds' % (data['remaining_hits'], self.rate_checking_sleep))
				time.sleep(self.rate_checking_sleep)

	def get_api_data(self, url, unlimited=False):
		"""Internal method, returns decoded JSON data from API"""
		path = url[url.find('/', 10):]
		while True:
			try:
				if self.request_sleep > 0:
					Logger().debug('Sleeping for %d seconds before request' % self.request_sleep)
					time.sleep(self.request_sleep)
				if self.check_rate_limit and self.recorded_requests % self.rate_limit_checking_interval == 0 and not unlimited:
					self.check_hourly_limit()
				jsondata = self._get_api_data(url)
				data = json.loads(jsondata)
				Logger().debug('Got %d bytes of correct json data from twitter api url: %s' % (len(jsondata), path))
				return data
			except KeyboardInterrupt:
				Logger().warning('Got keyboard interrupt, exiting')
				exit()
			except ValueError:
				Logger().warning('Wrong JSON data from twitter. Trying again')
			except urllib2.HTTPError, e:
				try:
					answer = json.loads(e.read())
				except:
					continue
				if e.code == 401:
					Logger().debug('Received 401 for request %s' % path)
					return False
				elif e.code == 404:
					Logger().debug('Got HTTP error 404 for %s' % path)
					return False
				elif answer.has_key('error') and answer.has_key('request'):
					Logger().warning('Couldn\'t get API data, twitter returned error: %s' % json.dumps(answer))
					time.sleep(self.errors_sleep)
				else:
					Logger().warning('Twitter returned unexpected error %d: %s ' (e.code, json.dumps(answer)))
					return False
			except:
				Logger().warning('Something went wrong while getting twitter api answer')
				time.sleep(self.errors_sleep)
			finally:
				if not unlimited:
					self.recorded_requests += 1


class BasicAuthTwitterAPI(Twitter):

	def __init__(self, user, password):
		self.user = user
		self.password = password
		self.api_opener = urllib2.build_opener()

	def __add_auth_header(self, request):
		raw = '%s:%s' % (self.user, self.password)
		auth = 'Basic %s' % base64.b64encode(raw).strip()
		request.add_header('Authorization', auth)
		return request

	def _get_api_data(self, url):
		request = urllib2.Request(url)
		self.__add_auth_header(request)
		connection = self.api_opener.open(request)
		answer = connection.read()
		connection.close()
		return answer

	def send_notification(self, user_id, message):
		# TODO: move to common class
		url = urllib2.Request('https://twitter.com/direct_messages/new.json')
		self.__add_auth_header(url)
		data = {'user_id': user_id, 'text': message}
		while True:
			try:
				connection = self.api_opener.open(url, urllib.urlencode(data))
				answer = json.loads(connection.read())
				connection.close()
				Logger().info('Send message to %s: %s' % (data['user_id'], data['text']))
				return True
			except KeyboardInterrupt:
				Logger().warning('Got keyboard interrupt, exiting')
				exit()
			except urllib2.HTTPError, e:
				try:
					answer = json.loads(e.read())
				except:
					Logger().warning('Can\'t send direct message to user %s, twitter returned not JSON answer' % user_id)
					continue
				if e.code == 403:
					if answer.has_key('error') and answer.has_key('request'):
						if answer['error'] == 'You cannot send messages to users who are not following you.':
							return False
						if answer['error'] == 'There was an error sending your message: You can\'t send direct messages to this user right now':
							if self.get_screen_name(user_id) == False:
								Logger().warning('User %d was suspended, skipping' % user_id)
								return False
						Logger().warning('Couldn\'t send message, twitter returned error: %s' % json.dumps(answer))
						#time.sleep(self.errors_sleep)
						return False
					Logger().warning('Can\'t send direct message to user %s, probably suspended' % user_id)
					return False
				elif e.code == 404:
					Logger().debug('Got HTTP error 404 for %s' % path)
					return False
				else:
					Logger().warning('Twitter returned unexpected error on DM send %d: %s ' % (e.code, json.dumps(answer)))
					return False
			except:
				Logger().warning('Oops, something wrong with twitter. Trying again')
				time.sleep(self.errors_sleep)


class OAuthTwitterAPI(Twitter):

	check_rate_limit = True
	min_available_api_requests = 20
	rate_limit_checking_interval = 1

	def __init__(self, user, oauth_token, oauth_token_secret, consumer):
		self.__init_common(user, oauth_token, oauth_token_secret, consumer)

	def __init__(self, user, consumer):
		try:
			authfile = open('oauth/'+str(user)+'.oauth')
			oauth_token = authfile.readline().strip()
			oauth_token_secret = authfile.readline().strip()
			authfile.close()
		except:
			oauth_token = ''
			oauth_token_secret = ''
			Logger().warning('Can\'t get oauth info from file for user %s' % user)
		self.__init_common(user, oauth_token, oauth_token_secret, consumer)

	def __init_common(self, user, oauth_token, oauth_token_secret, consumer):
		self.user = user
		self.token = oauth.OAuthToken(oauth_token, oauth_token_secret)
		self.consumer = consumer
		self.signature_method = oauth.OAuthSignatureMethod_HMAC_SHA1()
		self.api_opener = urllib2.build_opener()

	def _get_api_data(self, url):
		if url.find('?') != -1:
			get_parameters = {}
			for pair in url[url.find('?')+1:].split('&'):
				key, value = pair.split('=')
				get_parameters[key] = value
		else:
			get_parameters = None
		request = oauth.OAuthRequest.from_consumer_and_token(
			self.consumer, token=self.token, http_url=url,
			parameters=get_parameters, http_method='GET')
		request.sign_request(self.signature_method, self.consumer, self.token)
		connection = self.api_opener.open(request.to_url())
		answer = connection.read()
		connection.close()
		return answer

	def send_notification(self, user_id, message):
		Logger().warning('Sending DM is not implemented in OAuthTwitterAPI class')
		exit()


class User:
	"""Represents happy twitter users"""
	def __init__(self, id):
		self.id = id

	def get_id(self):
		return self.id

	def get_filename(self, dirname, extension='list'):
		"""Returns name of user's own file in directory"""
		return dirname+'/'+str(self.id)+'.'+extension

	def get_unfollows(self, followers):
		"""Returns user unfollowers list"""
		unfollows = set()
		if len(followers) == 0:
			Logger().debug('User %s has no followers,skipping' % self.id)
			return unfollows
		past_followers = self.get_followers()
		unfollows = past_followers.difference(followers)
		return unfollows

	def get_followers(self):
		"""Reads user followers from file and returns them as set"""
		followers_set = set()
		try:
			with open(self.get_filename('followers')) as followers_file:
				for follower in followers_file:
					followers_set.add(int(follower))
		except IOError:
			pass
		return followers_set

	def update_followers(self, followers):
		"""Saves user followers to file"""
		with open(self.get_filename('followers'), 'w+') as followers_file:
			for follower in followers:
				followers_file.write(str(follower)+'\n')


class Unfollowr:
	"""Unfollowr main application class"""
	iterations_sleep = 300
	twitter = None
	premium_users_file = 'unfollowr.premium'
	premium_processing_frequency = 2000
	skiplist_file = 'unfollowr.skip'
	skiplist_refresh_interval = 1000
	skiplist = set()
	message = 'Unfollowers: '
	resolve_processing_username = False

	def __init__(self):
		try:
			config_file = open('unfollowr.conf')
		except:
			Logger().warning('Can\'t open config file "unfollowr.conf", exiting')
			exit()
		config = ConfigParser.ConfigParser()
		config.readfp(config_file)
		# configuring logger
		try:
			loglevel = config.get('logger', 'loglevel')
			if not loglevel in ['debug', 'warning', 'info']:
				raise
			Logger().set_loglevel(loglevel)
		except:
			pass
		# configuring credentails
		self.user = config.get('unfollowr', 'username')
		self.password = config.get('unfollowr', 'password')
		self.twitter = BasicAuthTwitterAPI(self.user, self.password)
		self.oauth_consumer = oauth.OAuthConsumer(config.get('oauth', 'consumer'), config.get('oauth', 'consumer_secret'))
		if not self.twitter.verify_credentials():
			Logger().warning('Twitter auth info incorrect. Check your config file!')
			exit()
		self.dbstore = DBStore(
				config.get('mysql', 'host'),
				config.get('mysql', 'user'),
				config.get('mysql', 'passwd'),
				config.get('mysql', 'database'))
		self.__create_datadirs(['followers', 'oauth', 'stats'])

	def __create_datadirs(self, dirs):
		"""Internal method to create datadirs if they still not exist"""
		for directory in dirs:
			if not os.path.exists(os.path.join(os.path.dirname(__file__), directory)):
				os.mkdir(os.path.join(os.path.dirname(__file__), directory))

	def refresh_skiplist(self):
		"""Refresh list of users to skip"""
		self.skiplist = set()
		try:
			with open(self.skiplist_file) as skiplist_file:
				for user_id in skiplist_file:
					try:
						self.skiplist.add(int(user_id))
					except:
						pass # allow empty/incorrect lines in skiplist
		except:
			Logger().warning('Couldn\'t open skiplist file! It\'s optional, don\'t worry')
		Logger().debug('Refreshed skiplist. Current: %s' % str(self.skiplist))

	def start(self):
		"""Main application loop"""
		while True:
			followers = self.twitter.get_followers(self.user)
			result = True # FIXME: not checking friends first if it is True
			if followers != False:
				timer = self.dbstore.start_timer(len(followers))
				#followers.reverse()
				for i, user_id in enumerate(followers):
					if i % self.skiplist_refresh_interval == 0:
						self.refresh_skiplist()
					Logger().info('Processing user #%d from %d' % (i+1, len(followers)))
					user_followers = self.twitter.get_followers(user_id)
					if user_followers != False and len(user_followers) > 3000:
						Logger().debug("User has more than 3000 followers, skipping")
						continue
					if self.process_user(user_id, result) == True:
						if result == False:
							result = True
							self.calculate_premium()
						elif i % self.premium_processing_frequency == 0:
							self.calculate_premium()
					else:
						result = False
				self.dbstore.stop_timer(timer)
			else:
				Logger.warning('ZOMG! Could not get list of my followers!')
			if self.iterations_sleep > 0:
				Logger().info('Sleeping before next iteration for %d seconds' % self.iterations_sleep)
				time.sleep(self.iterations_sleep)

	def calculate_premium(self):
		"""Calculate donators and just good people"""
		self.process_userlist(self.twitter.get_friends(self.user), 'friends')
		self.process_userlist(self.get_premium(), 'premium')

	def get_premium(self):
		"""Returns list of premium user ids"""
		users = set()
		try:
			# file format: user_id => any comments about user (e.g. username, email, donation info)
			with open(self.premium_users_file) as users_file:
				for user in users_file:
					try:
						user_id, description = user.split(' => ')
						users.add(int(user_id))
					except:
						pass # allow empty lines
		except IOError:
			Logger().warning('Couldn\'t open premium users file')
		return users

	def process_userlist(self, users, list_name):
		"""Processes any list of user ids, one-time"""
		latest_own_followers = self.twitter.get_followers(self.user)
		if users != False:
			for i, user_id in enumerate(users):
				Logger().warning('Processing user #%d from %d (list: %s)' % (i+1, len(users), list_name))
				if user_id not in latest_own_followers:
					username = self.twitter.get_screen_name(user_id)
					if username == False:
						username = str(user_id)+' (username could not be resolved!)'
					else:
						username = '@'+username
					Logger().warning('User %s from list %s doesn\'t follow us, skipping' % (username, list_name))
				else:
					self.process_user(user_id)

	def process_user(self, user_id, last_successfull = True):
		"""Processing one user unfollows"""
		if user_id in self.skiplist:
			Logger().warning('Skipping user because of skiplist')
			return last_successfull
		if self.resolve_processing_username == True:
			username = self.twitter.get_screen_name(user_id)
			if username != False:
				Logger().warning('Processing user @%s' % username)
			else:
				Logger().warning('Couldn\t resolve username for %d' % user_id)
		user_followers = self.get_user_followers(user_id)
		if user_followers == False:
			Logger().warning('Couldn\'t get list of follwers for %s, skipping' % user_id)
			return last_successfull
		user = User(user_id)
		user_unfollowers = user.get_unfollows(user_followers)
		named_user_unfollowers = {}
		for unfollower_id in user_unfollowers:
			unfollower_name = self.twitter.get_screen_name(unfollower_id)
			if unfollower_name == False:
				unfollower_name = 'suspended'
			named_user_unfollowers[unfollower_id] = unfollower_name
		Logger().debug('Unfollowed '+str(user_id)+': '+str(named_user_unfollowers))
		not_notified_unfollows = self.send_unfollowed_notifications(user_id, named_user_unfollowers)
		if not_notified_unfollows != False:
			Logger().warning('Couldn\'t notify about next unfollows: '+str(not_notified_unfollows))
			user_followers.update(not_notified_unfollows)
		user.update_followers(user_followers)
		unfollows_to_store = dict((id, named_user_unfollowers[id]) for id in named_user_unfollowers if not_notified_unfollows == False or id not in not_notified_unfollows)
		Logger().debug('Storing unfollows: '+str(unfollows_to_store))
		self.dbstore.save_unfollows(user_id, unfollows_to_store)
		if not_notified_unfollows == False:
			unfollows_in_queue = 0
		else:
			unfollows_in_queue = len(not_notified_unfollows)
		self.dbstore.update_user(user_id, {'last_check': MySQLdb.times.format_TIMESTAMP(time), 'unfollows_in_queue':  unfollows_in_queue })
		return (not_notified_unfollows == False and len(user_unfollowers) > 0) or last_successfull

	def get_user_followers(self, user_id):
		"""Returns user's followers. Tries to use provided OAuth access, if any and necessary"""
		if os.path.exists(os.path.join(os.path.dirname(__file__), 'oauth', str(user_id)+'.oauth')):
			user_twitter_api = OAuthTwitterAPI(user_id, self.oauth_consumer)
			if user_twitter_api.verify_credentials() != False:
				if user_twitter_api.get_remaining_hits() > OAuthTwitterAPI.min_available_api_requests:
					Logger().warning('Using OAuth to get followers of %s' % user_id)
					user_followers = user_twitter_api.get_followers(user_id)
					if user_followers != False:
						return user_followers
					else:
						Logger().warning('Couldn\'t get followers with OAuth, trying using own requests')
				else:
					Logger().warning('Too low remaining requests to process via OAuth, trying using own requests')
			else:
				self.twitter.send_notification(user_id, 'Warning: your OAuth data was revoked or become incorrect')
				Logger().warning('OAuth login info is incorrect, revoking it')
		user_followers = self.twitter.get_followers(user_id)
		if user_followers == False:
			if not os.path.exists(os.path.join(os.path.dirname(__file__), 'oauth', str(user_id)+'.oauth.notified')):
				notified = self.twitter.send_notification(user_id, 'Looks like we can\'t get your followers list (protected account?). Please allow me OAuth access: http://bobrik.name/unfollowr/')
				if notified:
					file = open(os.path.join(os.path.dirname(__file__), 'oauth', str(user_id)+'.oauth.notified'), 'w')
					file.close()
			else:
				Logger().debug('User %s has already been notified about OAuth access' % user_id)
		return user_followers

	def send_unfollowed_notifications(self, user, named_unfollowers):
		"""Send message to user about unfollows"""
		# TODO: make generators readable/not generators/smaller
		notification_list = dict(('@'+str(named_unfollowers[id]), id) for id in named_unfollowers if named_unfollowers[id] != 'suspended')
		suspended_count = named_unfollowers.values().count('suspended')
		if suspended_count > 0:
			notification_list['@suspended (count: %d)' % suspended_count] = set(id for id in named_unfollowers if named_unfollowers[id] == 'suspended')
		message_chain_initialized = False
		while len(notification_list) > 0:
			if not message_chain_initialized:
				message = self.message
				message_chain_initialized = True
			else:
				message = ''
			message_unfollowers = {}
			# SUDDENLY!!1 Paustovsky
			item = notification_list.popitem()
			message_unfollowers[item[0]] = item[1]
			while len(message + ', '.join([name for name in message_unfollowers.keys()])) < 140 and len(notification_list) > 0:
				item = notification_list.popitem()
				message_unfollowers[item[0]] = item[1]
			if len(message + ', '.join([name for name in message_unfollowers.keys()])) > 140:
				item = message_unfollowers.popitem()
				notification_list[item[0]] = item[1]
			result = self.twitter.send_notification(user, message+', '.join([name for name in message_unfollowers.keys()]))
			if result != True:
				notification_list.update(message_unfollowers)
				unsuccessful = set(id for id in notification_list.values() if type(id) == int)
				for ids in notification_list.values():
					if type(ids) == set: # suspended users
						unsuccessful.update(set(ids))
				return unsuccessful
		return False



if __name__ == '__main__':
	unfollowr = Unfollowr()
	unfollowr.start()
