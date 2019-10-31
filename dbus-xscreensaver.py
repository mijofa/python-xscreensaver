#!/usr/bin/env python3
# Replace light-locker DBus service to call xscreensaver, based on the scripts here:
# https://github.com/quequotion/pantheon-bzr-qq/tree/master/EXTRAS/xscreensaver-dbus-screenlock

# The reason I've targetted light-locker instead of gnome-screensaver is because
# * org.freedesktop.ScreenSaver seemed a little more "standard" than org.gnome.ScreenSaver
# * dbus-monitoring Chrome indicated it only targets org.freedesktop.ScreenSaver and that's the main thing I care about.

# FIXME: Currently this only allows controlling xscreensaver, and maybe some status querying.
#        It does NOT support telling DBus when Xscreensaver state updates
#
#        I would like to improve this in future to implement xscreensaver-command's -watch functionality
#        and emit DBus messages accordingly
#        NOTE: xscreensaver-command.c did this with what looks like simply a "while true: GetActiveTime()" loop.

# FIXME: Facebook's gifs are played using the <video> element, which causes Chrome to repeatedly inhibit the screensaver.
#        Only solution I can think of for this would be to just not start the inhibitor process until 30-ish seconds after Chrome triggers it.
#        This is an ugly solution, but I can't think of any better.

import random
import sys
import time
import psutil

# FIXME: Can gi.repository.DBus get the same functionality?
#        Should I use that to reduce dependencies?
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

# FIXME: Xlib is obsolete and should be replaced.
#        I guess technically it'd be replaced by DBus,
#        so maybe it's completely valid for me to use it here as a compatibility layer
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

    def send_command(self, atom_name):
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

    def add_inhibitor(self, inhibitor_id: int, caller: dbus.String, reason: dbus.String, caller_process: psutil.Process):
        assert inhibitor_id not in self.inhibitors, "Already working on that inhibitor"
        self.inhibitors.update({inhibitor_id: {'caller': caller, 'reason': reason, 'caller_process': caller_process}})
        print('Inhibitor requested by "{caller}" ({process_name}) for reason "{reason}". Given ID {ID}'.format(
                  caller=caller, reason=reason, ID=inhibitor_id, process_name=caller_process.name()),  # noqa: E126
              file=sys.stderr, flush=True)
        if self.timeout_source_id is None:
            # AIUI the minimum xscreensaver timeout is 60s, so poke it every 50s.
            # NOTE: This is exactly what xdg-screensaver does
            # UPDATE: Changed to 30 seconds because there was some (very rare) circumstances were it skipped 1 poke
            self.timeout_source_id = GLib.timeout_add_seconds(30, self._inhibitor_func)
            # Because of Steam (at least) being stupid and constantly Inhibitting then UnInhibiting,
            # I'm not going to poke the screensaver immediatly because I don't want it to happen before the UnInhibit
            # # GObject's first run will be after the timeout has run once,
            # # so run it once immediately as well
            # self._inhibitor_func()
            # FIXME: Add support for ignoring certain apps and reasons, mostly because of ^ that Steam shit.

    def del_inhibitor(self, inhibitor_id):
        assert inhibitor_id in self.inhibitors, "Already removed that inhibitor"
        print('Removed inhibitor for "{caller}" with ID {ID}'.format(
            caller=self.inhibitors.pop(inhibitor_id)['caller'], ID=inhibitor_id), file=sys.stderr, flush=True)
        if len(self.inhibitors) == 0 and self.timeout_source_id is not None:
            print('Stopping inhibitor timeout')
            GLib.remove(self.timeout_source_id)
            self.timeout_source_id = None

    def _inhibitor_func(self):
        # This for loop must run on a copy of the dict so that it can pop things from the original dict.
        # Otherwise the for loop crashes with "RuntimeError: dictionary changed size during iteration"
        for inhibitor_id in self.inhibitors.copy():
            # NOTE: psutil confirms the pid hasn't been reused, so don't need to worry about that.
            if not self.inhibitors[inhibitor_id]['caller_process'].is_running():
                print("Inhibitor {inhibitor_id} ({caller}) died without uninhibiting, killing inhibitor".format(
                      inhibitor_id=inhibitor_id, caller=self.inhibitors[inhibitor_id]['caller']))
                self.inhibitors.pop(inhibitor_id)

        if len(self.inhibitors) == 0:
            print("Inhibitors finished")
            self.timeout_source_id = None
            return GLib.SOURCE_REMOVE
        else:
            if self.get_active():
                # Screen currently locked/blanked, don't poke it.
                # FIXME: Perhaps should also invalidate all active inhibitors?
                pass
            else:
                print("Poking screensaver for inhibitors:",
                      ', '.join([i['caller'] for i in self.inhibitors.values()]),
                      file=sys.stderr, flush=True)
                response = self.send_command("DEACTIVATE")
                if response != '+not active: idle timer reset.':
                    print("XSS response:", response, file=sys.stderr, flush=True)
            return GLib.SOURCE_CONTINUE


