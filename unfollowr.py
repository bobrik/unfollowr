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
		try:
			conn = MySQLdb.connect( host   = host,
									user   = user,
									passwd = passwd,
									db     = db)
		except MySQLdb.Error, e:
			Logger().warning('Couldn\'t connect to MySQL database. %d: %s' % (e.args[0], e.args[1]))
			exit(1)
		self.cursor = conn.cursor(MySQLdb.cursors.DictCursor)
	def save_unfollows(self, user_id, unfollowers):
		for unfollower_id in unfollowers.keys():
			self.cursor.execute("INSERT INTO unfollowr_unfollows "+
				"(`user_id`, `unfollower_id`, `unfollower_name`, `date`) "+
				" VALUES ('%d', '%d', '%s', NOW())" % (user_id, unfollower_id, unfollowers[unfollower_id]))

	def start_timer(self):
		self.cursor.execute('INSERT INTO unfollowr_iterations (start_time, stop_time) values (now(), now())')
		return self.cursor.lastrowid

	def stop_timer(self, timer):
		self.cursor.execute('UPDATE unfollowr_iterations SET stop_time = "'+time.strftime('%Y-%m-%d %H:%M:%S')+'" WHERE id = %d' % timer)


class Twitter:
	"""Twitter API communication class."""
	check_rate_limit = False
	min_available_api_requests = 10
	rate_checking_sleep = 120
	request_sleep = 0

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
		followers = []
		next_cursor = -1
		while next_cursor != 0:
			page_url = url+'?cursor=%d' % next_cursor
			data = self.get_api_data(page_url)
			if data == False or not data.has_key('next_cursor') or not data.has_key('ids'):
				return False
			else:
				next_cursor = data['next_cursor']
				followers += data['ids']
		return followers

	def get_followers_old(self, user):
		"""User followers"""
		url = 'https://twitter.com/followers/ids/%s.json' % user
		return self.get_api_data(url)

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
			elif data['remaining_hits'] > self.min_available_api_requests:
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
				if self.check_rate_limit and not unlimited:
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
			except IOError, error_code:
				if error_code[0] == "http error":
					if error_code[1] == 404:
						Logger().debug('Got HTTP error 404 for %s' % path)
						return False
					elif error_code[1] == 401:
						Logger().debug('Received 401 for request %s' % path)
						return False
			except:
				Logger().warning('Something went wrong while getting twitter api answer')


class BasicAuthTwitterAPI(Twitter):

	def __init__(self, user, password):
		self.user = user
		self.password = password
		self.api_opener = urllib.URLopener()

	def __get_url(self, url):
		return url[:url.find('://')+3]+self.user+':'+self.password+'@'+url[url.find('://')+3:]

	def _get_api_data(self, url):
		connection = self.api_opener.open(self.__get_url(url))
		answer = connection.read()
		connection.close()
		return answer

	def send_notification(self, user_id, message):
		url = self.__get_url('https://twitter.com/direct_messages/new.json')
		data = {'user_id': user_id, 'text': message}
		while True:
			try:
				connection = self.api_opener.open(url, urllib.urlencode(data))
				connection.close()
				Logger().info('Send message to %s: %s' % (data['user_id'], data['text']))
				break
			except KeyboardInterrupt:
				Logger().warning('Got keyboard interrupt, exiting')
				exit()
			except IOError, error_code:
				if error_code[0] == "http error":
					if error_code[1] == 403:
						Logger().warning('Can\'t send direct message to user %s, probably suspended' % user_id)
						break
					Logger().warning('Got HTTP error %s error from twitter, trying again' % error_code[1])
			except:
				Logger().warning('Oops, something wrong with twitter. Trying again')


