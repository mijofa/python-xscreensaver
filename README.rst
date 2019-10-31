python-xscreensaver
===================
Python implementation of xscreensaver-command.c

Initially started as a code snippet in a larger script, for now that script is still all that's in here.
I intend to split this out into its own library and improve with the extra features it doesn't yet support (such as -watch)

dbus-xscreensaver
-----------------
So I refuse to use any lockscreen other than xscreensaver, I'll just link to someone else explaining why that is: https://www.jwz.org/blog/2015/04/i-told-you-so-again/

Problem though is that xscreensaver doesn't implement a way for games/video-players/etc to inhibit or suspend the screensaver in some way so that I can sit back and watch a movie or play a game using non-X11 inputs such as game controllers. The closest solution Xscreensaver implements is the ability to "poke" the screensaver and keep it awake for another minute (or however long the configured timeout is) by simulating user input.

That seems reasonable to me, with that approach if the inhibiting app crashes or loses contact then xscreensaver will lock a minute later, failing safe. However no apps actually bother implementing this because gnome-screensaver, light-locker, and other "alternative" screensavers implement a way of inhibiting the screensaver via DBus calls. Chrome/Chromium was the particular app that was getting on my nerves and triggered me to right this.

This script is intended to be a compatibility layer between the org.freedesktop.ScreenSaver DBus calls and the xscreensaver-command functions, with a primary focus on supporting the Inibit/UnInhibit method by repeatedly simulating user input. It's not very pretty, and I admit that I'm probably reducing the security of my lockscreen by implementing such a thing, but it should still be safer than using an alternative.

FIXME: Somehow get the PID of the D-BUS caller and cancel the inhibitor if that PID dies.
