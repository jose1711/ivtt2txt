#!/usr/bin/env python
"""
Module to aid retrieving live data on ETA of trams/buses from imhd.sk
(currently only available in Bratislava).

This enables braille or console readers to access the displayed data
in a way more efficient fashion.

Read and adhere to terms specified at
http://imhd.sk/ba/online-zastavkova-tabula?info=copyright&skin=1

ivtt2txt stands for Imhd-Virtual-Time-Table-To-Text
"""

import requests, sys, re, time
from bs4 import BeautifulSoup
import websocket, json
try:
	import _thread as thread
except:
	import thread


class NoMatch(Exception):
	"""
	This exception is raised when a bus stop name cannot be uniquely resolved into an id.
	It happens when specifying ambiguous names in queries, such as 'Hranic' (could be
	either Hranicna or Nam. hraniciarov)
	"""
	pass

class ConnError(Exception):
	"""
	This exception is raised when imhd.sk webpage is inaccessible
	"""
	pass

class ParseError(Exception):
	"""
	This exception is raised when main page cannot be parsed properly
	"""
	pass

def busstop2id(busstop):
	"""
	Resolves name of the bus stop (e. g. Kosicka) into its numerical id

	busstop		bus stop name or its part (but a query must yield a unique result)
	"""
	r = requests.get('http://imhd.sk/ba/vyhladavanie?hladaj='+query)

	soup = BeautifulSoup(r.text, 'html.parser')
	try:
		link=soup.select(".cestovny_poriadok_zastavkova_tabula > a")[0]['href']
	except IndexError as e:
		raise NoMatch("Multiple matches for string or invalid name? "+str(e))
		return None

	return re.search('.*z=([^&]+)&.*',link).group(1)

def location2id(lat,lon):
	"""
	Resolve lat/lon pair into the nearest busstop

	Returns dictionary containing the following keys:
	z		busstop id
	n		list of platforms
	vzdialenost	distance in meters
	nazov		name of the bus stop
	oznacenie	unused
	info		copyright notice (see terms of use)
	"""
	import datetime
	return json.loads(requests.get('http://imhd.sk/ba/api/sk/cp', params={"op":"gns","lat":lat,"lng":lon,"t":datetime.datetime.now().strftime("%s")}).text)

def destid2destname(id):
	"""
	Translates a list of numerical destination ids into names
	
	Returns a dictionary with keys containing ids and values containing bus stop names

	Example with a single id:
	foo=imhd_virt_timetable2txt.destid2destname([374])
	print (foo[374])
	"""
	import datetime
	if not isinstance(id,list):
		raise TypeError('Id must be a list!')
	idlist=','.join([str(x) for x in id])
	r = requests.get('http://imhd.sk/ba/api/sk/cp', params={"op":"gsn","id":idlist,"t":datetime.datetime.now().strftime("%s")})
	return {int(x):y for (x,y) in zip(json.loads(r.text)['sn'].keys(),json.loads(r.text)['sn'].values())}

