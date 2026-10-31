-- Clip Review.app (built by `factory.py app`): opened bare (Dock, or a click on its notification) it opens the review
-- page; opened by the 06:30 morning job, which leaves the report in state/notify-pending.txt first, it posts that report
-- as its own notification, so clicking the notification opens this app, and the app opens the page.
on run
	set f to (POSIX path of (path to home folder)) & "clip-factory/state/notify-pending.txt"
	set msg to ""
	try
		set msg to do shell script "cat " & quoted form of f & " && rm -f " & quoted form of f
	end try
	if msg is not "" then
		display notification msg with title "Clip drafts ready to sign" sound name "Glass"
		delay 2
	else
		open location "http://127.0.0.1:8781/"
	end if
end run
