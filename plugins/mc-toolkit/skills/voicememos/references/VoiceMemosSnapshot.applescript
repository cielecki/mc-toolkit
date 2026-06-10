-- VoiceMemosSnapshot.app
-- One job: copy Apple Voice Memos' protected data — CloudRecordings.db (+ the
-- .m4a recordings) — out of the TCC-protected group container into this skill's
-- snapshot folder, so the (FDA-less) sync.py can read it. Needs Full Disk Access.
--
-- Uses the FDA-snapshot-app pattern: Claude Code can't hold FDA reliably (its
-- bundle path changes every version), so a dedicated tiny app at
-- ~/Applications/VoiceMemosSnapshot.app holds the grant. The DESTINATION
-- is read from a sidecar (VoiceMemosSnapshot.dest) next to the app, rewritten by
-- snapshot_trigger.sh each run — so the skill can move without rebuild/re-grant.
--
-- We rsync the WHOLE group container (incremental: only changed/new files) rather
-- than a hardcoded Recordings/ subpath, because the exact internal layout can't be
-- verified before FDA is granted. First successful run reveals it; sync.py then
-- locates CloudRecordings.db inside the snapshot.

on run
	set srcDir to (POSIX path of (path to home folder)) & "Library/Group Containers/group.com.apple.VoiceMemos.shared/"
	set fallbackDst to (POSIX path of (path to home folder)) & "voicememos/snapshot/"

	-- Resolve destination from the sidecar next to this app.
	set dstDir to fallbackDst
	try
		set myPosix to POSIX path of (path to me)
		set appContainer to do shell script "dirname " & quoted form of myPosix
		set destFile to appContainer & "/VoiceMemosSnapshot.dest"
		set sidecar to do shell script "cat " & quoted form of destFile & " 2>/dev/null || true"
		if sidecar is not "" then set dstDir to sidecar
	end try
	if dstDir does not end with "/" then set dstDir to dstDir & "/"

	set logFile to dstDir & "snapshot.log"
	set ts to do shell script "date '+%Y-%m-%d %H:%M:%S'"
	do shell script "mkdir -p " & quoted form of dstDir
	do shell script "echo '===== " & ts & " snapshot start =====' >> " & quoted form of logFile
	do shell script "echo '  src: " & srcDir & "' >> " & quoted form of logFile
	do shell script "echo '  dst: " & dstDir & "' >> " & quoted form of logFile
	try
		-- incremental mirror: only new/changed files copied. -L follows symlinks.
		do shell script "/usr/bin/rsync -aL --delete " & quoted form of srcDir & " " & quoted form of dstDir & " 2>>" & quoted form of logFile
		do shell script "echo '  rsync OK' >> " & quoted form of logFile
	on error errMsg
		do shell script "echo '  FAILED: " & errMsg & " (likely needs FDA)' >> " & quoted form of logFile
	end try
	do shell script "echo '===== " & ts & " snapshot done =====' >> " & quoted form of logFile
end run