class OAuthTwitterAPI(Twitter):

	check_rate_limit = True
	min_requests_to_process = 20

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
		self.api_opener = urllib.URLopener()

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
		unfollows = []
		if len(followers) == 0:
			Logger().debug('User %s has no followers,skipping' % self.id)
			return unfollows
		past_followers = self.get_followers()
		for past_follower in past_followers:
			if not past_follower in followers:
				unfollows.append(past_follower)
		return unfollows

	def get_followers(self):
		"""Reads user followers from file and returns them as list"""
		followers_list = []
		try:
			with open(self.get_filename('followers')) as followers_file:
				for follower in followers_file:
					followers_list.append(int(follower))
		except IOError:
			pass
		return followers_list

	def update_followers(self, followers):
		"""Saves user followers to file"""
		with open(self.get_filename('followers'), 'w+') as followers_file:
			for follower in followers:
				followers_file.write(str(follower)+'\n')

	def append_followers(self, followers):
		"""Append additional user followers and save full list"""
		past_followers = self.get_followers()
		Logger().debug('Once upon a time, %s had %d followers' % (len(past_followers), self.id))
		past_followers += followers
		self.update_followers(followers)
		Logger().debug('Currently %s has %d followers' % (len(past_followers), self.id))


class Unfollowr:
	"""Unfollowr main application class"""
	iterations_sleep = 300
	twitter = None

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

	def start(self):
		"""Main application loop"""
		while True:
			timer = self.dbstore.start_timer()
			followers = self.twitter.get_followers(self.user)
			if followers != False:
				for i, user_id in enumerate(followers):
					Logger().info('Processing user #%d from %d' % (i+1, len(followers)))
					user_followers = self.get_user_followers(user_id)
					if user_followers == False:
						Logger().warning('Couldn\'t get list of follwers for %s, skipping' % user_id)
						continue
					user = User(user_id)
					user_unfollowers = user.get_unfollows(user_followers)
					named_user_unfollowers = {}
					unfollowers_names = []
					for unfollower_id in user_unfollowers:
						unfollower_name = self.twitter.get_screen_name(unfollower_id)
						if unfollower_name == False:
							unfollower_name = 'suspended'
						named_user_unfollowers[unfollower_id] = unfollower_name
					Logger().debug('Unfollowed '+str(user_id)+': '+str(named_user_unfollowers))
					self.dbstore.save_unfollows(user_id, named_user_unfollowers)
					user.update_followers(user_followers)
					notification_list = [x for x in named_user_unfollowers.values() if x != 'suspended']
					suspended_unfolllowers_count = len([x for x in named_user_unfollowers.values() if x == 'suspended'])
					if suspended_unfolllowers_count > 0:
						notification_list.append('suspended (count: %d)' % suspended_unfolllowers_count)
					self.send_unfollowed_notifications(user_id, notification_list)
			else:
				Logger.warning('Could not get list of my followers!')
			self.dbstore.stop_timer(timer)
			if self.iterations_sleep > 0:
				Logger().info('Sleeping before next iteration for %d seconds' % self.iterations_sleep)
				time.sleep(self.iterations_sleep)

	def get_user_followers(self, user_id):
		"""Returns user's followers. Tries to use provided OAuth access, if any and necessary"""
		if os.path.exists(os.path.join(os.path.dirname(__file__), 'oauth', str(user_id)+'.oauth')):
			user_twitter_api = OAuthTwitterAPI(user_id, self.oauth_consumer)
			if user_twitter_api.verify_credentials() != False:
				if user_twitter_api.get_remaining_hits() < OAuthTwitterAPI.min_requests_to_process:
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
				self.twitter.send_notification(user_id, 'Looks like we can\'t get your followers list (protected account?). Please allow me OAuth access: http://bobrik.name/unfollowr/')
				file = open(os.path.join(os.path.dirname(__file__), 'oauth', str(user_id)+'.oauth.notified'), 'w')
				file.close()
			else:
				Logger().debug('User %s has already been notified about OAuth access' % user_id)
		return user_followers

	def send_unfollowed_notifications(self, user, user_unfollowers):
		"""Send message to user about unfollows"""
		message = 'Tweeps that no longer following you: '
		for pack in self.split_to_packs(user_unfollowers):
			self.twitter.send_notification(user, message+pack)

	def split_to_packs(self, data, max_pack_length=90):
		"""Split usernames to packs shorter than maximum pack length"""
		data = copy.copy(data) # we don't want to affect on data
		packs = []
		while len(data) > 0:
			element = str(data.pop())
			pack = '@'+element
			while len(data) > 0 and len(pack) < max_pack_length:
				pack += ', @'+str(data.pop())
			packs.append(pack)
		return packs


if __name__ == '__main__':
	unfollowr = Unfollowr()
	unfollowr.start()
