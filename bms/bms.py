#!/usr/bin/env python

import subprocess
from gi.repository import GLib as gobject # Python 3.x
import platform
import logging
import sys
import os
import requests # for http GET
import _thread as thread   # for daemon = True  / Python 3.x
import can
from paho.mqtt import client as mqtt_client

# Victron packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
import dbus.service
import ve_utils
from vedbus import VeDbusService
from settingsdevice import SettingsDevice

path_UpdateIndex = '/UpdateIndex'

class BMSService:
  def __init__(self, servicename, deviceinstance, paths, productname='BMS', connection='BMS service'):
    self._dbusservice = VeDbusService(servicename)
    self._paths = paths

    logging.debug("%s /DeviceInstance = %d" % (servicename, deviceinstance))

    # Create the management objects, as specified in the ccgx dbus-api document
    self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
    self._dbusservice.add_path('/Mgmt/ProcessVersion', 'Unkown version, and running on Python ' + platform.python_version())
    self._dbusservice.add_path('/Mgmt/Connection', connection)

    # Create the mandatory objects
    self._dbusservice.add_path('/DeviceInstance', deviceinstance)
    self._dbusservice.add_path('/ProductId', 0)
    self._dbusservice.add_path('/ProductName', productname)
    self._dbusservice.add_path('/CustomName', productname)    
    self._dbusservice.add_path('/Latency', None)
    self._dbusservice.add_path('/FirmwareVersion', 0.1)
    self._dbusservice.add_path('/HardwareVersion', 0)
    self._dbusservice.add_path('/Connected', 1)
    self._dbusservice.add_path('/Serial', 1234)
    self._dbusservice.add_path('/Position', 0)
    self._dbusservice.add_path('/UpdateIndex', 0)

    self._vmin=0
    self._vmax=4
    self._ccl=0
    self._dcl=0

    for path, settings in self._paths.items():
      self._dbusservice.add_path(
        path, settings['initial'], gettextcallback=settings['textformat'], writeable=True, onchangecallback=self._handlechangedvalue)

    self._watchdog=0
    gobject.timeout_add(1000, self._update) 

    self._bus = can.interface.Bus("can8", bustype="socketcan")
    self._notifier = can.Notifier(self._bus, [self.on_message_received])

    self.connect_mqtt()

  def on_message_received(self, msg):

    if msg.arbitration_id == 0x355 : 
      if msg.dlc < 4 : return
      soc=int.from_bytes([msg.data[0], msg.data[1]], byteorder='little', signed=False)
      soh=int.from_bytes([msg.data[2], msg.data[3]], byteorder='little', signed=False)
      #soc=soc*0.95
      self._dbusservice['/Soc'] = soc 
      self._dbusservice['/Soh'] = soh
      self._dbusservice['/Capacity'] = 130.0 * (soc/100)
      return

    if msg.arbitration_id == 0x370 : 
      if msg.dlc < 8 : return
      t_h=int.from_bytes([msg.data[0], msg.data[1]], byteorder='little', signed=False)
      t_l=int.from_bytes([msg.data[2], msg.data[3]], byteorder='little', signed=False)
      v_h=int.from_bytes([msg.data[4], msg.data[5]], byteorder='little', signed=False)
      v_l=int.from_bytes([msg.data[6], msg.data[7]], byteorder='little', signed=False)
      self._vmin = float(v_l)/1000
      self._vmax = float(v_h)/1000
      self._dbusservice['/System/MinCellVoltage'] = self._vmin
      self._dbusservice['/System/MaxCellVoltage'] = self._vmax
      self._dbusservice['/System/MinCellTemperature'] = float(t_l)/10
      self._dbusservice['/System/MaxCellTemperature'] = float(t_h)/10

      self._mqtt_client.publish("victron/bms/MinCellVoltage",self._vmin);
      self._mqtt_client.publish("victron/bms/MaxCellVoltage",self._vmax);
      return

    if msg.arbitration_id == 0x35c : #Request flags
      if msg.dlc < 1 : return
      b0 = msg.data[0]
      req_full_charge     = b0 & (1<<3)
      req_force_charge2   = b0 & (1<<4)
      req_force_charge1   = b0 & (1<<5)
      enable_discharge    = b0 & (1<<6)
      enable_charge       = b0 & (1<<7)

      if self._ccl<=0:
        enable_charge=0
      if self._dcl<=0:
        enable_discharge=0

      #enable_charge=True
      if enable_charge:
        self._dbusservice['/Io/AllowToCharge']=1
      else:
        self._dbusservice['/Io/AllowToCharge']=0

      if enable_discharge:
        self._dbusservice['/Io/AllowToDischarge']=1
      else:
        self._dbusservice['/Io/AllowToDischarge']=0

      if (enable_charge and enable_discharge):
        self._dbusservice['/SystemSwitch']=1
      else:
        self._dbusservice['/SystemSwitch']=0

      self._watchdog=0
      #print("enabled:",self._dbusservice['/SystemSwitch'])

      return

    if msg.arbitration_id == 0x351 : #Battery voltage + current limits
      if msg.dlc < 8 : return
      v_charge=int.from_bytes([msg.data[0], msg.data[1]], byteorder='little', signed=False)
      a_charge=int.from_bytes([msg.data[2], msg.data[3]], byteorder='little', signed=False)
      a_discharge=int.from_bytes([msg.data[4], msg.data[5]], byteorder='little', signed=False)
      v_discharge=int.from_bytes([msg.data[6], msg.data[7]], byteorder='little', signed=False)
      
      #print(a_charge)
      #a_charge=50 #limit to 5A
      #a_charge=250 #limit to 25A
      soc=self._dbusservice['/Soc']
      ccl=float(a_charge)/10
      dcl=float(a_discharge)/10
      ccl=min(ccl,35)
      #dcl=min(dcl,45)
      if self._vmax>=3.60: ccl=min(ccl,0)
      if self._vmax>=3.58: ccl=min(ccl,0)
      if self._vmax>=3.57: ccl=min(ccl,0.5)
      if self._vmax>=3.54: ccl=min(ccl,1)
      if self._vmax>=3.53: ccl=min(ccl,1.5)
      if self._vmax>=3.51: ccl=min(ccl,2)
      if self._vmax>=3.50: ccl=min(ccl,3)
      if self._vmax>=3.49: ccl=min(ccl,4)
      if self._vmax>=3.48: ccl=min(ccl,5)
      if self._vmax>=3.47: ccl=min(ccl,10)
      if self._vmax>=3.46: ccl=min(ccl,15)
      if self._vmax>=3.45: ccl=min(ccl,20)

      if self._vmin<=3.10: dcl=min(dcl,12)
      if self._vmin<=3.00: dcl=min(dcl,7)
      if self._vmin<=2.90: dcl=min(dcl,4)
      if self._vmin<=2.80: dcl=min(dcl,2)
      if self._vmin<=2.70: dcl=0

      if ccl>self._ccl-1: ccl=self._ccl+1

      self._ccl=ccl
      self._dcl=dcl

      if ccl<=0:
        self._dbusservice['/Io/AllowToCharge']=0
      if dcl<=0:
        self._dbusservice['/Io/AllowToDischarge']=0

      self._dbusservice['/Info/BatteryLowVoltage'] = float(v_discharge)/10
      self._dbusservice['/Info/MaxChargeCurrent'] = ccl
      self._dbusservice['/Info/MaxChargeVoltage'] = float(v_charge)/10
      self._dbusservice['/Info/MaxDischargeCurrent'] = dcl


      return

    if msg.arbitration_id == 0x356 : #Voltage / Current / Temp
      if msg.dlc < 6 : return
      v=int.from_bytes([msg.data[0], msg.data[1]], byteorder='little', signed=False)
      c=int.from_bytes([msg.data[2], msg.data[3]], byteorder='little', signed=True)
      t=int.from_bytes([msg.data[4], msg.data[5]], byteorder='little', signed=False)
      self._dbusservice['/Dc/0/Temperature'] = float(t)/10
      self._dbusservice['/Dc/0/Voltage'] = float(v)/100
      self._dbusservice['/Dc/0/Current'] = float(c)/10
      self._dbusservice['/Dc/0/Power'] = float(c)/10 * float(v)/100

      if self._dbusservice['/Dc/0/Voltage']>self._vmax:
        self._vmax=self._dbusservice['/Dc/0/Voltage']
      if self._dbusservice['/Dc/0/Voltage']<self._vmin:
        self._vmin=self._dbusservice['/Dc/0/Voltage']
      #print(v,c,t)
      return

    if msg.arbitration_id == 0x359 : #alarm flags
      if msg.dlc < 7 : return
      b0 = msg.data[0]
      b1 = msg.data[1]
      b2 = msg.data[2]
      b3 = msg.data[3]
      #protection:
      p_discharge_overcurrent = b0 & (1<<7)
      p_cell_undertemp        = b0 & (1<<4)
      p_cell_overtemp         = b0 & (1<<3)
      p_cell_undervolt        = b0 & (1<<2)
      p_cell_overvolt         = b0 & (1<<1)

      p_system_error          = b1 & (1<<3)
      p_charge_overcurrent    = b1 & (1<<0)

      #alarm:
      a_discharge_highcurrent = b2 & (1<<7)
      a_cell_lowtemp          = b2 & (1<<4)
      a_cell_hightemp         = b2 & (1<<3)
      a_cell_lowvolt          = b2 & (1<<2)
      a_cell_highvolt         = b2 & (1<<1)

      a_internal_comm_fail    = b3 & (1<<3)
      a_charge_highcurrent    = b3 & (1<<0)

      num_batteries=int.from_bytes([msg.data[4]], byteorder='little', signed=False)
      self._dbusservice['/System/NrOfModulesOnline']=num_batteries
      
      b5 = msg.data[5] #50
      b6 = msg.data[6] #4e
      
      charge=True
      discharge=True

      #print(b4,b5,b6)
      if p_cell_undervolt:
        self._dbusservice['/Alarms/LowVoltage']=2
        discharge=False
      elif a_cell_lowvolt:
        self._dbusservice['/Alarms/LowVoltage']=1
        discharge=False
      else:
        self._dbusservice['/Alarms/LowVoltage']=0

      if p_cell_overvolt:
        self._dbusservice['/Alarms/HighVoltage']=2
        charge=False
      elif a_cell_highvolt:
        self._dbusservice['/Alarms/HighVoltage']=1
        charge=False
      else:
        self._dbusservice['/Alarms/HighVoltage']=0

      if p_charge_overcurrent:
        self._dbusservice['/Alarms/HighChargeCurrent']=2
        charge=False
      elif a_charge_highcurrent:
        self._dbusservice['/Alarms/HighChargeCurrent']=1
        charge=False
      else:
        self._dbusservice['/Alarms/HighChargeCurrent']=0

      if p_discharge_overcurrent:
        self._dbusservice['/Alarms/HighDischargeCurrent']=2
        discharge=False
      elif a_discharge_highcurrent:
        self._dbusservice['/Alarms/HighDischargeCurrent']=1
        discharge=False
      else:
        self._dbusservice['/Alarms/HighDischargeCurrent']=0

      if p_cell_overtemp:
        self._dbusservice['/Alarms/HighTemperature']=2
        charge=False
        discharge=False
      elif a_cell_hightemp:
        self._dbusservice['/Alarms/HighTemperature']=1
        charge=False
        discharge=False
      else:
        self._dbusservice['/Alarms/HighTemperature']=0

      if p_cell_undertemp:
        self._dbusservice['/Alarms/LowTemperature']=2
        charge=False
      elif a_cell_lowtemp:
        self._dbusservice['/Alarms/LowTemperature']=1
        charge=False
      else:
        self._dbusservice['/Alarms/LowTemperature']=0

      if p_system_error:
        self._dbusservice['/Alarms/InternalFailure']=2
        discharge=False
        charge=False
      elif a_internal_comm_fail:
        self._dbusservice['/Alarms/InternalFailure']=1
        discharge=False
        charge=False
      else:
        self._dbusservice['/Alarms/InternalFailure']=0

      if charge:
        self._dbusservice['/System/NrOfModulesBlockingCharge']=0
      else:
        self._dbusservice['/System/NrOfModulesBlockingCharge']=1

      if discharge:
        self._dbusservice['/System/NrOfModulesBlockingDischarge']=0
      else:
        self._dbusservice['/System/NrOfModulesBlockingDischarge']=1

 
      #self._dbusservice['/Alarms/LowTemperature']=1
      #self._dbusservice['/Connected'] = 1
      return

  def _handlechangedvalue(self, path, value):
    logging.debug("someone else updated %s to %s" % (path, value))
    return True # accept the change

  def _update(self):
    try:
      self._watchdog += 1
      if self._watchdog>10:
        #battery turned off
        self._dbusservice['/Io/AllowToCharge']=0
        self._dbusservice['/Io/AllowToDischarge']=0
        self._dbusservice['/SystemSwitch']=0
        self._dbusservice['/System/NrOfModulesOnline']=0
        logging.error("BMS offline: {}s".format(self._watchdog))

      #self._dbusservice['/Soc'] = 32
      #logging.info("House Consumption: {:.0f}".format(meter_consumption))
    except:
      logging.info("WARNING: Error")

    # increment UpdateIndex - to show that new data is available
    self._dbusservice['/UpdateIndex'] = (self._dbusservice['/UpdateIndex'] + 1 ) % 256
    return True

  def connect_mqtt(self):
      def on_connect(client, userdata, flags, rc):
          if rc == 0:
              print("Connected to MQTT Broker!")
          else:
              print("Failed to connect, return code %d\n", rc)

      self._mqtt_client = mqtt_client.Client("victron-bms")
      self._mqtt_client.username_pw_set("chp", "homegrow")
      self._mqtt_client.on_connect = on_connect
      self._mqtt_client.connect("192.168.0.10", 1883)
      self._mqtt_client.loop_start()
      print("Connecing to MQTT ...")
      return self._mqtt_client