class ImhdBa(object):
	"""
	Object allowing to retrieve data about eta of buses and trams from imhd.sk webpage the same way as your browser does.
	"""
	def __init__(self):
		self.eta = []
		self.delay = []
		self.linkadata = []
		self.result = []
		self.msgcounter = 0

	def get_arrival_time(self, busstopid,linka,nastupiste,debug=False):
		"""
		Retrieves arrival times for a specified bus stop id, ref (direction) and bus number
		
		busstopid	numerical identifier of a bus stop (may be retrieved using busstop2id method)
		linka		bus number(s) you wish to get data for (multiple values are comma separated)
		nastupiste	platform ref
		debug (opt)	debug information (normally not needed)

		Returns a list containing members of a dictionary type with arrival time data, one per each connection.
		"""

		# reset vars
		self.__init__()

		headers={
		'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
		'Accept-Encoding':'gzip, deflate',
		'Accept-Language':'en-US,en;q=0.5',
		'Connection':'keep-alive',
		'Host':'imhd.sk',
		'User-Agent':'Mozilla/5.0 (X11; Linux i686; rv:45.0) Gecko/20100101 Firefox/45.0'
		}

		busstopid=str(busstopid)
		linka=str(linka)
		nastupiste=str(nastupiste)
		r = requests.get('http://imhd.sk/ba/online-zastavkova-tabula?z='+busstopid, params={'skin':'2', 'fullscreen':'1'}, headers=headers)
		if r.status_code != 200:
			raise ConnError("Could not load the main timetable! (status code: %s)" % r.status_code)

		try:
			platform=[x for x in r.text.split("\n") if x.startswith(u'nastupiste=')][0].split('=')[1].replace(';','')
		except IndexError:
			raise ParseError("Could not parse the main page")

		if debug:
			print("Platform: %s" % platform)

		if not nastupiste in platform:
			raise ParseError("No such platform")
		
		headers['Accept']='*/*'
		headers['If-None-Match']='1.3.7'
		headers['Referer']='http://imhd.sk/ba/online-zastavkova-tabula?z='+busstopid+'&skin=2&fullscreen=1'
		r = requests.get('http://imhd.sk/rt/sio/socket.io.js', params=None, headers=headers)

		cookies={'testCookie':'1'}
		headers['Accept']='text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
		r = requests.get('http://imhd.sk/rt/sio/', params={'EIO':'3','transport':'polling','t':str(int(time.time()*1000))+'-0'}, cookies=cookies)

		io=r.cookies['io']
		if debug:
			print (r.cookies)
			print ('IO: {}'.format(io))

		cookies={'testCookie':'1', 'io':io}
		headers['Accept']='text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8' 
		r = requests.get('http://imhd.sk/rt/sio/', params={'EIO':'3','transport':'polling','t':str(int(time.time()*1000))+'-1','sid':io}, cookies=cookies)

		if debug:
			print (r.text)

		def on_message(ws, message):
			if debug:
				print(':::'+message+':::')
			linkalist=linka.split(',')
			if debug:
				print ("Filter: %s" % linkalist)
			# first messages received are probes, ignore those
			if 'nastupiste' in message:
				self.msgcounter += 1
				if debug:
					print ("msg counter: %d" % self.msgcounter)
				if self.msgcounter > len(platform)+2:
					if debug:
						print ("No message for this platform received, terminating")
					ws.close()
				linkadata = json.loads(str(message[2:]))
				if str(linkadata[1]['nastupiste']) != str(nastupiste):
					return
				else:
					for llinkadata in linkadata[1]['tab']:
						if str(llinkadata['typ']) != 'online':
							continue
						if not str(llinkadata['linka']) in linkalist:
							continue
						else:
							self.result.append(llinkadata)
					if not self.result:
						if debug:
							print("No data for this bus received, maybe it's not in service?")
						ws.close()
						return
					ws.close()
			else:
				return

		def on_error(ws, error):
			print (error)

		def on_close(ws):
			if debug:
				print("### closed ###")

		def on_open(ws):
			def run(*args):
				time.sleep(1)
				ws.send('2probe')
				time.sleep(10)
				while True:
					try:
						ws.send('5')
					except websocket.WebSocketConnectionClosedException:
						# if socket is already closed, make no fuzz about it
						pass
					time.sleep(30)
				time.sleep(10)
				ws.close()
				if debug:
					print ("thread terminating...")

			thread.start_new_thread(run, ())

			time.sleep(2)
			headers.pop('If-None-Match')
			headers['Origin']='http://imhd.sk'
			headers['Content-type']='text/plain;charset=UTF-8' 
			headers['Accept']='*/*' 
			reqdata = '42["req",['+busstopid+','+platform+']]'
			reqdata = str(len(reqdata))+':'+reqdata
			r = requests.post('http://imhd.sk/rt/sio/', params={'EIO':'3','transport':'polling','t':str(int(time.time()*1000))+'-2','sid':io}, headers=headers, data=reqdata, cookies=cookies)

			if debug:
				print("posted data")

		if debug:
			websocket.enableTrace(True)

		ws = websocket.WebSocketApp('ws://imhd.sk/rt/sio/?EIO=3&transport=websocket&sid='+io,
			on_message = on_message,
			on_error = on_error,
			on_close = on_close)
		ws.on_open = on_open
		ws.run_forever()

		time.sleep(1)

		return self.result

if __name__ == "__main__":
	import datetime
	try:
		query=sys.argv[1]
		bus=sys.argv[2]
		platform=sys.argv[3]
	except IndexError as e:
		print ("mandatory arguments: 'bus stop name' 'bus number' 'platform ref'")
		sys.exit(1)

	try:
		busstopid = busstop2id(query)
	except Exception as e:
		print ('Could not resolve %s into id (%s)' % (query, e))
		sys.exit(1)
	print ('bus stop id: {}'.format(busstopid))

	imhdba = ImhdBa()
	try:
		arrival_times=imhdba.get_arrival_time(busstopid, bus, platform, debug=False)
	except Exception as e:
		print ("Failure: %s" % e)
		sys.exit(1)

	currtime = datetime.datetime.now()
	print ("Current time: %s" % currtime)
	for (bus,eta,delay,destid) in ([(x['linka'],(datetime.datetime.fromtimestamp(x['cas']/1000)-currtime).total_seconds()/60,x['casDelta'],int(x['ciel'])) for x in arrival_times]):
		print ('bus {} to {} in {:.1f} min (delay: {})'.format(bus,destid2destname([destid])[destid],eta,delay))
