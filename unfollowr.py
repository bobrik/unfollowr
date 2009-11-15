#!/usr/bin/python2.6
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

import os
import urllib
import json
import time
import copy
import ConfigParser


class Logger(object):
	"""Class to log events and write stats to file and console. Singletone"""

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
		f = open(self.logfile, 'a')
		f.write(event_line+'\n')
		f.close()


class Twitter:
	"""Twitter API communication class."""
	user     = None
	password = None
	api_opener = None
	check_rate_limit = False
	min_available_api_requests = 10
	rate_checking_sleep = 120
	request_sleep = 1

	def __init__(self, user, password):
		self.user = user
		self.password = password
		self.api_opener = urllib.URLopener()

	def send_notification(self, user_id, message):
		"""Send direct message to user_id"""
		url = 'http://%s:%s@twitter.com/direct_messages/new.json' % (self.user, self.password)
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
			except:
				Logger().warning('Oops, something wrong with twitter communication. Trying again')

	def verify_credentials(self):
		"""Verify is user credentails correct"""
		url = 'http://%s:%s@twitter.com/account/verify_credentials.json' % (self.user, self.password)
		if self.get_api_data(url) == {}:
			return False
		else:
			return True

	def get_followers(self, user):
		"""Get user followers list with ids"""
		url = 'http://%s:%s@twitter.com/followers/ids/%s.json' % (self.user, self.password, user)
		return self.get_api_data(url)

	def get_screen_name(self, user_id):
		"""Get user screen_name by id"""
		url = 'http://%s:%s@twitter.com/users/show/%d.json' % (self.user, self.password, int(user_id))
		data = self.get_api_data(url)
		if data.has_key('screen_name'):
			return data['screen_name']
		else:
			Logger().debug('No username for user %d' % int(user_id))
			return ''

	def check_hourly_limit(self):
		"""Check is hourly request limit reached and waits for new requests"""
		url = 'http://%s:%s@twitter.com/account/rate_limit_status.json' % (self.user, self.password)
		while True:
			data = self.get_api_data(url, True)
			if data['hourly_limit'] > self.min_available_api_requests:
				Logger().debug('Twitter api rate limit checking ok, %d requests remaining' % data['hourly_limit'])
				return
			else:
				Logger().warning('Hourly twitter api rate limit reached (%d requests remaining). Sleeping for %d seconds' % (data['hourly_limit'], self.rate_checking_sleep))

	def get_api_data(self, url, unlimited=False):
		"""Internal method to get decoded JSON data from API"""
		path = url[url.find('/', 10):]
		while True:
			try:
				if self.check_rate_limit and not unlimited:
					self.check_hourly_limit()
					time.sleep(self.request_sleep)
				connection = self.api_opener.open(url)
				jsondata = connection.read()
				connection.close()
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
						return {}
					elif error_code[1] == 401:
						#Logger().debug('Twitter needs to get authorisation again. Having another try after %d seconds' % self.error_sleep)
						#time.sleep(self.error_sleep)
						Logger().debug('Twitter bug, we can\'t see followers list for user with protected updates')
						return {}
			except:
				Logger().warning('Something went wrong while getting twitter api answer')


class User:
	"""Twittter user class to work with followers"""
	def __init__(self, id):
		self.id = id

	def get_id(self):
		return self.id

	def get_filename(self):
		return 'followers/'+str(self.id)+'.list'

	def calculate(self, followers):
		"""Calculate user unfollows and save followers list"""
		if len(followers) > 0:
			unfollows = self.get_unfollows(followers)
			self.update(followers)
			return unfollows
		else:
			Logger().debug('Empty followers list for user %s, skipping' % self.id)
			return []

	def get_unfollows(self, followers):
		"""Return user unfollows"""
		unfollows = []
		past_followers = self.get_past_followers()
		for past_follower in past_followers:
			if not past_follower in followers:
				unfollows.append(past_follower)
		return unfollows

	def get_past_followers(self):
		"""Read user followers from file and return as list"""
		followers_list = []
		try:
			followers_file = open(self.get_filename())
			for follower in followers_file:
				try:
					followers_list.append(int(follower))
				except:
					pass
			followers_file.close()
		except:
			pass
		return followers_list

	def update(self, followers):
		"""Write user followers to file"""
		followers_file = open(self.get_filename(), 'w+')
		for follower in followers:
			followers_file.write(str(follower)+'\n')
		followers_file.close()

	def append_followers(self, followers):
		"""Append additional user followers and save full list"""
		past_followers = self.get_past_followers()
		Logger().debug('There was %d followers for %s' % (len(past_followers), self.id))
		past_followers += followers
		self.update(followers)
		Logger().debug('Currently there is %d followers for %s' % (len(past_followers), self.id))


class Unfollowr:
	"""Unfollowr application class"""
	iterations_sleep = 2400
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
		self.twitter = Twitter(self.user, self.password)
		if not self.twitter.verify_credentials():
			Logger().warning('Twitter auth info incorrect. Check your config file!')
			exit()
		# datadir creation
		if not os.path.exists(os.path.join(os.path.dirname(__file__), 'followers')):
			os.mkdir(os.path.join(os.path.dirname(__file__), 'followers'))

	def start(self):
		"""Main application loop"""
		while True:
			followers = self.twitter.get_followers(self.user)
			for i, user in enumerate(followers):
				Logger().info('Calculating for user #%d from %d' % (i+1, len(followers)))
				user_unfollowers = self.calculate_user(user)
				named_user_unfollowers = []
				unnamed_user_unfollowers = []
				for unfollower in user_unfollowers:
					name = self.twitter.get_screen_name(unfollower)
					if name != '':
						named_user_unfollowers.append(name)
					else:
						# FIXME: do we need to store them? mostly spammers
						unnamed_user_unfollowers.append(unfollower)
				if len(unnamed_user_unfollowers) > 0:
					named_user_unfollowers.append('suspended (count: {0:d})'.format(len(unnamed_user_unfollowers)))
				Logger().debug('Unfollows for '+str(user)+':'+str(named_user_unfollowers)+', unnamed: '+str(unnamed_user_unfollowers))
				self.send_unfollowed_notifications(user, named_user_unfollowers)
			Logger().info('Sleeping befoge next iteration for %d seconds' % self.iterations_sleep)
			time.sleep(self.iterations_sleep)

	def calculate_user(self, user):
		"""Calculate user unfollows"""
		user = User(user)
		user_followers = self.twitter.get_followers(user.id)
		unfollows = user.calculate(user_followers)
		return unfollows

	def send_unfollowed_notifications(self, user, user_unfollowers):
		"""Send message to user about unfollows"""
		message = 'Tweeps that no longer following you: '
		for pack in self.split_to_packs(user_unfollowers):
			self.twitter.send_notification(user, message+pack)

	def split_to_packs(self, data, max_pack_length=90):
		"""Split usernames to pack to make direct messages shorter than 140 chars"""
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
