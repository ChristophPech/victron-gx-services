# victron-gx-services

virtual venus-os services:
 * gridmeter - simulates a victron smart meter from querying a tasmota
 * solarmeter - simulates a solarinverter from querying several homeassistant states
 * bms - simulates a BMS from a chinese CAN battery with pylontech protocol (somehow the pylontech protocol between the battery and the gx only works half, so I manually translated everything for the gx)


# installation

- copy one or more of the three module directories to /data
- modify the python files to match your network addresses etc.
- copy or merge the data/rc.local to /data/rc.local and remove the unneeded services

For the BMS you also need python-can
```
opkg update
opkg install python3-pip
pip3 install python-can
```

![Battery](https://github.com/ChristophPech/victron-gx-services/blob/main/doc/Battery.png?raw=true)
![Details](https://github.com/ChristophPech/victron-gx-services/blob/main/doc/Details.png?raw=true)
![IO](https://github.com/ChristophPech/victron-gx-services/blob/main/doc/IO.png?raw=true)
![Alarms](https://github.com/ChristophPech/victron-gx-services/blob/main/doc/Alarms.png?raw=true)
