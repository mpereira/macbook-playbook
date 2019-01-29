tell application "System Preferences"
	activate
	set current pane to pane "com.apple.preference.keyboard"
end tell

delay 1

tell application "System Events"
	tell process "System Preferences"
		get properties

		click button "Modifier Keys…" of tab group 1 of window "Keyboard"
		tell sheet 1 of window "Keyboard"
			click pop up button 2
			click menu item 2 of menu 1 of pop up button 2
			click button "OK"
		end tell
	end tell

	tell application "System Preferences" to quit
end tell