class DBusListener(dbus.service.Object):
    def __init__(self, action_handler):
        self.action_handler = action_handler

        session_bus = dbus.SessionBus()
        # FIXME: Also trigger for org.gnome.ScreenSaver
        bus_name = dbus.service.BusName("org.freedesktop.ScreenSaver", bus=session_bus)
        # FIXME: Also trigger for /org/gnome/ScreenSaver
        super().__init__(bus_name, '/org/freedesktop/ScreenSaver')

        # The only way I could find to get the process ID (or any useful info) of the dbus caller was to make a separate dbus call.
        # This is just to avoid needing to initialise another bus connection, etc.
        self._get_procid = session_bus.get_object('org.freedesktop.DBus', '/').GetConnectionUnixProcessID

    @dbus.service.method("org.freedesktop.ScreenSaver")
    def GetActive(self):
        """Query the state of the locker"""
        return dbus.Boolean(self.action_handler.get_active())

    @dbus.service.method("org.freedesktop.ScreenSaver")
    def GetActiveTime(self):
        """Query the length of time the locker has been active"""
        # xscreenssaver-command -time
        pass

    @dbus.service.method("org.freedesktop.ScreenSaver")
    def GetSessionIdleTime(self):
        """Query the idle time of the locker"""
        # Doesn't have it's own dedicated light-locker-command argument,
        # but gets called instead of GetActiveTime when GetActive returns False

        # xscreenssaver-command -time ?
        pass

    @dbus.service.method("org.freedesktop.ScreenSaver")
    def Lock(self):
        """Tells the running locker process to lock the screen immediately"""
        # xscreenssaver-command -lock
        self.action_handler.send_command("LOCK")

    @dbus.service.method("org.freedesktop.ScreenSaver")
    def SetActive(self, activate):
        """Blank or unblank the screensaver"""
        # xscreensaver-command -deactivate or -activate
        activate = bool(activate)  # DBus booleans turn into ints, I want bools
        resp = self.action_handler.send_command("ACTIVATE" if activate else "DEACTIVATE")
        return dbus.Boolean(  # NOTE: return True for success, not True for "activated"
            resp == ('+activating.' if activate else '+deactivating.')
        )

    @dbus.service.method("org.freedesktop.ScreenSaver")
    def SimulateUserActivity(self):
        """Poke the running locker to simulate user activity"""
        self.action_handler.send_command("DEACTIVATE")

    @dbus.service.method("org.freedesktop.ScreenSaver", sender_keyword='dbus_sender')
    def Inhibit(self, caller: dbus.String, reason: dbus.String, dbus_sender: str):
        """Inhibit the screensaver from activating. Terminate the light-locker-command process to end inhibition."""
        # This gets more complicated with a need to repeatedly "poke" xscreensaver because there is no inhibitor built into it.
        # NOTE: xdg-screensaver already has this working, perhaps just reuse that

        # NOTE: There's something calling itself "My SDL application" calling Inhibit every 20 seconds when there's user input,
        #       with the reason "Playing a game", then immediately calling UnInhibit if it was given an ID.
        #       It's Steam, I don't understand wtf it's doing since it should probably be calling SimulateUserActivity.
        #       I suspect when an actual game is running it won't UnInhibit, but I haven't investigated that.

        # Since DBus uses 32bit integers, make sure isn't any larger than that
        # NOTE: I could start at 0, but I've decided not to for easier debugging
        # FIXME: This won't handle randomly generating duplicates
        inhibitor_id = random.randint(1, 4294967296)
        self.action_handler.add_inhibitor(inhibitor_id, caller=caller, reason=reason,
                                          caller_process=psutil.Process(self._get_procid(dbus_sender)))
        return dbus.UInt32(inhibitor_id)

    @dbus.service.method("org.freedesktop.ScreenSaver")
    def UnInhibit(self, inhibitor_id):
        self.action_handler.del_inhibitor(inhibitor_id)
        # print("UnInhibit called for inhibitor", int(inhibitor_id))


if __name__ == '__main__':
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    DBusListener(XSS_worker())  # The object this returns is useless because it'll get dealt with by GObject
    GLib.MainLoop().run()
