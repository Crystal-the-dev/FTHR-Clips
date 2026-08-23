"""Local, one-way activation messages for an existing FTHR instance.

The mutex in :mod:`core.single_instance` remains the ownership authority.
This module only lets a second launcher ask that owner to restore its window;
it cannot start capture, save files, or alter settings.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


SERVER_NAME = 'FTHRClips_Activation_v1'
_ACTIVATE = b'activate\n'


class InstanceActivationServer(QObject):
    """Receive bounded local ``activate`` requests on the Qt event loop."""

    activation_requested = Signal()

    def __init__(self, name: str = SERVER_NAME, parent=None):
        super().__init__(parent)
        self._name = name
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._consume_pending_connections)

    def start(self) -> bool:
        # The mutex has already been acquired before this is called, therefore
        # no healthy FTHR instance owns this endpoint.  Removing only this
        # fixed endpoint clears a server left behind by a crash.
        QLocalServer.removeServer(self._name)
        if not self._server.listen(self._name):
            print(f'[Lifecycle] Instance activation server unavailable: '
                  f'{self._server.errorString()}')
            return False
        print('[Lifecycle] InstanceActivationReady')
        return True

    def stop(self) -> None:
        if self._server.isListening():
            self._server.close()
        QLocalServer.removeServer(self._name)

    def _consume_pending_connections(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            socket.readyRead.connect(
                lambda peer=socket: self._consume_socket(peer))
            socket.disconnected.connect(socket.deleteLater)
            # A fast second process can write before this slot is connected.
            # Consume already-buffered bytes as well as future readyRead.
            if socket.bytesAvailable():
                self._consume_socket(socket)

    def _consume_socket(self, socket: QLocalSocket) -> None:
        payload = bytes(socket.readAll()).strip()
        if payload == _ACTIVATE.strip():
            self.activation_requested.emit()
        socket.disconnectFromServer()


def request_existing_instance_activation(name: str = SERVER_NAME,
                                         timeout_ms: int = 350) -> bool:
    """Ask a running FTHR process to restore its window, never blocking long."""

    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(timeout_ms):
        return False
    if socket.write(_ACTIVATE) != len(_ACTIVATE):
        socket.abort()
        return False
    # Give the owner event loop a bounded chance to accept the pipe before the
    # short-lived launcher disconnects.  The boolean is intentionally not an
    # acknowledgement protocol: successful pipe connection plus a buffered
    # local write is enough for this non-sensitive, idempotent activation.
    socket.flush()
    socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    return True
