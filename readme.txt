weewx-twi

This is a driver for weewx that collects data from Texas Weather Instruments
hardware.

Installation

0) install weewx (see the weewx user guide)

1) download the driver

wget -O weewx-twi.zip https://github.com/matthewwall/weewx-twi/archive/master.zip

2) install the driver

wee_extension --install weewx-twi.zip

3) configure the driver

wee_config --reconfigure

4) start weewx

sudo /etc/init.d/weewx start
