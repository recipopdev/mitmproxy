from typing import List, Tuple

import h2.connection
import h2.events
import h2.config
import hyperframe.frame
import pytest

from mitmproxy.http import HTTPFlow
from mitmproxy.proxy.protocol.http import HTTPMode
from mitmproxy.proxy2.commands import OpenConnection, SendData
from mitmproxy.proxy2.context import Context, Server
from mitmproxy.proxy2.events import DataReceived
from mitmproxy.proxy2.layers import http
from test.mitmproxy.proxy2.layers.http.hyper_h2_test_helpers import FrameFactory
from test.mitmproxy.proxy2.layers.http.test_http2 import example_request_headers, example_response_headers, make_h2
from test.mitmproxy.proxy2.tutils import Placeholder, Playbook, reply

h2f = FrameFactory()


def event_types(events):
    return [type(x) for x in events]


def h2_client(tctx: Context) -> Tuple[h2.connection.H2Connection, Playbook]:
    tctx.client.alpn = b"h2"

    playbook = Playbook(http.HttpLayer(tctx, HTTPMode.regular))
    conn = h2.connection.H2Connection()
    conn.initiate_connection()

    server_preamble = Placeholder(bytes)
    assert (
            playbook
            << SendData(tctx.client, server_preamble)
    )
    assert event_types(conn.receive_data(server_preamble())) == [h2.events.RemoteSettingsChanged]

    settings_ack = Placeholder(bytes)
    assert (
            playbook
            >> DataReceived(tctx.client, conn.data_to_send())
            << SendData(tctx.client, settings_ack)
    )
    assert event_types(conn.receive_data(settings_ack())) == [h2.events.SettingsAcknowledged]

    return conn, playbook


def test_h2_to_h1(tctx):
    """Test HTTP/2 -> HTTP/1 request translation"""
    server = Placeholder(Server)
    flow = Placeholder(HTTPFlow)

    conn, playbook = h2_client(tctx)

    conn.send_headers(1, example_request_headers, end_stream=True)
    response = Placeholder(bytes)
    assert (
            playbook
            >> DataReceived(tctx.client, conn.data_to_send())
            << http.HttpRequestHeadersHook(flow)
            >> reply()
            << http.HttpRequestHook(flow)
            >> reply()
            << OpenConnection(server)
            >> reply(None)
            << SendData(server, b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
            >> DataReceived(server, b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\n\r\nHello World!")
            << http.HttpResponseHeadersHook(flow)
            >> reply()
            << http.HttpResponseHook(flow)
            >> reply()
            << SendData(tctx.client, response)
    )
    events = conn.receive_data(response())
    assert event_types(events) == [
        h2.events.ResponseReceived, h2.events.DataReceived, h2.events.DataReceived, h2.events.StreamEnded
    ]
    resp: h2.events.ResponseReceived = events[0]
    body: h2.events.DataReceived = events[1]
    assert resp.headers == [(b':status', b'200'), (b'content-length', b'12')]
    assert body.data == b"Hello World!"


@pytest.mark.xfail
def test_h1_to_h2(tctx):
    """Test HTTP/1 -> HTTP/2 request translation"""
    server = Placeholder(Server)
    flow = Placeholder(HTTPFlow)

    playbook = Playbook(http.HttpLayer(tctx, HTTPMode.regular))

    conf = h2.config.H2Configuration(client_side=False)
    conn = h2.connection.H2Connection(conf)
    conn.initiate_connection()

    h2_preamble = Placeholder(bytes)
    assert (
            playbook
            >> DataReceived(tctx.client, b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n")
            << http.HttpRequestHeadersHook(flow)
            >> reply()
            << http.HttpRequestHook(flow)
            >> reply()
            << OpenConnection(server)
            >> reply(None, side_effect=make_h2)
            << SendData(server, h2_preamble)
    )
    events = conn.receive_data(h2_preamble())
    y = h2_preamble()

    assert not events  # FIXME

    request = Placeholder(bytes)
    assert (
            playbook
            >> DataReceived(server, conn.data_to_send())
            << http.HttpResponseHeadersHook(flow)
            >> reply()
            << http.HttpResponseHook(flow)
            >> reply()
            << SendData(tctx.client, b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\n\r\nHello World!")
    )