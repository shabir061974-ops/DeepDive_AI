"""CA selection and TLS verification regressions; all sockets/handshakes mocked."""

import socket
import ssl
import unittest
from unittest.mock import Mock, patch

import certifi

from deepdive_ai.http_transport import PublicHTTPTransport, _PinnedConnection
from deepdive_ai.mesh import MeshAPIClient, MeshSettings
from deepdive_ai.search import BraveSearchDiscovery, BraveSearchSettings


class TLSBundleTests(unittest.TestCase):
    def test_provider_hosts_use_certifi_and_verified_contexts_with_original_sni(self):
        create_context = ssl.create_default_context
        for host in ("api.search.brave.com", "api.meshapi.ai"):
            contexts = []

            def context_factory(*args, **kwargs):
                context = create_context(*args, **kwargs)
                contexts.append(context)
                return context

            raw, secure = Mock(), Mock()
            with self.subTest(host=host), patch("socket.socket", return_value=raw) as sockets, \
                    patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")), \
                    patch("ssl.create_default_context", side_effect=context_factory) as factory, \
                    patch.object(ssl.SSLContext, "wrap_socket", return_value=secure) as wrap:
                connection = _PinnedConnection(host, 443, "8.8.8.8", 2, True)
                connection.connect()
            factory.assert_called_once_with(cafile=certifi.where())
            self.assertEqual(contexts[0].verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(contexts[0].check_hostname)
            sockets.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
            raw.connect.assert_called_once_with(("8.8.8.8", 443))
            wrap.assert_called_once_with(raw, server_hostname=host)
            self.assertIs(connection.sock, secure)

    def test_certificate_failure_closes_socket_without_retry_or_insecure_fallback(self):
        raw = Mock()
        create_context = ssl.create_default_context
        with patch("socket.socket", return_value=raw), \
                patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")), \
                patch("ssl.create_default_context", wraps=create_context) as factory, \
                patch.object(ssl.SSLContext, "wrap_socket", side_effect=ssl.SSLCertVerificationError("offline failure")) as wrap:
            with self.assertRaises(ssl.SSLCertVerificationError):
                _PinnedConnection("api.search.brave.com", 443, "8.8.8.8", 2, True).connect()
        factory.assert_called_once_with(cafile=certifi.where())
        wrap.assert_called_once()
        raw.close.assert_called_once()
        raw.sendall.assert_not_called()

    def test_missing_bundle_fails_closed_without_system_store_fallback(self):
        raw = Mock()
        with patch("socket.socket", return_value=raw), \
                patch("ssl.create_default_context", side_effect=FileNotFoundError("offline missing bundle")) as factory, \
                patch.object(ssl.SSLContext, "wrap_socket") as wrap:
            with self.assertRaises(FileNotFoundError):
                _PinnedConnection("api.meshapi.ai", 443, "8.8.8.8", 2, True).connect()
        factory.assert_called_once_with(cafile=certifi.where())
        wrap.assert_not_called()
        raw.close.assert_called_once()
        raw.sendall.assert_not_called()

    def test_existing_plain_http_path_does_not_create_a_tls_context(self):
        raw = Mock()
        with patch("socket.socket", return_value=raw), patch("ssl.create_default_context") as factory, \
                patch("deepdive_ai.http_transport.certifi.where") as bundle:
            connection = _PinnedConnection("fixture.invalid", 80, "8.8.8.8", 2, False)
            connection.connect()
        factory.assert_not_called()
        bundle.assert_not_called()
        self.assertIs(connection.sock, raw)

    def test_both_default_adapters_share_the_protected_transport_without_startup_io(self):
        with patch("socket.socket", side_effect=AssertionError("No connection permitted")), \
                patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")), \
                patch("ssl.create_default_context") as factory:
            mesh = MeshAPIClient(MeshSettings("offline-placeholder"))
            brave = BraveSearchDiscovery(BraveSearchSettings("offline-placeholder"))
        self.assertIsInstance(mesh._http.transport, PublicHTTPTransport)
        self.assertIsInstance(brave._http.transport, PublicHTTPTransport)
        factory.assert_not_called()
