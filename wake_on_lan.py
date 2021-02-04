import asyncio
import pathlib
import socket
import warnings

import settings


class DatagramClient:
    """
    Representation of a Datagram socket attached via either bind() or
    connect() returned to consumers of this module.  Provides simple
    wrappers around sending and receiving bytes.

    Due to the stateless nature of datagram protocols, errors are not
    immediately available to this class at the point an action was performed
    that will generate it.  Rather, successive calls will raise exceptions if
    there are any.  Checking for exceptions can be done explicitly by using the
    exception property.

    For instance, failure to connect to a remote endpoint will not be noticed
    until some point in time later, at which point ConnectionRefused will be
    raised.
    """

    def __init__(self, transport, recvq, excq, drained):
        """
        @param transport    - asyncio transport
        @param recvq        - asyncio queue that gets populated by the
                              DatagramProtocol with received datagrams.
        @param excq         - asyncio queue that gets populated with any errors
                              detected by the DatagramProtocol.
        @param drained      - asyncio event that is unset when writing is
                              paused and set otherwise.
        """
        self._transport = transport
        self._recvq = recvq
        self._excq = excq
        self._drained = drained

    def __del__(self):
        self._transport.close()

    @property
    def exception(self):
        """
        If the underlying protocol detected an error, raise the first
        unconsumed exception it noticed, otherwise returns None.
        """
        try:
            exc = self._excq.get_nowait()
            raise exc
        except asyncio.queues.QueueEmpty:
            pass

    @property
    def sockname(self):
        """
        The associated socket's own address
        """
        r = self._transport.get_extra_info("sockname")
        return None if r == "" else r

    @property
    def peername(self):
        """
        The address the associated socket is connected to
        """
        r = self._transport.get_extra_info("peername")
        return None if r == "" else r

    @property
    def socket(self):
        """
        The socket instance used by the stream.  In python <3.8 this is a
        socket.socket instance, after it is an asyncio.TransportSocket
        instance.
        """
        return self._transport.get_extra_info("socket")

    def close(self):
        """
        Close the underlying transport.
        """
        self._transport.close()

    async def send(self, data, addr=None):
        """
        @param data - bytes to send
        @param addr - remote address to send data to, if unspecified then the
                      underlying socket has to have been been connected to a
                      remote address previously.
        """
        _ = self.exception
        self._transport.sendto(data, addr)
        await self._drained.wait()

    async def recv(self):
        """
        Receive data on the local socket.

        @return - tuple of the bytes received and the address (ip, port) that
                  the data was received from.
        """
        _ = self.exception
        data, addr = await self._recvq.get()
        return data, addr


class Protocol(asyncio.DatagramProtocol):
    """
    asyncio.DatagramProtocol for feeding received packets into the
    Datagram{Client,Server} which handles converting the lower level callback
    based asyncio into higher level coroutines.
    """

    def __init__(self, recvq, excq, drained):
        """
        @param recvq    - asyncio.Queue for new datagrams
        @param excq     - asyncio.Queue for exceptions
        @param drained  - asyncio.Event set when the write buffer is below the
                          high watermark.
        """
        self._recvq = recvq
        self._excq = excq
        self._drained = drained

        self._drained.set()

        # Transports are connected at the time a connection is made.
        self._transport = None

    def connection_made(self, transport):
        if self._transport is not None:
            old_peer = self._transport.get_extra_info("peername")
            new_peer = transport.get_extra_info("peername")
            warnings.warn(
                "Reinitializing transport connection from %s to %s", old_peer, new_peer
            )

        self._transport = transport

    def connection_lost(self, exc):
        if exc is not None:
            self._excq.put_nowait(exc)

        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def datagram_received(self, data, addr):
        self._recvq.put_nowait((data, addr))

    def error_received(self, exc):
        self._excq.put_nowait(exc)

    def pause_writing(self):
        self._drained.clear()
        super().pause_writing()

    def resume_writing(self):
        self._drained.set()
        super().resume_writing()


async def connect(addr):
    """
    Connect a socket to a remote address for datagrams.  The socket will be
    either AF_INET, AF_INET6 or AF_UNIX depending upon the type of host
    specified.

    @param addr - For AF_INET or AF_INET6, a tuple with the the host and port to
                  to connect to.
                  For AF_UNIX the path at which to connect (with a leading \0
                  for abstract sockets).
    @return     - A DatagramClient instance
    """
    loop = asyncio.get_event_loop()
    recvq = asyncio.Queue()
    excq = asyncio.Queue()
    drained = asyncio.Event()

    if not isinstance(addr, tuple):
        family = socket.AF_UNIX
        if isinstance(addr, pathlib.Path):
            addr = str(addr)
    else:
        family = 0

    transport, protocol = await loop.create_datagram_endpoint(
        lambda: Protocol(recvq, excq, drained),
        remote_addr=addr,
        family=family,
        allow_broadcast=True,
    )

    return DatagramClient(transport, recvq, excq, drained)


def create_magic_packet(macaddress):
    """
    Create a magic packet.
    A magic packet is a packet that can be used with the for wake on lan
    protocol to wake up a computer. The packet is constructed from the
    mac address given as a parameter.
    Args:
        macaddress (str): the mac address that should be parsed into a
            magic packet.
    """
    if len(macaddress) == 17:
        sep = macaddress[2]
        macaddress = macaddress.replace(sep, "")
    elif len(macaddress) != 12:
        raise ValueError("Incorrect MAC address format")

    return bytes.fromhex("F" * 12 + macaddress * 16)


async def send_packet(macaddress):
    message = create_magic_packet(macaddress)
    client = await connect((settings.BROADCAST_ADDRESS, 9))
    await client.send(message)
    client.close()
