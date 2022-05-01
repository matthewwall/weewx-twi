#!/usr/bin/env python
# Copyright 2016-2022 Matthew Wall, all rights reserved
"""
Collect data from Texas Weather Instruments stations.  This driver should work
with at least the following models: WLS, WRL, WR, WPS.

Based on the protocol as specified by TWI:
  http://txwx.com/wp-content/uploads/2013/04/TWI_binary_record_format.pdf

Thanks to weewx user Jason Kitchens for testing and validation.

Commands:
V - firmware version number
S - firmware serial number
I - unit ID number
C - daily minimum and maximum of all parameters (rainfall rate), then clear
c - daily minimum and maximum of all parameters (term rain), then clear
D - same as C but no clear
d - same as c but no clear
E - min/max plus date and time of occurence
e - min/max plus date and time of occurence
M - term min/max with datetime then clear
m - same as M but no clear
R - current conditions (rainfall rate)
r - current conditions (term rain)
K - calculated values (dewpoint, windchill, heatindex)
Q - hi resolution time, wind, temperature, pressure

If there is logged data in memory (WLS-8000):
T - top of data records, then send data
N - next record, then send data
A - same record again
P - previous data record, then send data
B - bottom of data records, then send data

z - accumulated lightning data for current hour
Z - current lightning data

L - leaf wetness
"""

# FIXME: implement wee_config interface for full set of commands:
#        V, S, I, C, c, D, d, E, e, M, m, R, r, K, Q, T, N, A, P, B, z
# FIXME: detect WLS-8000 and read its data for genArchiveRecords
# FIXME: implement host:port and read from socket instead of serial

from __future__ import with_statement, print_function
import serial
import syslog
import time

import weewx.drivers
from weewx.wxformulas import calculate_rain

DRIVER_NAME = 'TWI'
DRIVER_VERSION = '0.4'


def loader(config_dict, _):
    return TWIDriver(**config_dict[DRIVER_NAME])


def confeditor_loader():
    return TWIConfigurationEditor()


# import/setup logging, WeeWX v3 is syslog based but WeeWX v4 is logging based,
# try v4 logging and if it fails use v3 logging
try:
    # WeeWX4 logging
    import logging

    log = logging.getLogger(__name__)


    def logdbg(msg):
        log.debug(msg)


    def loginf(msg):
        log.info(msg)


    def logerr(msg):
        log.error(msg)

except ImportError:
    # WeeWX legacy (v3) logging via syslog
    import syslog


    def logmsg(level, msg):
        syslog.syslog(level, 'twi: %s' % msg)


    def logdbg(msg):
        logmsg(syslog.LOG_DEBUG, msg)


    def loginf(msg):
        logmsg(syslog.LOG_INFO, msg)


    def logerr(msg):
        logmsg(syslog.LOG_ERR, msg)


class TWIConfigurationEditor(weewx.drivers.AbstractConfEditor):
    @property
    def default_stanza(self):
        return """
[TWI]
    # This section is for the Texas Weather Instruments driver.

    # The serial port to which the station is connected
    port = /dev/ttyUSB0

    # The station model, such as WRL, WLS, or WPS
    model = WRL

    # How often, in seconds, to query the hardware for data
    poll_interval = 15

    # The driver to use
    driver = user.twi
"""

    def prompt_for_settings(self):
        print("Specify the serial port on which the station is connected, for")
        print("example /dev/ttyUSB0 or /dev/ttyS0.")
        port = self._prompt('port', '/dev/ttyUSB0')
        return {'port': port}


class TWIDriver(weewx.drivers.AbstractDevice):
    def __init__(self, **stn_dict):
        loginf('driver version is %s' % DRIVER_VERSION)
        self._model = stn_dict.get('model', 'WRL')
        self._poll_interval = int(stn_dict.get('poll_interval', 15))
        loginf('poll interval is %s' % self._poll_interval)
        max_tries = int(stn_dict.get('max_tries', 10))
        retry_wait = int(stn_dict.get('retry_wait', 10))
        port = stn_dict.get('port', TWIStation.DEFAULT_PORT)
        self.last_rain = None
        self._station = TWIStation(port, max_tries, retry_wait)
        self._station.open()
        loginf('unit id: %s' % self._station.get_unit_id())
        loginf('firmware version: %s' % self._station.get_firmware_version())
        loginf('firmware serial: %s' % self._station.get_firmware_serial())

    def closePort(self):
        self._station.close()

    @property
    def hardware_name(self):
        return self._model

    def genLoopPackets(self):
        while True:
            raw = self._station.get_current()
            if raw:
                logdbg("raw data: %s" % raw)
                data = TWIStation.parse_current(raw)
                logdbg("parsed data: %s" % data)
                packet = self._data_to_packet(data)
                yield packet
            time.sleep(self._poll_interval)

    def _data_to_packet(self, data):
        pkt = {
            'dateTime': int(time.time() + 0.5),
            'usUnits': weewx.US,
            'windDir': data.get('wind_dir'),
            'windSpeed': data.get('wind_speed'),
            'inTemp': data.get('temperature_in'),
            'outTemp': data.get('temperature_out'),
            'extraTemp1': data.get('temperature_aux'),
            'outHumidity': data.get('humidity'),
            'pressure': data.get('pressure'),
            'rain': calculate_rain(data['rain_total'], self.last_rain)
        }
        self.last_rain = data['rain_total']
        return pkt


