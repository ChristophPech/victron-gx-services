/opt/victronenergy/swupdate-scripts/resize2fs.sh

opkg update
opkg install python3
opkg install python3-pip
pip3 install --upgrade pip
pip3 install python-dev
pip3 install binutils packagegroup-core-buildessential
pip3 install python-can

