# installer for twi driver
# Copyright 2016 Matthew Wall

from weecfg.extension import ExtensionInstaller

def loader():
    return TWIInstaller()

class TWIInstaller(ExtensionInstaller):
    def __init__(self):
        super(TWIInstaller, self).__init__(
            version="0.4",
            name='twi',
            description='Collect data from Texas Weather Instruments hardware',
            author="Matthew Wall",
            author_email="mwall@users.sourceforge.net",
            files=[('bin/user', ['bin/user/twi.py'])]
            )
