#!/usr/bin/env python

from gi.repository import GLib as gobject # Python 3.x
import platform
import logging
import sys
import os
import requests # for http GET
import _thread as thread   # for daemon = True  / Python 3.x

# our own packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
from vedbus import VeDbusService

path_UpdateIndex = '/UpdateIndex'

class BaseService:
  def __init__(self, servicename, deviceinstance, paths, productname , connection):
    self._dbusservice = VeDbusService(servicename)
    self._paths = paths

    logging.debug("%s /DeviceInstance = %d" % (servicename, deviceinstance))

    # Create the management objects, as specified in the ccgx dbus-api document
    self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
    self._dbusservice.add_path('/Mgmt/ProcessVersion', 'Unkown version, and running on Python ' + platform.python_version())
    self._dbusservice.add_path('/Mgmt/Connection', connection)

    # Create the mandatory objects
    self._dbusservice.add_path('/DeviceInstance', deviceinstance)
    self._dbusservice.add_path('/ProductName', productname)
    self._dbusservice.add_path('/CustomName', productname)    
    self._dbusservice.add_path('/Latency', None)
    self._dbusservice.add_path('/FirmwareVersion', 0.1)
    self._dbusservice.add_path('/HardwareVersion', 0)
    self._dbusservice.add_path('/Connected', 1)
    self._dbusservice.add_path('/Serial', 1234)
    self._dbusservice.add_path('/UpdateIndex', 0)

    for path, settings in self._paths.items():
      self._dbusservice.add_path(
        path, settings['initial'], gettextcallback=settings['textformat'], writeable=True, onchangecallback=self._handlechangedvalue)

  def _handlechangedvalue(self, path, value):
    logging.debug("someone else updated %s to %s" % (path, value))
    return True # accept the change


