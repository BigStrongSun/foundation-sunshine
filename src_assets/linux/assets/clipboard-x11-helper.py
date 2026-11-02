#!/usr/bin/python3
"""Isolated GTK X11 clipboard owner. Private bounded IPC, never logs contents."""
import os
import struct
import sys
import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

LIMIT = 50 * 1024 * 1024  # clipboard_blob_store::kMaxBlobBytes
os.environ['GDK_BACKEND'] = 'x11'
ok, _ = Gtk.init_check([])
if not ok:
    sys.exit(1)
clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
incoming = bytearray()
outgoing = bytearray()
write_watch = None
generation = 0
owned = False
last_sent = None
os.set_blocking(0, False)
os.set_blocking(1, False)


def drain(_fd, condition):
    global write_watch
    if condition & (GLib.IO_HUP | GLib.IO_ERR):
        Gtk.main_quit()
        return False
    try:
        n = os.write(1, outgoing)
        del outgoing[:n]
    except BlockingIOError:
        pass
    if not outgoing:
        write_watch = None
        return False
    return True


def emit(kind, data):
    global write_watch, last_sent
    if not data or len(data) > LIMIT or last_sent == (kind, data):
        return
    last_sent = (kind, data)
    # If the parent stops reading, bound memory and stop rather than blocking GTK.
    if len(outgoing) + len(data) + 5 > LIMIT + 5:
        Gtk.main_quit()
        return
    outgoing.extend(kind + struct.pack('!I', len(data)) + data)
    if write_watch is None:
        write_watch = GLib.io_add_watch(1, GLib.IO_OUT | GLib.IO_HUP | GLib.IO_ERR, drain)


def changed(_clipboard, event):
    global generation, owned
    generation += 1
    current = generation
    # GTK's selection window is the owner when it serves our latest write.
    owner = Gdk.selection_owner_get(Gdk.SELECTION_CLIPBOARD)
    if owner is not None and owner.get_user_data():
        owned = True
        return
    owned = False

    def text_received(_cb, text, _data):
        if current == generation and not owned and text and '\0' not in text:
            emit(b'T', text.encode('utf-8'))

    def png_received(_cb, selection, _data):
        if current != generation or owned:
            return
        data = selection.get_data()
        if data and bytes(data).startswith(b'\x89PNG\r\n\x1a\n'):
            emit(b'P', bytes(data))

    clipboard.request_text(text_received, None)
    clipboard.request_contents(Gdk.Atom.intern('image/png', False), png_received, None)


def receive(_fd, condition):
    global owned, generation, last_sent
    try:
        chunk = os.read(0, 65536)
    except BlockingIOError:
        return True
    if not chunk:
        Gtk.main_quit()
        return False
    incoming.extend(chunk)
    while len(incoming) >= 5:
        kind = incoming[:1]
        size = struct.unpack('!I', incoming[1:5])[0]
        if size > LIMIT or kind not in (b'T', b'P'):
            Gtk.main_quit()
            return False
        if len(incoming) < size + 5:
            break
        data = bytes(incoming[5:5 + size])
        del incoming[:5 + size]
        generation += 1
        owned = True
        last_sent = (bytes(kind), data)
        try:
            if kind == b'T':
                text = data.decode('utf-8')
                if not text or '\0' in text:
                    continue
                clipboard.set_text(text, -1)
            else:
                if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                    continue
                loader = GdkPixbuf.PixbufLoader.new_with_type('png')
                too_large = []

                def size_prepared(decoder, width, height):
                    if width <= 0 or height <= 0 or width * height > 32 * 1024 * 1024:
                        too_large.append(True)
                        decoder.set_size(1, 1)

                loader.connect('size-prepared', size_prepared)
                loader.write(data)
                loader.close()
                image = loader.get_pixbuf()
                if image is not None and not too_large:
                    clipboard.set_image(image)
        except (UnicodeError, GLib.Error):
            continue
    return True


clipboard.connect('owner-change', changed)
Gdk.Display.get_default().request_selection_notification(Gdk.SELECTION_CLIPBOARD)
GLib.io_add_watch(0, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, receive)
os.write(1, b'R\0\0\0\0')
Gtk.main()
