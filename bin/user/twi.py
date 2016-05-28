#!/usr/bin/env python
# Copyright 2016 Matthew Wall, all rights reserved
"""
Collect data from Texas Weather Instruments stations.  This driver should work
with at least the following models: WLS, WRL, WR, WPS.

Based on the protocol as specified by TWI:
  http://txwx.com/wp-content/uploads/2013/04/TWI_binary_record_format.pdf
"""

# FIXME: implement wee_config interface for full set of commands:
#        V, S, I, C, c, D, d, E, e, M, m, R, r, K, Q, T, N, A, P, B, z
# FIXME: detect WLS-8000 and read its data for genArchiveRecords
# FIXME: implement host:port and read from socket instead of serial

from __future__ import with_statement
import serial
import syslog
import time

import weewx.drivers
from weewx.wxformulas import calculate_rain

DRIVER_NAME = 'TWI'
DRIVER_VERSION = '0.1'

def loader(config_dict, _):
    return TWIDriver(**config_dict[DRIVER_NAME])

def confeditor_loader():
    return TWIConfigurationEditor()


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

    # The driver to use
    driver = weewx.drivers.twi
"""

    def prompt_for_settings(self):
        print "Specify the serial port on which the station is connected, for"
        print "example /dev/ttyUSB0 or /dev/ttyS0."
        port = self._prompt('port', '/dev/ttyUSB0')
        return {'port': port}


class TWIDriver(weewx.drivers.AbstractDevice):
    def __init__(self, **stn_dict):
        loginf('driver version is %s' % DRIVER_VERSION)
        self._model = stn_dict.get('model', 'WRL')
        self._poll_interval = int(stn_dict.get('poll_interval', 15))
        loginf('poll interval is %s' % poll_interval)
        self._max_tries = int(stn_dict.get('max_tries', 10))
        self._retry_wait = int(stn_dict.get('retry_wait', 10))
        port = stn_dict.get('port', TWIStation.DEFAULT_PORT)
        self.last_rain = None
        self._station = TWIStation(port)
        self._station.open()
        loginf('unit id: %s' % self._station.get_unit_id())
        loginf('firmware version: %s' % self._station.get_firmware_version())
        loginf('firmware serial: %s' % self._station.get_firmware_serial())

    def closePort(self):
        self._station.close()

    def hardware_name(self):
        return self._model

    def genLoopPackets(self):
        while True:
            raw = self._station.get_current(self.max_tries, self.retry_wait)
            if raw:
                logdbg("raw data: %s" % raw)
                data = TWIStation.parse_current(raw)
                logdbg("parsed data: %s" % data)
                packet = self._data_to_packet(data)
                yield packet
            time.sleep(self._poll_interval)

    def _data_to_packet(self, data):
        pkt = {'dateTime': int(time.time() + 0.5), 'usUnits': weewx.US}
        pkt['windDir'] = data.get('wind_dir')
        pkg['windSpeed'] = data.get('wind_speed')
        pkg['inTemp'] = data.get('temperature_in')
        pkg['outTemp'] = data.get('temperature_out')
        pkg['extraTemp1'] = data.get('temperature_aux')
        pkg['outHumidity'] = data.get('humidity')
        pkg['pressure'] = data.get('pressure')
        pkt['rain'] = calculate_rain(data['rain_total'], self.last_rain)
        self.last_rain = data['rain_total']
        return pkt


class TWIStation(object):
    COMPASS_POINTS = {'N': 0, 'NNE': 22.5, 'NE': 45, 'ENE': 67.5, 'E': 90,
                      'ESE': 112.5, 'SE': 135, 'SSE': 157.5, 'S': 180,
                      'SSW': 202.5, 'SW': 225, 'WSW': 247.5, 'W': 270,
                      'WNW': 292.5, 'NW': 315, 'NNW': 337.5}
    DEFAULT_PORT = '/dev/ttyS0'

    def __init__(self, port):
        self.port = port
        self.baudrate = 19200
        self.timeout = 3 # seconds
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
        self.serial_port.write(cmd)
        buf = self.serial_port.readline()
        if DEBUG_SERIAL:
            logdbg("station said: %s" %
                   ' '.join(["%0.2X" % ord(c) for c in buf]))
        buf = buf.strip()
        return buf

    def get_data_with_retry(self, cmd, max_tries=5, retry_wait=10):
        for ntries in range(0, max_tries):
            try:
                buf = self.get_data(cmd)
                return buf
            except (serial.serialutil.SerialException, weewx.WeeWxIOError), e:
                loginf("Failed attempt %d of %d to get readings: %s" %
                       (ntries + 1, max_tries, e))
                time.sleep(retry_wait)
        else:
            msg = "Max retries (%d) exceeded for command '%s'" % (max_tries, cmd)
            logerr(msg)
            raise weewx.RetriesExceeded(msg)

    def get_current(self, max_tries=5, retry_wait=10):
        return self.get_data_with_retry('R', max_tries, retry_wait)

    def get_firmware_version(self):
        return self.get_data_with_retry('V')

    def get_firmware_serial(self):
        return self.get_data_with_retry('S')

    def get_unit_id(self):
        return self.get_data_with_retry('I')

    @staticmethod
    def parse_current(s):
        # sample string:
        # 5:15 07/24/90 SSE 04MPH 052F 069F 078F 099% 30.04R 00.19"D 01.38"M 11.78"T
        parts = s.split(' ')
        data = dict()
        data['time'] = parts[0]
        data['date'] = parts[1]
        data['wind_dir'] = TWIStation.COMPASS_POINTS.get(parts[2])
        data['wind_speed'] = TWIStation.try_float(parts[3][:2]) # mph
        data['temperature_aux'] = TWIStation.try_float(parts[4][:3]) # F
        data['temperature_in'] = TWIStation.try_float(parts[5][:3]) # F
        data['temperature_out'] = TWIStation.try_float(parts[6][:3]) # F
        data['humidity'] = TWIStation.try_float(parts[7][:3]) # %
        data['pressure'] = TWIStation.try_float(parts[8][:-1]) # inHg
        data['rain_day'] = TWIStation.try_float(parts[9][:-2]) # in
        data['rain_month'] = TWIStation.try_float(parts[10][:-2]) # in
        data['rain_total'] = TWIStation.try_float(parts[11][:-2]) # in
        return data

    @staticmethod
    def try_float(s):
        try:
            return float(s)
        except ValueError, e:
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
        print "twi driver version %s" % DRIVER_VERSION
        exit(1)

    if options.debug:
        syslog.setlogmask(syslog.LOG_UPTO(syslog.LOG_DEBUG))

    with TWIStation(port) as s:
        print "unit id:", s.get_unit_id()
        print "firmware serial:", s.get_firmware_serial()
        print "firmware version:", s.get_firmware_version()
        while True:
            raw = s.get_current()
            print "raw:", raw
            print "parsed:", TWIStation.parse_current(raw)
            time.sleep(5)