class SolarService(BaseService):
  def __init__(self, servicename, deviceinstance, paths, productname='Solar Inverter', connection='Solar Inverter service'):
    BaseService.__init__(self, servicename, deviceinstance, paths, productname , connection)

    self._dbusservice.add_path('/ProductId', 0xA141) # value used in ac_sensor_bridge.cpp of dbus-cgwacs
    self._dbusservice.add_path('/DeviceType', 345)
    self._dbusservice.add_path('/Role', 'solar')
    self._dbusservice.add_path('/StatusCode', 0)  # Dummy path so VRM detects us as a PV-inverter.
    self._dbusservice.add_path('/Position', 0)

    self.solar_0=0.0
    self.solar_1=0.0

    gobject.timeout_add(500, self._update) # pause 500ms before the next request

  def _update(self):
    try:
      meter_url = "http://192.168.0.10/api/states/sensor.solar_power"
      if self._dbusservice['/UpdateIndex']%2==1:
        meter_url = "http://192.168.0.10/api/states/sensor.solar_dachoben_power"

      headers = {'content-type': 'application/json', 'Authorization': 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI3MTc2MTMxYTJlZGM0YjZhYTcwYzE0ODFmOTI2YWE2YiIsImlhdCI6MTY3MjM5MDA5NywiZXhwIjoxOTg3NzUwMDk3fQ.uJ26hu-S9_gMf2sL-6JyYWQNV_rlo7KlWQhhRktF9ig'}
      meter_r = requests.get(url=meter_url,headers=headers, verify=False, timeout=5) # request data from the Fronius PV inverter
      meter_data = meter_r.json() # convert JSON data
      value = float(meter_data['state'])
      #value = value * -1

      if self._dbusservice['/UpdateIndex']%2==0:
        self.solar_0=value
      else:
        self.solar_1=value
      total=self.solar_0+self.solar_1

      self._dbusservice['/Connected'] = 1
      self._dbusservice['/Ac/Power'] = total
      self._dbusservice['/Ac/Voltage'] = 230
      self._dbusservice['/Ac/Current'] = round(float(total)/230,2)
      self._dbusservice['/Ac/L1/Voltage'] = 230
      self._dbusservice['/Ac/L2/Voltage'] = 230
      self._dbusservice['/Ac/L3/Voltage'] = 230
      self._dbusservice['/Ac/L1/Current'] = round(float(self.solar_0)/230,2) + round(float(self.solar_1)/230,2)
      self._dbusservice['/Ac/L2/Current'] = 0
      self._dbusservice['/Ac/L3/Current'] = 0
      self._dbusservice['/Ac/L1/Power'] = self.solar_0 + self.solar_1
      self._dbusservice['/Ac/L2/Power'] = 0
      self._dbusservice['/Ac/L3/Power'] = 0
      self._dbusservice['/Ac/Energy/Forward'] = self._dbusservice['/Ac/Energy/Forward'] + (self._dbusservice['/Ac/Power']/(60*60/0.5*1000))            


      logging.info("Solar Consumption: {:.0f} {:.0f}".format(self.solar_0,self.solar_1))
    except Exception as e:
      self._dbusservice['/Connected'] = 0
      logging.info("WARNING: Could not read from PV inverter: %s",e)

    # increment UpdateIndex - to show that new data is available
    self._dbusservice['/UpdateIndex'] = (self._dbusservice['/UpdateIndex'] + 1 ) % 256
    return True


def main():
  logging.basicConfig(level=logging.DEBUG) # use .INFO for less logging
  thread.daemon = True # allow the program to quit

  from dbus.mainloop.glib import DBusGMainLoop
  # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
  DBusGMainLoop(set_as_default=True)

  #formatting 
  _kwh = lambda p, v: (str(round(v, 2)) + ' KWh')
  _a = lambda p, v: (str(round(v, 1)) + ' A')
  _w = lambda p, v: (str(round(v, 1)) + ' W')
  _v = lambda p, v: (str(round(v, 1)) + ' V')
  _paths={
      '/Ac/Energy/Forward': {'initial': 0, 'textformat': _kwh}, # energy bought from the grid
      '/Ac/Energy/Reverse': {'initial': 0, 'textformat': _kwh}, # energy sold to the grid
      '/Ac/Power': {'initial': 0, 'textformat': _w},
          
      '/Ac/Current': {'initial': 0, 'textformat': _a},
      '/Ac/Voltage': {'initial': 0, 'textformat': _v},
          
      '/Ac/L1/Voltage': {'initial': 0, 'textformat': _v},
      '/Ac/L2/Voltage': {'initial': 0, 'textformat': _v},
      '/Ac/L3/Voltage': {'initial': 0, 'textformat': _v},
      '/Ac/L1/Current': {'initial': 0, 'textformat': _a},
      '/Ac/L2/Current': {'initial': 0, 'textformat': _a},
      '/Ac/L3/Current': {'initial': 0, 'textformat': _a},
      '/Ac/L1/Power': {'initial': 0, 'textformat': _w},
      '/Ac/L2/Power': {'initial': 0, 'textformat': _w},
      '/Ac/L3/Power': {'initial': 0, 'textformat': _w},
      '/Ac/L1/Energy/Forward': {'initial': 0, 'textformat': _kwh},
      '/Ac/L2/Energy/Forward': {'initial': 0, 'textformat': _kwh},
      '/Ac/L3/Energy/Forward': {'initial': 0, 'textformat': _kwh},
      '/Ac/L1/Energy/Reverse': {'initial': 0, 'textformat': _kwh},
      '/Ac/L2/Energy/Reverse': {'initial': 0, 'textformat': _kwh},
      '/Ac/L3/Energy/Reverse': {'initial': 0, 'textformat': _kwh},
    }

  solar_output = SolarService(servicename='com.victronenergy.pvinverter',deviceinstance=40,paths=_paths)

  logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
  mainloop = gobject.MainLoop()
  mainloop.run()

if __name__ == "__main__":
  main()