class TWIStation(object):
    COMPASS_POINTS = {'N': 0, 'NNE': 22.5, 'NE': 45, 'ENE': 67.5, 'E': 90,
                      'ESE': 112.5, 'SE': 135, 'SSE': 157.5, 'S': 180,
                      'SSW': 202.5, 'SW': 225, 'WSW': 247.5, 'W': 270,
                      'WNW': 292.5, 'NW': 315, 'NNW': 337.5}
    DEFAULT_PORT = '/dev/ttyUSB0'

    def __init__(self, port, max_tries=5, retry_wait=10):
        self.port = port
        self.baudrate = 19200
        self.timeout = 3 # seconds
        self.max_tries = max_tries
        self.retry_wait = retry_wait
        self.serial_port = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, _, value, traceback):
        self.close()

    def open(self):
        logdbg("open serial port %s" % self.port)
        self.serial_port = serial.Serial(
            self.port, self.baudrate, timeout=self.timeout)

    def close(self):
        if self.serial_port is not None:
            logdbg("close serial port %s" % self.port)
            self.serial_port.close()
            self.serial_port = None

    def get_data(self, cmd):
        logdbg("send cmd: %s" % cmd)
        self.serial_port.write(cmd)
        buf = self.serial_port.readline()
        logdbg("station said: %s" % ' '.join(["%0.2X" % ord(c) for c in buf]))
        buf = buf.strip()
        return buf

    def get_data_with_retry(self, cmd):
        for ntries in range(0, self.max_tries):
            try:
                buf = self.get_data(cmd)
                return buf
            except (serial.serialutil.SerialException, weewx.WeeWxIOError) as e:
                loginf("Failed attempt %d of %d to get readings: %s"
                       % (ntries + 1, self.max_tries, e))
                time.sleep(self.retry_wait)
        else:
            msg = "Max retries (%d) exceeded for command '%s'" \
                  % (self._max_tries, cmd)
            logerr(msg)
            raise weewx.RetriesExceeded(msg)

    def get_current(self):
        return self.get_data_with_retry(b'r')

    def get_firmware_version(self):
        return self.get_data_with_retry(b'V')

    def get_firmware_serial(self):
        return self.get_data_with_retry(b'S')

    def get_unit_id(self):
        return self.get_data_with_retry(b'I')

    @staticmethod
    def parse_current(s):
        # sample responses:
        # 5:15 07/24/90 SSE 04MPH 052F 069F 078F 099% 30.04R 00.19"D 01.38"M 11.78"T
        # 13:28 06/02/16 WSW 00MPH 460F 081F 086F 054% 29.31F 00.00"D 00.00"M 00.00"R
        # 13:28 06/02/16 SW  00MPH 460F 081F 086F 054% 29.31F 00.00"D 00.00"M 00.00"R
        # 13:29 06/02/16 W   00MPH 460F 081F 086F 054% 29.31F 00.00"D 00.00"M 17.15"T
        parts = s.split()
        data = {
            'time': parts[0],
            'date': parts[1],
            'wind_dir': TWIStation.COMPASS_POINTS.get(parts[2]),
            'wind_speed': TWIStation.try_float(parts[3][:2]),
            'temperature_aux': TWIStation.try_float(parts[4][:3]),
            'temperature_in': TWIStation.try_float(parts[5][:3]),
            'temperature_out': TWIStation.try_float(parts[6][:3]),
            'humidity': TWIStation.try_float(parts[7][:3]),
            'pressure': TWIStation.try_float(parts[8][:-1]),
            'rain_day': TWIStation.try_float(parts[9][:-2]),
            'rain_month': TWIStation.try_float(parts[10][:-2]),
            'rain_total': TWIStation.try_float(parts[11][:-2])
        }
        return data

    @staticmethod
    def try_float(s):
        try:
            return float(s)
        except ValueError:
            pass
        return None


# define a main entry point for basic testing of the station without weewx
# engine and service overhead.  invoke this as follows from the weewx root dir:
#
# PYTHONPATH=bin python bin/weewx/drivers/twi.py

if __name__ == '__main__':
    import optparse

    usage = """%prog [options] [--debug] [--help]"""

    syslog.openlog('twi', syslog.LOG_PID | syslog.LOG_CONS)
    syslog.setlogmask(syslog.LOG_UPTO(syslog.LOG_INFO))
    parser = optparse.OptionParser(usage=usage)
    parser.add_option('--version', dest='version', action='store_true',
                      help='display driver version')
    parser.add_option('--debug', dest='debug', action='store_true',
                      help='display diagnostic information while running')
    parser.add_option('--port', dest='port', metavar='PORT',
                      help='serial port to which the station is connected',
                      default=TWIStation.DEFAULT_PORT)

    (options, args) = parser.parse_args()

    if options.version:
        print("twi driver version %s" % DRIVER_VERSION)
        exit(1)

    if options.debug:
        syslog.setlogmask(syslog.LOG_UPTO(syslog.LOG_DEBUG))

    with TWIStation(options.port) as s:
        print("unit id:", s.get_unit_id())
        print("firmware serial:", s.get_firmware_serial())
        print("firmware version:", s.get_firmware_version())
        while True:
            raw = s.get_current()
            print("raw:", raw)
            print("parsed:", TWIStation.parse_current(raw))
            time.sleep(5)
