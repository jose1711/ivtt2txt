#!/usr/bin/env python
"""
Module to aid retrieving live data on ETA of trams/buses from imhd.sk
(currently only available in Bratislava).

This enables braille or console readers to access the published data.

Read and respect usage terms outlined at
http://imhd.sk/ba/online-zastavkova-tabula?info=copyright&skin=1

ivtt2txt stands for Imhd-Virtual-Time-Table-To-Text

 sent        recvd           rel.time (s)
----------------------------------------
2probe                           0
            3probe               0.5
5                                1
            data                 1.5
2 (loops every 30 seconds)      30
            3                   30.5
            data                random (async,
                                        updates typically every 20-30 seconds)

"""
import requests
import sys
import re
import time
import logging
import json

from websocket import create_connection, WebSocketConnectionClosedException
from bs4 import BeautifulSoup
from datetime import datetime


class NoMatch(Exception):
    """
    Raised when a bus stop name cannot be uniquely resolved into an id.
    It happens when specifying ambiguous names in queries, such as 'Hranic'
    (could be either Hranicna or Nam. hraniciarov)
    """
    pass


class ConnError(Exception):
    """
    Raised when imhd.sk webpage is inaccessible
    """
    pass


class ParseError(Exception):
    """
    Raised when main page cannot be parsed properly
    """
    pass


def busstop2id(busstop):
    """
    Resolves name of the bus stop (e. g. Kosicka) into its numerical id

    busstop    bus stop name - full or partial (but a query must yield a unique result)
    """
    r = requests.get('http://imhd.sk/ba/vyhladavanie?hladaj=' + busstop)

    soup = BeautifulSoup(r.text, 'html.parser')
    try:
        link = soup.select(".cestovny_poriadok_zastavkova_tabula > a")[0]['href']
    except IndexError as e:
        raise NoMatch("Multiple matches for string or invalid name? " + str(e))

    return int(re.search('.*z=([^&]+)&.*', link).group(1))


def location2id(lat, lon):
    """
    Resolve lat/lon pair into the nearest busstop

    Returns dictionary containing the following keys:
    z        busstop id
    n        list of platforms
    vzdialenost    distance in meters
    nazov        name of the bus stop
    oznacenie    unused
    info         copyright notice (see terms of use)
    """
    return json.loads(requests.get('http://imhd.sk/ba/api/sk/cp',
                      params={"op": "gns",
                              "lat": lat,
                              "lng": lon,
                              "t": datetime.now().strftime("%s")}).text)


def destid2destname(id):
    """
    Translates numerical destination id(s) into name(s)

    Returns a dictionary with keys containing ids and values containing bus stop names

    id may be scalar (str or int) in case of a single id, or a list
    """
    if not isinstance(id, list):
        id = [id]
    else:
        id = [str(x) for x in id]
    idlist = ','.join([str(x) for x in id])
    r = requests.get('http://imhd.sk/ba/api/sk/cp',
                     params={"op": "gsn",
                             "id": idlist,
                             "t": datetime.now().strftime("%s")})
    return {int(x): y for (x, y) in zip(json.loads(r.text)['sn'].keys(), json.loads(r.text)['sn'].values())}


