import socket
import uuid


class UniqueComputerUtils:


    def get_host_name(self):
        hostname = socket.gethostname()
        return hostname


    def get_mac_address(self):
        mac=uuid.getnode()
        mac_address = ':'.join(('%012X' % mac)[i:i + 2] for i in range(0, 12, 2))
        return mac_address