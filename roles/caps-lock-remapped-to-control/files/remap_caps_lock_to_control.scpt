tell application "System Preferences"
	activate
	set current pane to pane "com.apple.preference.keyboard"
end tell

tell application "System Events"
	tell application process "System Preferences"
		get properties
		
		click button "Modifier Keys…" of tab group 1 of window "Keyboard"
		tell sheet 1 of window "Keyboard"
			click pop up button 4
			click menu item 2 of menu 1 of pop up button 4
			click button "OK"
		end tell
	end tell
	
	tell application "System Preferences" to quit
end tell