class Stop(object):
    """
    Object allowing to retrieve data about eta of buses and trams from imhd.sk
    webpage the same way as your browser does.
    """
    def __init__(self, busstopid, debug=False):
        self.headers = {
                   'Connection': 'keep-alive',
                   'Host': 'imhd.sk',
                   'User-Agent': 'Mozilla/5.0 (X11; Linux i686; rv:45.0) Gecko/20100101 Firefox/45.0'
        }
        self._urlbase = 'http://imhd.sk/ba/'

        self.eta = []

        self.busstopid = str(busstopid)
        if debug:
            logging.basicConfig(level=logging.DEBUG)

        self._data = {}
        self._valid_platforms = []

        # get all valid choices for platform refs
        self.get_valid_platforms()

    def _u(self, url):
        return self._urlbase + url

    def get_valid_platforms(self):
        return
        r = requests.get(self._u('online-zastavkova-tabula?z=') +
                         self.busstopid,
                         params={'skin': '2', 'fullscreen': '1'}, headers=self.headers)
        if r.status_code != 200:
            raise ConnError("Could not load the main timetable! (status code: %s)" % r.status_code)

        self._valid_platforms = re.search(r'nastupiste=\[([^]]+)\]', r.text).group(1)
        self._valid_platforms = [int(x) for x in self._valid_platforms.split(',')]

        logging.debug("Platforms: %s" % self._valid_platforms)

    def _subscribe(self):
        """
        Subscribe to an stop data "channel". Should not be called directly.
        """
        referer = self._u('online-zastavkova-tabula?z=%s'
                          '&skin=2&fullscreen=1' % self.busstopid)
        self.headers['Referer'] = referer
        self.r = requests.get('http://imhd.sk/rt/sio/socket.io.js', params=None,
                              headers=self.headers)

        cookies = {'testCookie': '1'}
        while True:
            self.r = requests.get('http://imhd.sk/rt/sio2/',
                                  params={'EIO': '3',
                                          'transport': 'polling',
                                          't': str(int(time.time()*1000))},
                                  cookies=cookies,
                                  headers=self.headers)

            cookies = {'testCookie': '1'}
            self.r = requests.get('http://imhd.sk/rt/sio2/',
                                  params={'EIO': '3',
                                          'transport': 'polling',
                                          't': str(int(time.time()*1000))+'-0'},
                                  cookies=cookies,
                                  headers=self.headers)

            io = self.r.cookies.get('io', None)
            if io:
                break
            else:
                time.sleep(2)

        logging.debug(self.r.cookies)

        cookies = {'testCookie': '1', 'io': io}
        requests.get('http://imhd.sk/rt/sio2/',
                     params={'EIO': '3',
                             'transport': 'polling',
                             't': str(int(time.time()*1000))+'-1',
                             'sid': io},
                     cookies=cookies)

        self.headers['Origin'] = 'http://imhd.sk'
        self.headers['Content-type'] = 'text/plain;charset=UTF-8'

        reqdata = '42["req",[{0},["*"]]]'.format(self.busstopid,
                                                 self._valid_platforms)

        reqdata = reqdata.replace(' ', '')
        reqdata = str(len(reqdata)) + ':' + reqdata
        logging.debug(reqdata)

        r = requests.post('http://imhd.sk/rt/sio2/',
                          params={'EIO': '3',
                                  'transport': 'polling',
                                  't': str(int(time.time()*1000))+'-2',
                                  'sid': io},
                           headers=self.headers,
                           data=reqdata,
                           cookies=cookies)

        self.ws = create_connection('ws://imhd.sk/rt/sio2/?EIO=3&transport=websocket&sid=' + io)

        self.ws.send('2probe')
        # '3probe' is sent back
        self.ws.recv()
        time.sleep(1)
        self.ws.send('5')

    def fetch(self, platform):
        """
        Get or update arrival times info for a specified platform

        platform     platform ref

        Nothing is returned by this method, use self.get_data.
        """

        if 'ws' not in dir(self):
            logging.debug('subscribing')
            self._subscribe()
            self.timer = time.time()
        else:
            logging.debug('no need to resubscribe')
        time.sleep(3)
        # aux. timer to help empty queued responses
        response_timer = time.time()
        while True:
            if (time.time() - self.timer) > 30:
                self.timer = time.time()
                logging.debug('sending keepalive')
                try:
                    self.ws.send('2')
                except OSError:  # (BrokenPipeError, ConnectionResetError)
                    self._subscribe()
                    self.timer = time.time()
                    time.sleep(3)
                    continue
            try:
                data = self.ws.recv()[2:]
                table_data = json.loads(data)
                # if no new message came within the last 3 seconds, return
                if time.time() - response_timer > 3:
                    return
            except ValueError:  # json.decoder.JSONDecodeError
                logging.debug('Cannot parse >>%s<<' % data)
                continue
            except:
                self._subscribe()
                self.timer = time.time()
                time.sleep(3)
                continue

            if '{0}.{1}'.format(self.busstopid, platform) in table_data[1]:
                self._data[platform] = table_data[1].get('{0}.{1}'.format(self.busstopid, platform))
                response_timer = time.time()

        logging.debug(self.r.text)

    def get_data(self, platform, conn, force_update=False, resolve_names=False):
        """
        platform   platform ref
        conn       bus number(s) you wish to get data for (multiple values
                   are passed a list)

        force_update set to True if you want to fetch fresh data

        a list of dictionaries is returned
        """
        result = []
        if not isinstance(conn, list):
            conn = [str(conn)]
        else:
            conn = [str(x) for x in conn]

        if force_update:
            self.fetch(platform)

        for x in self._data[platform]['tab']:
            if x['linka'] in conn:
                x['toffset'] = int(int(x['cas']) / 1000 - time.time())
                if resolve_names:
                    for key in x:
                        if key in ('cielZastavka', 'lastZ'):
                            if x[key] != 0:
                                x[key] = destid2destname(x[key])
                result.append(x)
        return result


if __name__ == "__main__":
    try:
        query, bus, platform = sys.argv[1:4]
    except ValueError as e:
        print("mandatory arguments: 'bus stop name' 'bus number' 'platform ref'")
        sys.exit(1)

    try:
        busstopid = busstop2id(query)
    except Exception as e:
        print('Could not resolve %s into id (%s)' % (query, e))
        sys.exit(1)

    platform = int(platform)
    print('bus stop id: {0} ({1})'.format(busstopid, query))
    print('platform id: {0}'.format(platform))
    print('bus: {0}'.format(bus))
    print('refresh: 120 seconds')
    print(35 * '-')

    busdata = Stop(busstopid, debug=False)
    while True:
        print('{0}'.format(datetime.now().strftime('%H:%M:%S')))
        print(busdata.get_data(platform, bus,
                               force_update=True,
                               resolve_names=True))
        time.sleep(120)
