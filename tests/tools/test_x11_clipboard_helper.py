#!/usr/bin/python3
"""Run against a private Xvfb display, never the user's desktop clipboard."""
import os
import select
import struct
import subprocess
import sys
import time
import unittest
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

HELPER = sys.argv.pop(1)


class ClipboardHelperTest(unittest.TestCase):
    def setUp(self):
        self.clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        command = ['/usr/bin/python3', HELPER] if HELPER.endswith('.py') else [HELPER]
        self.p = subprocess.Popen(command, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.buffer = bytearray()
        self.assertEqual(self.read_frame(), (b'R', b''))

    def tearDown(self):
        self.p.stdin.close()
        try:
            self.p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.fail('helper did not stop on parent disconnect')
        errors = self.p.stderr.read().decode()
        self.assertNotIn('Traceback', errors)
        self.assertEqual(self.p.returncode, 0, errors)
        self.p.stdout.close()
        self.p.stderr.close()

    def read_frame(self, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            if len(self.buffer) >= 5:
                n = struct.unpack('!I', self.buffer[1:5])[0]
                if len(self.buffer) >= n + 5:
                    result = bytes(self.buffer[:1]), bytes(self.buffer[5:n+5])
                    del self.buffer[:n+5]
                    return result
            if select.select([self.p.stdout], [], [], 0.01)[0]:
                data = os.read(self.p.stdout.fileno(), 65536)
                if not data:
                    raise AssertionError('helper disconnected: ' + self.p.stderr.read().decode())
                self.buffer.extend(data)
        raise TimeoutError('clipboard event timeout')

    def send(self, kind, data, fragment=False):
        packet = kind + struct.pack('!I', len(data)) + data
        if fragment:
            for b in packet[:5]:
                self.p.stdin.write(bytes([b])); self.p.stdin.flush()
            packet = packet[5:]
        self.p.stdin.write(packet); self.p.stdin.flush()
        # Service GTK until the other process has taken selection ownership.
        until = time.monotonic() + 0.3
        while time.monotonic() < until:
            while Gtk.events_pending(): Gtk.main_iteration_do(False)
            time.sleep(0.005)

    def test_bidirectional_unicode_and_fragmented_header(self):
        text = 'X11 test 中文 αβ 😀\nsecond line'
        self.clip.set_text(text, -1)
        self.assertEqual(self.read_frame(), (b'T', text.encode()))
        self.send(b'T', 'client → host 中文'.encode(), fragment=True)
        self.assertEqual(self.clip.wait_for_text(), 'client → host 中文')
        with self.assertRaises(TimeoutError): self.read_frame(timeout=0.3)
        self.clip.set_text('host copied again', -1)
        self.assertEqual(self.read_frame(), (b'T', b'host copied again'))

    def test_png_and_large_incr_transfer(self):
        # Incompressible PNG exercises X11 INCR rather than a tiny selection.
        pixels = GLib.Bytes.new(os.urandom(1024 * 1024 * 3))
        image = GdkPixbuf.Pixbuf.new_from_bytes(pixels, GdkPixbuf.Colorspace.RGB,
                                              False, 8, 1024, 1024, 3072)
        self.clip.set_image(image)
        kind, png = self.read_frame(timeout=15)
        self.assertEqual(kind, b'P')
        self.assertGreater(len(png), 1024 * 1024)
        self.send(b'P', png)
        result = self.clip.wait_for_image()
        self.assertIsNotNone(result)
        self.assertEqual(result.get_pixels(), image.get_pixels())
        with self.assertRaises(TimeoutError): self.read_frame(timeout=0.3)

    def test_invalid_utf8_does_not_crash(self):
        self.send(b'T', b'\xff\xfe')
        self.send(b'T', b'valid after invalid')
        self.assertEqual(self.clip.wait_for_text(), 'valid after invalid')

    def test_oversized_frame_is_rejected(self):
        self.p.stdin.write(b'T' + struct.pack('!I', 50 * 1024 * 1024 + 1))
        self.p.stdin.flush()
        self.assertEqual(self.p.wait(timeout=3), 0)


if __name__ == '__main__':
    Gtk.init([])
    unittest.main()
