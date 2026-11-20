import os
import socket
from http.client import HTTPSConnection
from urllib.request import HTTPSHandler, ProxyHandler, build_opener

from .config import AgentError


def open_on_interface(request, timeout: int, interface: str):
    if not hasattr(socket, "SO_BINDTODEVICE"):
        raise AgentError(
            "Привязка к сетевому интерфейсу поддерживается только в Linux."
        )
    try:
        socket.if_nametoindex(interface)
    except OSError:
        raise AgentError("Интерфейс из ROUTERAI_NETWORK_INTERFACE не найден.") from None
    device = os.fsencode(interface) + b"\0"

    def connect(address, timeout, source_address=None):
        host, port = address
        last_error = OSError("У хоста нет доступных сетевых адресов.")
        for family, kind, protocol, _, destination in socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        ):
            connection = socket.socket(family, kind, protocol)
            try:
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, device)
                connection.settimeout(timeout)
                if source_address:
                    connection.bind(source_address)
                connection.connect(destination)
                return connection
            except OSError as error:
                last_error = error
                connection.close()
        raise last_error

    def factory(host, **options):
        connection = HTTPSConnection(host, **options)
        connection._create_connection = connect
        return connection

    class InterfaceHTTPSHandler(HTTPSHandler):
        def https_open(self, request):
            return self.do_open(factory, request, context=self._context)

    return build_opener(ProxyHandler({}), InterfaceHTTPSHandler()).open(
        request, timeout=timeout
    )
