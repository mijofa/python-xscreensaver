#!/usr/bin/env python3
# FIXME: Currently this only activate/deactivate & lock, and maybe some status querying.
#
#        I would like to improve this in future to implement xscreensaver-command's -watch functionality
#        NOTE: xscreensaver-command.c did this with what looks like simply a "while true: GetActiveTime()" loop.

import sys
import time

# FIXME: Xlib is obsolete and should be replaced.
#        I guess technically it'd be replaced by DBus,
#        so maybe it's completely valid for me to use it here while xscreensaver doesn't natively support dbus?
import Xlib.Xatom
import Xlib.display
import Xlib.protocol


# FIXME: Turn this into its own "xscreensaver_command" library and import that.
class XSS_worker():
    timeout_source_id = None

    def __init__(self):
        self.inhibitors = {}  # Must be set in the __init__ function because of list immutability

        self.display = Xlib.display.Display()

        ## Find the xscreensaver window.
        screensavers = [child for child in self.display.screen().root.query_tree().children
                        if child.get_full_property(self.display.intern_atom("_SCREENSAVER_VERSION", False), Xlib.Xatom.STRING)]
        # FIXME: Use actual exceptions
        ## Actually we can have multiple screensaver windows because there's 1 for each output display.
        ## xscreensaver-command stops at the first one it finds, so we'll do the same.
        # assert not len(screensavers) > 1, "Can't have multiple screensaver windows!"
        assert not len(screensavers) < 1, "No screensaver window found. Is there a screensaver running?"
        # Don't actually want a list, it was just the easiest way to loop over the query_tree and assert only 1 window
        self.xss_window = screensavers[0]

        ## Set the event_mask se that responses can be caught
        self.xss_window.change_attributes(event_mask=Xlib.X.PropertyChangeMask)

    def _get_xscreensaver_response(self):
        # NOTE: I've already set the necessary event mask for the xscreensaver window object to include Xlib.X.PropertyChangeMask
        response = None  # So the assert below actually triggers rather than a UnboundLocalError
        timeout = time.monotonic() + 1
        while time.monotonic() < timeout:  # If there hasn't been a response in 1 second, there won't be one
            if self.display.pending_events():
                ev = self.display.next_event()
                if ev.type == Xlib.X.PropertyNotify and \
                   ev.state == Xlib.X.PropertyNewValue and \
                   ev.atom == self.display.intern_atom("_SCREENSAVER_RESPONSE", False):
                        # NOTE: The C code accepts AnyPropertyType, not just Strings, I'm being more defensive here.
                        # FIXME: Can there be multiple responses all at once? Should we wait the whole second and add them all up?
                        # FIXME: Can I just get the property info from the event object?
                        response = ev.window.get_full_property(
                            self.display.intern_atom("_SCREENSAVER_RESPONSE", False),
                            Xlib.Xatom.STRING)
                        break
        assert response, "No response recieved"
        return response.value

    def get_active(self):
        status = self.display.screen().root.get_full_property(
            self.display.intern_atom("_SCREENSAVER_STATUS", False), Xlib.Xatom.INTEGER).value
        blanked = status[0]
        # tt = status[1]  # Something to do with the time since blanked/unblanked, not implemented here yet
        if blanked in (self.display.intern_atom("BLANK", False), self.display.intern_atom("LOCK", False)):
            return True
        else:
            return False

    def _send_command(self, atom_name):
        Xevent = Xlib.protocol.event.ClientMessage(
            display=self.display,
            window=self.xss_window,
            client_type=self.display.intern_atom("SCREENSAVER", False),
            # In the C code the last [0, 0] happened implicitly, Python's xlib doesn't cope well with them being left out though.
            # The first [0, 0] was set according to certain other arguments, but for DEACTIVATE was always [0, 0]
            data=(32, [self.display.intern_atom(atom_name, False), 0, 0, 0, 0]),
        )
        self.display.send_event(destination=Xevent.window,
                                propagate=False,
                                event_mask=0,
                                event=Xevent,
                                # FIXME: Should raise an exception here
                                onerror=lambda err: print('ERROR:', err, file=sys.stderr, flush=True))

        return self._get_xscreensaver_response()

    def activate(self):
        """
        Tell xscreensaver to turn on immediately (that is, blank the screen, as
        if the user had been idle for long enough.) The screensaver will
        deactivate as soon as there is any user activity, as usual.
        """
        self._send_command("ACTIVATE")

    def deactivate(self):
        """
        This tells xscreensaver to pretend that there has just been user
        activity. This means that if the screensaver is active (the screen is
        blanked), then this command will cause the screen to un-blank as if
        there had been keyboard or mouse activity. If the screen is locked,
        then the password dialog will pop up first, as usual. If the screen is
        not blanked, then this simulated user activity will re-start the
        countdown (so, issuing the -deactivate command periodically is one way
        to prevent the screen from blanking.)
        """
        self._send_command("DEACTIVATE")

    def lock(self):
        """
        Tells the running xscreensaver process to lock the screen immediately.
        This is like -activate, but forces locking as well, even if locking is
        not the default (that is, even if xscreensaver's lock resource is
        false, and even if the lockTimeout resource is non-zero.)
        """
        self._send_command("LOCK")