def main():
  logging.basicConfig(level=logging.INFO) # use .INFO for less logging
  thread.daemon = True # allow the program to quit

  subprocess.call(['ip', 'link', 'set', 'can8', 'type' , 'can' , 'bitrate' , '500000'])
  subprocess.call(['ip', 'link', 'set', 'up', 'can8'])

  from dbus.mainloop.glib import DBusGMainLoop
  # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
  DBusGMainLoop(set_as_default=True)

  #formatting 
  _kwh = lambda p, v: (str(round(v, 2)) + ' KWh')
  _a = lambda p, v: (str(round(v, 1)) + ' A')
  _ah = lambda p, v: (str(round(v, 1)) + ' Ah')
  _w = lambda p, v: (str(round(v, 1)) + ' W')
  _v = lambda p, v: (str(round(v, 1)) + ' V')
  _v3 = lambda p, v: (str(round(v, 3)) + ' V')
  _mv = lambda p, v: (str(int(v)) + ' mV')
  _c = lambda p, v: (str(round(v, 1)) + ' °C')
  _p = lambda p, v: (str(round(v, 1)) + ' %')
  _n = lambda p, v: (v)
  _s = lambda p, v: (str(v))
  _paths={
      '/Alarms/CellImbalance': {'initial': None, 'textformat': _n},
      '/Alarms/HighChargeCurrent': {'initial': None, 'textformat': _n},
      '/Alarms/HighChargeTemperature': {'initial': None, 'textformat': _n},
      '/Alarms/HighDischargeCurrent': {'initial': None, 'textformat': _n},
      '/Alarms/HighTemperature': {'initial': None, 'textformat': _n},
      '/Alarms/HighVoltage': {'initial': None, 'textformat': _n},
      '/Alarms/InternalFailure': {'initial': None, 'textformat': _n},
      '/Alarms/LowChargeTemperature': {'initial': None, 'textformat': _n},
      '/Alarms/LowTemperature': {'initial': None, 'textformat': _n},
      '/Alarms/LowVoltage': {'initial': None, 'textformat': _n},

      '/Io/AllowToCharge': {'initial': None, 'textformat': _n},
      '/Io/AllowToDischarge': {'initial': None, 'textformat': _n},
      
      '/Soc': {'initial': None, 'textformat': _p},
      '/Soh': {'initial': None, 'textformat': _p},
      '/Capacity': {'initial': None, 'textformat': _ah},
      '/InstalledCapacity': {'initial': 130, 'textformat': _ah},
      '/SystemSwitch': {'initial': None, 'textformat': _n},

      '/System/MinCellVoltage': {'initial': None, 'textformat': _v3},
      '/System/MinVoltageCellId': {'initial': "n/a", 'textformat': _s},
      '/System/MaxCellVoltage': {'initial': None, 'textformat': _v3},
      '/System/MaxVoltageCellId': {'initial': "n/a", 'textformat': _s},
      '/System/MinCellTemperature': {'initial': None, 'textformat': _c},
      '/System/MinTemperatureCellId': {'initial': "n/a", 'textformat': _s},
      '/System/MaxCellTemperature': {'initial': None, 'textformat': _c},
      '/System/MaxTemperatureCellId': {'initial': "n/a", 'textformat': _s},
      '/System/NrOfCellsPerBattery': {'initial': "16", 'textformat': _n},
      '/System/NrOfModulesOnline': {'initial': 0, 'textformat': _s},
      '/System/NrOfModulesOffline': {'initial': 0, 'textformat': _s},
      '/System/NrOfModulesBlockingCharge': {'initial': 0, 'textformat': _s},
      '/System/NrOfModulesBlockingDischarge': {'initial': 0, 'textformat': _s},
      
      '/Dc/0/Voltage': {'initial': None, 'textformat': _v},
      '/Dc/0/Current': {'initial': None, 'textformat': _a},
      '/Dc/0/Power': {'initial': None, 'textformat': _w},
      '/Dc/0/Temperature': {'initial': None, 'textformat': _c},
      
      '/Info/BatteryLowVoltage': {'initial': None, 'textformat': _v},
      '/Info/MaxChargeCurrent': {'initial': None, 'textformat': _a},
      '/Info/MaxChargeVoltage': {'initial': None, 'textformat': _v},
      '/Info/MaxDischargeCurrent': {'initial': None, 'textformat': _a},
    }

  pvac_output = BMSService(servicename='com.victronenergy.battery.ttyO0',deviceinstance=40,paths=_paths)

  logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
  mainloop = gobject.MainLoop()
  mainloop.run()

if __name__ == "__main__":
  main()
