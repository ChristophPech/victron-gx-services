/opt/victronenergy/swupdate-scripts/resize2fs.sh

opkg update
opkg upgrade
opkg install python3
opkg install python3-dev
opkg install python3-pip
pip3 install --upgrade pip
opkg install binutils
opkg install packagegroup-core-buildessential
pip3 install python-can

