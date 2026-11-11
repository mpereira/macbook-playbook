# mpereira-backup

Backups of a Mac and its drives with [restic](https://restic.net), on a schedule, and copies of files that lose
nothing. One command, `mpereira-backup`, in Python with only the standard library (the Mac's `/usr/bin/python3`), and
a launchd agent that starts it every hour.

This README uses example values: a ZFS pool mounted at `/Volumes/pool`, a portable disk at `/Volumes/portable`, and a
B2 bucket `example-bucket`. Your own values go in a settings file that is not in this repository.

- [The strategy](#the-strategy)
- [Install](#install)
- [Full Disk Access](#full-disk-access)
- [The settings](#the-settings)
- [The commands](#the-commands)
- [The scheduled runs](#the-scheduled-runs)
- [How a backup runs](#how-a-backup-runs)
- [ingest and copy-media](#ingest-and-copy-media)
- [quarantine check and purge](#quarantine-check-and-purge)
- [dedupe plan and apply](#dedupe-plan-and-apply)
- [Retention, forget, prune, and checks](#retention-forget-prune-and-checks)
- [The restore test](#the-restore-test)
- [What the backups leave out](#what-the-backups-leave-out)
- [Restore](#restore)
- [Unplug a drive](#unplug-a-drive)
- [Files and folders](#files-and-folders)
- [Exit codes](#exit-codes)
- [The tests](#the-tests)
- [Not done yet](#not-done-yet)
- [Lessons](#lessons)

## The strategy

Three copies of each file that matters, on two kinds of storage, one of them away from home (3-2-1):

| Repository (example) | Where | What it holds | When |
|---|---|---|---|
| `mac` | on the pool (`/Volumes/pool/restic/mac`) | the Mac's home folder, with an hourly history | every hour that the pool is mounted |
| `offsite` | B2 (`s3:…/example-bucket/offsite`) | the pool's files, and the most important folders of the Mac | every night |
| `portable` | a portable disk (`/Volumes/portable/restic`) | the pool's files | when the disk is plugged in, at most once a week |

- The pool is the main copy of the files that are not on the Mac. `offsite` and `portable` are its two backups.
- The Mac's files are in `mac` (on the pool), and its most important folders also in `offsite`, so they have a copy
  away from home also when the pool is away.
- What can be downloaded or rebuilt is not backed up: caches, package stores, installers, build outputs, vendored
  dependencies ([`files/share/rebuildable.exclude`](files/share/rebuildable.exclude) and
  [the build outputs list](#build-outputs)).
- Each repository has its own password. The passwords and the B2 keys are kept outside the Mac too (in a password
  manager): without them a repository cannot be read.

## Install

The role installs the command, the launcher, the settings, and the launchd agent. Run it with your private variables:

```bash
ansible-playbook main.yml --tags mpereira-backup --extra-vars @/path/to/private/vars.yml
```

| Variable | Default | What it is |
|---|---|---|
| `mpereira_backup_config` | `{}` | the settings, as YAML; written to the settings file as JSON. Empty: the role installs the command only, and no agent |
| `mpereira_backup_secret_files` | `[]` | files written with mode 0600 and never logged: a list of `{path, content}`, such as the password files and the B2 keys |
| `mpereira_backup_config_file` | `~/.config/mpereira-backup/config.json` | the settings file |
| `mpereira_backup_state_dir` | `~/.local/state/mpereira-backup` | where the folders for the settings and the logs are made (set `state_dir` in the settings to the same) |
| `mpereira_backup_lib_dir` | `/usr/local/lib/mpereira-backup` | the Python package, the files that come with it (`share/`), and the launcher's source |
| `mpereira_backup_bin` | `/usr/local/bin/mpereira-backup` | the command |
| `mpereira_backup_launcher` | `/usr/local/bin/mpereira-backup-launch` | the launcher that launchd starts, which holds Full Disk Access |
| `mpereira_backup_launch_agent` | `true` | install and load the launchd agent |
| `mpereira_backup_label` | `org.mpereira.backup` | the agent's label, and the launcher's code-signing identifier |
| `mpereira_backup_interval_seconds` | `3600` | how often launchd starts `mpereira-backup run` |

The role checks that the settings load (`mpereira-backup status`) and loads the agent again when its plist or the
launcher changes.

What the command needs: `restic` and `b3sum` (`brew install restic b3sum`), and for notifications
`terminal-notifier` (`brew install terminal-notifier`; in System Settings › Notifications, set it to Alerts so that a
notice stays until you close it). The paths are settings.

A settings example with every kind of repository and job: [`files/config.example.json`](files/config.example.json).
In YAML, for `mpereira_backup_config`:

```yaml
mpereira_backup_config:
  state_dir: ~/.local/state/mpereira-backup
  trash_requires: [mac]
  drives:
    pool: {mount: /Volumes/pool, kind: zfs, pool: pool}
  repositories:
    mac:
      repository: /Volumes/pool/restic/mac
      password_file: ~/.config/mpereira-backup/mac.password
      drive: pool
      sources:
        - {name: home, kind: home, path: "~", skip: [Library, Downloads], add: [.ssh, .config], remind_after_days: 7}
      iexclude_files: [rebuildable.exclude]
      build_outputs: true
      retention: {keep-hourly: 24, keep-daily: 14, keep-weekly: 8, keep-monthly: 24, keep-yearly: 10}
  jobs:
    mac: {kind: backup, repository: mac, due: {every_hours: 1}}
    health: {kind: health, due: {daily_at: "10:00"}}
mpereira_backup_secret_files:
  - {path: ~/.config/mpereira-backup/mac.password, content: "{{ vault_mac_restic_password }}"}
```

A new repository must exist before its first backup: `mpereira-backup restic mac init`.

## Full Disk Access

macOS keeps some folders from programs that do not have Full Disk Access: Mail, Messages, Safari, and others in
`~/Library`, and some files in `~/Desktop` and `~/Documents`. A backup started by launchd reads them only with the access.

macOS gives the access to the program that launchd starts, by its code signature. A script cannot hold it, and a
Homebrew program loses it at each upgrade. So launchd starts a small launcher
([`files/launcher/mpereira-backup-launch.c`](files/launcher/mpereira-backup-launch.c)), which starts
`mpereira-backup` and waits for it (it does not `exec`, so it stays the program that macOS checks). Its children,
restic and b3sum included, then have the access too.

The role builds the launcher with the path of the command in it, and signs it (ad hoc) with the label as its identifier.
It builds it again only when its source or the command's path changes, because macOS asks for the access again for each
new build. After a build, the role says so. Then:

1. Open System Settings › Privacy & Security › Full Disk Access.
2. If `mpereira-backup-launch` is listed, select it and click −.
3. Click +, press Command-Shift-G, type `/usr/local/bin/mpereira-backup-launch`, and click Open.
4. Make sure that its switch is on.

To check: start a run from launchd (`launchctl kickstart gui/$(id -u)/org.mpereira.backup`), then look in
`~/.local/state/mpereira-backup/logs/run.log`. Each run tries to read the first of `~/Library/Safari`, `~/Library/Mail`
and `~/Library/Messages` that exists, and the log says "Full Disk Access: yes" or "no" when it changes. While it is
"no", the daily `health` job notifies you. The list in System Settings does not always show a program that is not an
app, so the log is the check to trust.

Commands that you type in a terminal run with the terminal's access, not the launcher's.

## The settings

One JSON file: `~/.config/mpereira-backup/config.json`, or the file that `--config` or `MPEREIRA_BACKUP_CONFIG`
names. An unknown setting is an error, so a typo does not go unnoticed. A `"//"` key is a comment, in any object. A
path that starts with `~/` is in `home`.

### Top level

| Setting | Default | What it is |
|---|---|---|
| `drives` | (needed) | the drives that repositories, sources, or `copy_media` are on: [Drives](#drives) |
| `repositories` | (needed) | [Repositories](#repositories) |
| `jobs` | `{}` | what the scheduled runs do: [Jobs](#jobs) |
| `copy_media` | none | the settings of the `copy-media` command and job: [copy_media](#copy_media) |
| `trash_requires` | `[]` | the repositories that must hold a copy before `ingest --then-trash` moves its original to the Trash |
| `state_dir` | `~/.local/state/mpereira-backup` | the state, the logs, the lock, and the transfers' journals |
| `notify` | terminal-notifier | the command for a notification, with `{title}` and `{message}` in its arguments |
| `pause_files` | `[]` | more files that pause the scheduled runs while they exist (another tool's marker) |
| `home` | your home folder | the folder that `~/` means, and restic's `HOME` |
| `restic` | `/opt/homebrew/bin/restic` | |
| `b3sum` | `/opt/homebrew/bin/b3sum` | |
| `zpool` | `/usr/local/zfs/bin/zpool` | for `eject` of a ZFS drive |
| `path` | Homebrew, `/usr/local/bin`, and the system folders | the `PATH` that restic runs with |
| `lock_file` | `STATE_DIR/lock` | |

### Drives

```json
"drives": {
  "pool": {"mount": "/Volumes/pool", "kind": "zfs", "pool": "pool"},
  "portable": {"mount": "/Volumes/portable", "kind": "disk"}
}
```

| Setting | What it is |
|---|---|
| `mount` | where it mounts. The drive is there only when this is a mount point: a folder left at that path does not count, and nothing is ever written under the path while the drive is away |
| `kind` | `zfs` (ejected with `sudo zpool export POOL`) or `disk` (ejected with `diskutil eject`) |
| `pool` | the ZFS pool's name, for `zfs` |
| `eject` | another eject command, as a list of arguments |

### Repositories

| Setting | Default | What it is |
|---|---|---|
| `repository` | (needed) | restic's repository: a folder, or `s3:`, `b2:`, `sftp:`, `rest:`, `rclone:` |
| `password_file` | (needed) | the file with the repository's password |
| `env_file` | none | `KEY=VALUE` lines for restic, such as the B2 or S3 keys. It is read, never run as a script |
| `drive` | none | the drive the repository is on. While it is away, the repository's jobs are skipped |
| `cache_dir` | none with `drive`, else restic's | restic's cache. A repository on a drive needs none: restic reads the drive about as fast. A cache on a drive that is away is not used |
| `min_free_bytes` | `0` | a backup to a folder stops before a snapshot when the folder's drive has less free space |
| `max_added_bytes` | `0` | a backup that adds more than this to the repository (before compression) passes with a warning and a notification. Most often a new cache or build folder that no exclude covers yet (0: no limit) |
| `sources` | `[]` | what it backs up: [Sources](#sources) |
| `exclude`, `iexclude` | `[]` | restic exclude patterns; `iexclude` ignores case |
| `exclude_files`, `iexclude_files` | `[]` | exclude files. A name with no folder is one of the files that come with the tool, such as `rebuildable.exclude` |
| `build_outputs` | `false` | leave out the build outputs and vendored folders found in the sources: [Build outputs](#build-outputs) |
| `backup_args` | `[]` | more arguments for each `restic backup` |
| `retention` | `{}` | the `forget` rules: `keep-last`, `keep-hourly`, `keep-daily`, `keep-weekly`, `keep-monthly`, `keep-yearly`, `keep-within`, `keep-tag` |
| `group_by` | restic's | `forget --group-by` |
| `forget_after_backup` | `false` | run `forget` with the retention rules after each scheduled backup. Leave it off until you have read the output of `mpereira-backup forget REPO --dry-run` |
| `prune_every_days` | `0` | after a `forget`, also `prune` when the last prune is this many days old (0: never by itself) |
| `prune_args` | `[]` | such as `["--max-unused", "10%"]` |

### Sources

Each source is backed up, and reminded about, on its own. Three kinds:

```json
{"name": "pool", "kind": "folder", "path": "/Volumes/pool/files", "split": ["Photos", "Projects"], "remind_after_days": 30}
{"name": "home", "kind": "home", "path": "~", "skip": ["Library", "Downloads"], "add": [".ssh", ".config"], "remind_after_days": 7}
{"name": "docs", "kind": "paths", "paths": ["~/Documents", "~/.ssh"], "tag": "docs", "remind_after_days": 3}
```

| Setting | Kinds | What it is |
|---|---|---|
| `name` | all | unique in the repository; shown in `status` and the reminders |
| `kind` | all | `folder`, `home`, or `paths` |
| `path` | folder, home | the folder (home: default `~`) |
| `split` | folder | folders in it that are cut into one snapshot per entry, because they are large ([How a backup runs](#how-a-backup-runs)) |
| `skip` | home | entries of the home folder to leave out |
| `add` | home | hidden entries of the home folder to back up (hidden folders are left out unless named here) |
| `paths` | paths | the files and folders, backed up together in one snapshot |
| `tag` | all | the snapshot tag. Defaults: folder `NAME-top-files` (for the files at the top of split folders), home `NAME` |
| `drive` | all | the drive the source is on. While it is away, this source is skipped and the others are backed up |
| `remind_after_days` | all | a notification when this source has had no backup for longer (0: never) |

A source on a drive that is not named in `drive` is still found: a path under a drive's `mount` belongs to that drive.

### Jobs

What the hourly run does. The name is yours. A run goes through the jobs by kind: the backups, then `copy-media`,
then the checks, then `health`, so a check never holds back a backup and `health` reports on this run; within a kind,
in the order of the settings.

```json
"jobs": {
  "mac": {"kind": "backup", "repository": "mac", "due": {"every_hours": 1}},
  "offsite": {"kind": "backup", "repository": "offsite", "due": {"daily_at": "02:00"}, "nice": 10},
  "portable": {"kind": "backup", "repository": "portable", "due": {"every_days": 7},
               "notify_success": "The portable disk is backed up: you can eject it."},
  "check": {"kind": "check", "due": {"every_days": 7}},
  "check-data": {"kind": "check", "due": {"monthly_on": 1}, "read_data_subset": true},
  "health": {"kind": "health", "due": {"daily_at": "10:00"}},
  "copy-media": {"kind": "copy-media", "due": {"monthly_on": 1}}
}
```

| Setting | What it is |
|---|---|
| `kind` | `backup` (one repository), `check` (`restic check` on each repository it can reach), `health` (the reminders), or `copy-media` |
| `due` | one of: `every_hours: N`, `every_days: N`, `daily_at: "HH:MM"`, `monthly_on: D` (1 to 28). Counted from the last success, so a job that failed or was skipped is due again at the next run |
| `repository` | for `backup` |
| `repositories` | for `check`: default all |
| `read_data_subset` | for `check`: also read one twelfth of the data, a different twelfth each month (`--read-data-subset M/12`), so all of it is read once a year |
| `read_new` | for `check`: after a check that passes, also read back each file that a repository on a disk gained since the last read-back: [checks](#retention-forget-prune-and-checks). The first run only records the time |
| `nice` | run restic at a lower priority |
| `notify_success` | a notification after each run that passes |

`every_hours` and `every_days` allow 10 minutes of slack, because launchd's hourly start drifts. `daily_at` and
`monthly_on` are due at the first run at or after that time: a Mac that sleeps through 02:00 runs the job when it wakes.

### copy_media

```json
"copy_media": {
  "home": "~",
  "dest": "/Volumes/pool/files/inbox/{date}-mac-media",
  "drive": "pool",
  "folders": ["Pictures", "Movies", "Desktop"],
  "skip": ["Pictures/Photos Library.photoslibrary"],
  "bundles": [".fcpbundle", ".imovielibrary"],
  "derived_names": ["Render Files", "Transcoded Media", "Proxy Media", "Analysis Files", "Thumbnail Media"],
  "derived_paths": []
}
```

| Setting | What it is |
|---|---|
| `home` | the folder that `folders` are in |
| `dest` | where each run copies to; `{date}` is the day of the run, so each run has its own folder |
| `drive` | the destination's drive; the job is skipped while it is away |
| `folders` | the folders to copy from, relative to `home` |
| `skip` | paths relative to `home` to leave out (the Photos library has its own export) |
| `bundles` | folder name endings of app bundles (Final Cut Pro, iMovie) whose derived files are left out |
| `derived_names`, `derived_paths` | the names, or path endings, of folders in those bundles that the app can make again |
| `skip_files` | file names to leave out; default `.DS_Store` and `.localized` |
| `since` | an ISO 8601 time with its zone, such as `2026-11-07T19:54:29+01:00`: the first run copies only what changed after it, because an earlier copy holds the rest. Without it, the first run copies everything. Later runs start from their own stamp |

### quarantine

The files that a reorganization (a move of duplicates, a cleanup) put aside instead of removing, and the check that
must pass before they are removed for good. Optional: without it, the `quarantine` commands say so and stop.

```json
"quarantine": {
  "journals": "/Volumes/pool/.journal",
  "root": "/Volumes/pool/.quarantine",
  "locations": {"pool": ["offsite", "portable"], "mac": ["mac", "offsite"]},
  "min_age_days": 60,
  "check_max_age_days": 7,
  "read_back": {"mac": 1.0, "portable": 1.0, "offsite": 0.01},
  "max_reads": 2000
}
```

| Setting | What it is |
|---|---|
| `journals` | the folder of the journals: one `BATCH.tsv` for each batch of the reorganization |
| `root` | the quarantine folder. Only files under it are checked and purged; what a batch put in the Trash is yours to empty |
| `locations` | for each keeper location (the journal's `keeper_location` column), the repositories that must hold the keeper |
| `min_age_days` | how long the newest file of a batch stays in the quarantine before `purge` may remove the batch; default 60 |
| `check_max_age_days` | each repository that must hold a keeper must have passed `check` this recently; default 7 |
| `read_back` | for each repository, the share (0 to 1) of keepers whose metadata matches the snapshot that is also read back from the repository and hashed; default 0.01. Local repositories are cheap to read: 1.0 reads them all |
| `max_reads` | at most this many keepers whose metadata differs from the snapshot are read back; the rest fail; default 2000 |

A journal has a header line and these tab-separated columns: `batch`, `source`, `keeper`, `keeper_location`, `class`,
`blake3`, `size`, `mtime` (nanoseconds), `quarantine`, `state`. The last line for a source wins. `keeper` is the copy
that stays, `blake3` the hash of the file, `quarantine` where the source went (`-` for a rename), and `state` one of
`planned`, `moved`, `removed`, `trashed`, `purged`, or `skipped: REASON`. `class` is `A` (must be in the backups), `C`
(a copy whose keeper is enough, such as a song in a second copy of an album), or `R` (rebuildable, such as a build
output: the keeper is enough while a repository leaves it out by choice; once none does, it is checked as `A`).

### dedupe

Where `dedupe plan` looks for duplicate files, and which copy it keeps: [dedupe plan and apply](#dedupe-plan-and-apply).
Optional, and needs `quarantine`.

```json
"dedupe": {
  "root": "/Volumes/pool",
  "location": "pool",
  "prefer": ["library", "archive", "inbox", "replaceable/TV Shows", "replaceable/Movies", "replaceable/Music",
             "replaceable"],
  "replaceable": ["replaceable"],
  "stay": ["^(?!library/)(?:.*/)?(?i:exports?)/"],
  "skip": [".quarantine", ".journal", "restic"],
  "min_size": 1048576
}
```

| Setting | What it is |
|---|---|
| `root` | the folder whose duplicates are found and moved; it and `quarantine.root` must be on one file system |
| `location` | the keeper location of the batches (a key of `quarantine.locations`): the repositories that must hold each keeper |
| `prefer` | folders under `root`, in order: the keeper is a copy in the first one that holds a copy. A copy in none of them comes last. Put the organized folders before the "bag" folders (`replaceable/Music` before `replaceable`, which holds the torrents) |
| `replaceable` | folders under `root` whose files can be found again. A keeper in one of them makes the batch line class `C`; a group with copies both in and out of them goes to the review |
| `stay` | regular expressions (Python) on the path under `root`: a matching copy is never moved, but can be the keeper. The example keeps each copy in an `Exports` folder outside `library` where it is |
| `skip` | folders under `root` that are never read: the quarantine, the journals, and repositories. `quarantine.root`, `quarantine.journals`, and each local repository under `root` are skipped also when not listed |
| `min_size` | smaller files are left out (bytes; default 1 MiB): many small duplicates free little space and cost a read each |
| `fclones` | the `fclones` program; default `/opt/homebrew/bin/fclones` |
| `within_tier` | `true` also moves a copy whose keeper is in the same `prefer` folder (default `false`: only a copy whose keeper is in a better folder moves) |

## The commands

All commands take `--config FILE` before the command. Commands that use a repository wait for the lock, so they never run
at the same time as a scheduled job. `mpereira-backup restic` waits for it too, and restic holds it until it exits:
a `restore` or a `mount` keeps scheduled jobs and `eject` waiting while it runs. Each writes its log to `STATE_DIR/logs/COMMAND.log`, and to the terminal.

| Command | What it does |
|---|---|
| `mpereira-backup run [JOB…] [--force]` | runs the jobs that are due (all, or those named), or all of them with `--force`. What launchd starts |
| `mpereira-backup status` | the drives, each source's last backup and reminder limit, and each job's last success, result, and next due time |
| `mpereira-backup backup REPO [--continue] [--source NAME]… [-- ARGS]` | backs up a repository now. `--continue` skips the snapshots that the last, stopped backup finished. `-- --dry-run` passes arguments to `restic backup` |
| `mpereira-backup forget REPO [--dry-run]` | removes the snapshots that the retention rules do not keep. The data stays until `prune` |
| `mpereira-backup prune REPO [-- ARGS]` | deletes the data of forgotten snapshots, for good |
| `mpereira-backup check REPO [--new-since TIME\|last] [-- ARGS]` | checks that the repository is whole; `-- --read-data-subset 1/12` also reads data. `--new-since` then reads back each file that a repository on a disk gained since TIME (ISO 8601) or since the last read-back that passed (`last`) |
| `mpereira-backup restore-test REPO PATH [--snapshot ID] [--into FOLDER]` | restores a folder from the newest snapshot that holds it and compares it with the folder on disk: [restore test](#the-restore-test) |
| `mpereira-backup restic REPO ARGS…` | any restic command, with the repository's settings: `snapshots`, `ls`, `find`, `diff`, `restore`, `mount`, `init` |
| `mpereira-backup pause REASON…` / `resume` | stops the scheduled runs (except `health`, which reminds you daily) until `resume` |
| `mpereira-backup eject DRIVE` | waits for a running job, then ejects the drive, and says when you can unplug it |
| `mpereira-backup ingest SOURCE DEST [--then-trash]` | copies a folder and checks each copy: [ingest](#ingest) |
| `mpereira-backup copy-media` | the `copy-media` job, now |
| `mpereira-backup quarantine list` | each batch: the files in the quarantine, their size and age, and the day a purge may start. Reads no repository |
| `mpereira-backup quarantine check BATCH` | checks that each keeper of the batch is whole and in the backups that must hold it: [quarantine](#quarantine-check-and-purge). Read-only, so it takes no lock |
| `mpereira-backup quarantine release BATCH PATH... [--undo]` | marks the files whose keepers you removed by choice: the check passes them, and a purge removes them |
| `mpereira-backup quarantine purge BATCH [--dry-run]` | when the batch is old enough and the check passes, removes its files from the quarantine, for good |
| `mpereira-backup excludes report REPO [--source NAME]… [--continue] [--top N]` | walks the repository's sources and lists each place its backups leave out, with the size and the reason: [what the backups leave out](#what-the-backups-leave-out). Read-only, no lock |
| `mpereira-backup excludes review REPO [--all] [--top N]` | from the last report: the kept folders and files named like a cache or a build output, that it did not show before |
| `mpereira-backup dedupe plan BATCH [FOLDER…] [--fclones JSON [--hashed-after TIME]] [--cache]` | finds the duplicates under `dedupe.root` (or only in the FOLDERs) and writes a plan: [dedupe](#dedupe-plan-and-apply). Moves nothing, no lock |
| `mpereira-backup dedupe apply BATCH [--jobs N] [--dry-run]` | moves the planned files to `QUARANTINE_ROOT/BATCH/`, each after a BLAKE3 comparison with its keeper, with a journal |

Examples:

```bash
mpereira-backup status
mpereira-backup backup offsite --source home
mpereira-backup backup portable -- --dry-run
mpereira-backup forget offsite --dry-run
mpereira-backup restic mac snapshots --latest 3
mpereira-backup restic offsite find '*.pdf' --newest 5
mpereira-backup pause "replacing the pool's disks"
mpereira-backup ingest "/Volumes/SD card/DCIM" /Volumes/pool/files/inbox/2026-11-08-camera --then-trash
mpereira-backup eject portable
mpereira-backup quarantine list
mpereira-backup quarantine check albums
mpereira-backup quarantine purge albums --dry-run
mpereira-backup check portable --new-since last
mpereira-backup restore-test mac ~/Documents/taxes
mpereira-backup excludes report mac --top 40
mpereira-backup excludes review mac
mpereira-backup dedupe plan dedupe-2027-01 /Volumes/pool/archive /Volumes/pool/inbox
mpereira-backup dedupe apply dedupe-2027-01 --dry-run
```

Options of `mpereira-backup` come before `--`; what comes after it goes to restic as it is.

## The scheduled runs

launchd starts `mpereira-backup run` every hour, when the Mac starts, and when a drive mounts. Each run:

1. Goes through the jobs by kind (backups, `copy-media`, checks, `health`), and runs each that is due.
2. Takes the lock for each job. If a command or a job holds it, the rest wait for the next run.
3. Skips a job whose drive is away. A skip is not a failure, and the job is due again at the next run.
4. Records each result in `STATE_DIR/state.json`, and each source's last backup.

**Notifications:**

- One when a job starts failing (not at each failed run), and one when it passes again.
- From the daily `health` job, one for each source past its `remind_after_days`, one while the runs are paused, and
  one while the scheduled runs do not have Full Disk Access.
- After each run of a job that has `notify_success`.
- A failure in `mpereira-backup` itself (a bug) is recorded and notified as a failure.

**Drives that are away.** A repository on a drive that is away is skipped. A source on a drive that is away is
skipped, and the other sources of its repository are backed up: with the pool away, `offsite` still backs up the Mac's
folders. Only the sources backed up count as backed up, so the reminders tell you when a drive has been away too long.

**Pause.** `mpereira-backup pause REASON` writes `STATE_DIR/PAUSED`. Any file in `pause_files` pauses too, so another
tool can pause the backups while it changes a drive. `resume` removes `PAUSED`, and says if a `pause_files` file
still pauses.

## How a backup runs

**One snapshot per unit, so that a stopped backup continues.** A large source would be one long `restic backup`, which
starts again from the start when it stops. So each source is cut into units, one snapshot each:

- A `folder` source: one unit for each entry in the folder. Each folder named in `split` is cut again, one unit for each
  entry in it, and the files at the top of a split folder go together in one unit (tagged `NAME-top-files`).
- A `home` source: one unit with the visible entries of the home folder (minus `skip`), its hidden files (not its hidden
  folders, which hold apps' data), and the paths in `add`.
- A `paths` source: one unit.

A unit that the excludes leave out as a whole gets no snapshot. Each unit is backed up with
`--one-file-system --exclude-caches`, the repository's excludes, and the unit's parent snapshot (its last snapshot), so
that restic does not read again a file that did not change.

**Continue.** The units that pass are recorded in `STATE_DIR/backups/REPO-progress.json`. A scheduled backup that
starts less than 24 hours after a backup that stopped before its end skips the units that one finished.
`mpereira-backup backup REPO --continue` does the same by hand.

**Exit 3.** restic exits with 3 when it could not read some files. The backup is kept, but the job counts as failed, so
you hear about it: see the log for the files. One exception: when every error line restic printed says only that it
could not read an extended attribute (`error: can not obtain extended attribute ...`), the job passes with a warning
that gives the number of files. restic still backed up those files, their contents, and their other metadata; only that
attribute is missing from the snapshot. On ZFS this happens with old files whose `com.apple.FinderInfo` is empty (macOS
expects 32 bytes) and with files that carry an ACL (`com.apple.system.Security`, which no user process can read). Any
other error in the same backup, such as a file that could not be opened, still fails the job.

<a id="build-outputs"></a>**Build outputs.** With `build_outputs`, each backup first walks its sources for folders
that a build tool or a package manager made, and writes them to `STATE_DIR/backups/REPO-build-outputs.exclude`. A
folder named `target`, `Library`, `vendor`, or `bower_components` can also hold your own files, so it is listed only
when what sits beside it shows a build tool made it: `target` beside `Cargo.toml`, `pom.xml`, `project.clj`, `build.sbt`,
`build.gradle`, `deps.edn`, and others; `Library` beside a Unity project's `Assets` and `ProjectSettings`; `vendor` beside
`.git` or a package manager's file; `bower_components` beside `bower.json`. The walk does not go into `.git`, into
`node_modules` (which `rebuildable.exclude` leaves out), or onto other file systems.

**Excludes and wholly left-out folders.** To know which units the excludes leave out as a whole, the tool matches the
patterns as restic does. A pattern that it cannot match exactly matches nothing, and a set with a negation (`!pattern`)
leaves out nothing: a doubt costs an empty snapshot, never a folder left out of the backups.

## ingest and copy-media

<a id="ingest"></a>**`mpereira-backup ingest SOURCE DEST [--then-trash]`** copies a folder (an SD card, a phone
export, Downloads) to a drive, so that nothing is lost. It works in four phases, each recorded as it goes in
`STATE_DIR/transfers/ingest-NAME-ID/`, so a run that stops continues where it stopped when you run the same command
again:

1. **List.** Walk SOURCE, and hash each file with BLAKE3 (b3sum, in batches, four at a time).
2. **Copy.** Copy each file next to its destination under a temporary name (`.NAME.mpereira-backup-copy`), read the copy
   back with BLAKE3, and give it its name only when the hash is the listed one. Times and permissions are kept, and
   links are copied as links.
   - Nothing is overwritten. A destination that is already there counts as copied when it has the same bytes, and is
     otherwise a conflict.
   - A file that already has the temporary name is never removed, even one that a killed run left: the copy takes the
     next free name (`.NAME.2.mpereira-backup-copy`). Remove such leftovers yourself.
   - A file that changed after the list is not copied.
   - When a copy reads back with other bytes, the original is read twice more. If both reads give the copy's bytes, the
     list's read was the bad one (a card can give one bad read), and the copy counts. Otherwise the run stops: a drive
     is bad.
   - Before the first copy, the destination's drive must have the size of the copies free, plus 10 GB, or the run
     stops with exit 75. A full drive also stops the run.
3. **Trash** (only with `--then-trash`). Each original whose copy passed moves to the Trash (on its own volume:
   `~/.Trash` or `VOLUME/.Trashes/UID`, in a folder named for the time and the source), but only when:
   - each repository in `trash_requires` is reachable and has a snapshot, taken after the copy, that holds every copy
     with its size (`restic ls`); otherwise nothing moves, and the command says which backup to wait for (exit 75);
   - the original, hashed again, has not changed since it was copied;
   - the copy, hashed again, still has the original's bytes, and its ctime is older than each of those snapshots (a
     copy changed after a snapshot would make the snapshot hold other bytes);
   - neither the original nor its folder is locked (the `uchg` or `schg` flag, Finder's "Locked"). Clear the flag
     with `chflags nouchg PATH` and run the command again to move it;
   - the original has no resource fork. Only the data fork is copied and hashed, so a file with a resource fork (an
     old Mac file, a font suitcase) stays, and is yours to move.
   What does not pass stays where it is. Empty folders are removed, never SOURCE itself.
4. **Done.** The counts, and up to 50 of the files that stay, with the reason. Exit 1 when some stay.

Files that are only in iCloud ("dataless") are not opened, because that downloads them: they are listed as `dataless`
and stay. A file that cannot be read stays as `unreadable`. Nothing is ever deleted: you empty the Trash.

**`mpereira-backup copy-media`** (and the monthly `copy-media` job) copies the photos and videos made or added in
`copy_media.folders` since its last run to a new folder, `dest` with the day in it. It is `ingest` without the Trash,
with the rules of `copy_media`, and a stamp in `STATE_DIR/transfers/copy-media-since.json`: a file counts as new when
its change time (ctime, which a copy or a move also sets) is after the start of the last run that copied everything. A
run where some files could not be copied does not move the stamp, so the next run tries them again.

## quarantine check and purge

`quarantine check BATCH` passes only if, for every file of the batch that is still under `quarantine.root`:

1. Its keeper, followed through the renames of all the journals (a later batch may have moved or renamed it), is
   there, is a regular file, is not in the quarantine, and hashes now to the hash in the journal. A keeper you moved
   by hand is found by its content: the check looks in the top folder of its path under the source that holds it
   (`~/Documents` for `~/Documents/Finance/a.pdf`), hashes only the files of its size, and takes the first file, by
   path, with its hash. The rest of the check is then about that file, so a backup must have run since the move. A
   keeper moved out of its top folder, or removed, fails.
2. Each repository of the keeper's location holds it, unless that repository leaves it out by your choice: no source
   holds it, its excludes match it or a folder above it, a folder above it has a valid `CACHEDIR.TAG`, or a folder
   above it is a build output (with `build_outputs`). A class `A` keeper that no repository holds fails. The log
   counts what each repository leaves out and why, and how many class `A` keepers only one repository holds.
3. "Holds it" means the newest snapshot whose paths cover the keeper has it with the same size, mtime, ctime, and
   inode. A change of content always changes the ctime, and the keeper was just hashed, so that snapshot holds the
   same bytes; no test of "a snapshot newer than the move" is needed. Where the metadata differs (a file restored or
   copied back), the file is read back with `restic dump` and hashed. A share of the matching ones (`read_back`) is
   read back too, to catch a damaged repository. A read back that fails is tried again by the next check.
4. Each of those repositories passed `check` (or a check job) within `check_max_age_days`, and its drive is there.

A keeper whose ctime changed while it was hashed, and nothing else (inode, size, and mtime), is hashed once more: on
OpenZFS on macOS the first read of a file can set its ctime. It passes when the second hash is the same and the file
did not change again.

Hashes are saved as they finish, in `STATE_DIR/quarantine/`, keyed by each file's size, mtime, ctime, and inode, so a
stopped check continues where it stopped, and a second check of a batch whose keepers did not change hashes nothing.

A file whose keeper you removed by choice fails the check, because its copy in the quarantine is the last one
outside the snapshots. `quarantine release BATCH PATH...` marks it: each `PATH` is the file in the quarantine or its
keeper as the journal names it, and a `released` line goes to the journal. The check then passes it with no test, and
a purge removes it. It changes nothing when a path matches no file of the batch, or when a keeper is still there
(the check can test that one). `--undo` puts released files back under the check.

`quarantine purge BATCH` first looks at the age: the newest file in the batch's quarantine must have been put there
(its ctime) at least `min_age_days` ago, or it stops with exit 75 and names the day. Then it runs the check, and only
when it passes, reads each file in the quarantine and removes it only when its size and mtime are still those in
the journal, its BLAKE3 is the journal's, and it did not change while it was read. A file whose ctime, and nothing else, changed
after it was read (the first read on OpenZFS, or a write that kept the size and mtime) is read once more, right
before it goes, and must give the journal's hash again. It adds a `purged` line for it to
the journal right after, and removes the folders left empty. A file that changed in the quarantine, cannot be read, or is locked
(the `uchg` or `schg` flag on it or its folder), stays, and the purge exits with 1. Snapshots of the pool that still hold the files are yours to destroy.

## dedupe plan and apply

```bash
mpereira-backup dedupe plan dedupe-2027-01
less ~/.local/state/mpereira-backup/dedupe/dedupe-2027-01/plan.tsv
mpereira-backup dedupe apply dedupe-2027-01 --dry-run
mpereira-backup dedupe apply dedupe-2027-01
mpereira-backup quarantine check dedupe-2027-01    # after the next backups
```

Two steps, so that you read the plan before anything moves. Each batch has a new name: `plan` refuses a batch that
has a journal.

`dedupe plan BATCH [FOLDER…]` runs `fclones group --hash-fn blake3 --min MIN_SIZE --hidden --no-ignore --one-fs` on
the FOLDERs (default: all of `root`), without the `skip` folders, into `STATE_DIR/dedupe/BATCH/fclones.json`. A
finished fclones run of the same folders is used again by the next `plan` of that batch, so a change to the settings
does not read the pool again. `--fclones JSON` uses a run of yours instead; add `--hashed-after TIME` with the time
that run started, if it used `--hash-fn blake3`, so that `apply` can take its hashes. `--cache` lets fclones use its
hash cache, whose hashes can be older than the run, so `apply` then reads every file.

From each group of equal files, `plan` leaves out the copies that must not move or be a keeper: smaller than
`min_size`, a tab or a newline in the name, in a repository tree (a `.git` in its folder or a folder above), in a
package (`.app`, `.photoslibrary`, `.fcpbundle`, …), or in a `skip` folder. It sorts the others: the first `prefer`
folder that holds the copy, then the fewest folders deep, then the oldest mtime, then the path. The first is the
keeper. The others go to the plan, except those that match `stay`, and, unless `within_tier` is true, those in the
same `prefer` folder as the keeper (or, like the keeper, in none). So by default a copy moves only when a better
folder holds its keeper: `inbox/x` and `archive/x` go to the quarantine when `library/x` exists, but the same file in
two machine backups under `archive/machines`, or in two albums of an unsorted music folder, stays in both. The
reorganization's step 22 (2026-11-07) moved only copies of that kind, and a trial of the generic rules on its fclones
run found 26 GB of copies inside one folder that nobody had decided on. A whole group goes to `review.tsv` instead
when:

- it has copies both in and out of the `replaceable` folders: whether the content is backed up depends on which one
  stays;
- a repository of `location` leaves out the keeper by choice (excludes, `CACHEDIR.TAG`, no source) but holds a copy
  that would move;
- a copy is gone or changed since fclones read it.

It writes `plan.tsv` (file, keeper, location, class `A` or `C`, size, BLAKE3 or `-`), `review.tsv` (reason, group,
size, path), and `plan.json`, and prints the totals by folder and what it left out and why.

`dedupe apply BATCH` moves each planned file to `QUARANTINE_ROOT/BATCH/<its path under root>` with `rename`, never a
copy and never over a file. Before each move it reads the file and its keeper with `b3sum` and moves only if they are
equal, and equal to the plan's hash when it has one: a file can change and keep its size. A file whose ctime and mtime
are both before fclones started takes the hash of the plan instead, because a write always sets the ctime to the time
of the write. A file whose ctime, and nothing else, changed while it was read is read once more, and moves when the
second read gives the same hash: on OpenZFS on macOS the first read can set the ctime. A keeper with many copies is
read once. It refuses (a `skipped: REASON` line, exit 1 at the end) a
file that is gone, changed, not a regular file, locked (the `uchg` or `schg` flag on it or its folder; clear it with
`chflags nouchg PATH` and run again), or in a repository tree or package; a keeper that is gone, not a
regular file, or the same file (a hard link); and a quarantine path that exists. A failed read or move stops the run.

The journal is `quarantine.journals/BATCH.tsv`, as `quarantine check` and `purge` read it. A `planned` line, synced,
comes before each move and a `moved` line after it. Run the same command again after a stop: the journal and the disk
say what is done, and a move whose `moved` line the stop cut off is found by the size and mtime of the quarantine
file. `--dry-run` checks each line against the disk without reading or moving anything. `--jobs N` (default 4) moves
N files at a time.

`dedupe` moves whole files only. Duplicate albums (the same album in two folders, with some tracks different) need
a decision per album; see [Not done yet](#not-done-yet).

## Retention, forget, prune, and checks

- `forget` removes the snapshots that the `retention` rules do not keep, for each unit on its own (restic groups
  snapshots by host and paths). `keep-daily 14` counts days that have a snapshot, so the last snapshots of a drive that
  is away are kept however long it is away.
- `prune` then deletes their data, **for good**. Read `mpereira-backup forget REPO --dry-run` before you turn on
  `forget_after_backup`.
- B2 has no minimum storage time, so a monthly `prune --max-unused 10%` costs nothing extra.
- On a portable disk, `--max-repack-size` limits how much a prune writes at a time.
- The weekly `check` reads the repository's structure. The monthly `check` with `read_data_subset` also reads one
  twelfth of the data, so each pack is read once a year.
- `read_new` (or `check REPO --new-since last`) reads back what the last backups added to a repository on a disk.
  restic names each data, index, and snapshot file by the SHA-256 of its bytes, so a file whose hash matches its name is
  exactly what restic wrote. This reads only what is new, where `--read-data` reads everything, so a bad write is found
  within a week, not within a year. It reads the files directly, so it works only for a repository on a disk, not on
  B2. The time it records is the start of the check, and it moves only when every file matched.

## The restore test

```bash
mpereira-backup restore-test mac ~/Documents/taxes
```

1. Picks the newest snapshot whose paths hold the folder (or `--snapshot ID`). A pool repository makes one snapshot
   per unit, so pick a folder inside one unit.
2. Restores the folder into `STATE_DIR/restore-test/REPO` (or `--into`), with a `CACHEDIR.TAG` so no backup keeps it.
   A folder given with `--into` must be empty or not exist; what a failed test left in the default folder is removed
   first.
   The drive must have the folder's size free, plus 10 GB, or the test stops with exit 75 before restic runs.
3. Compares each restored file with the file on disk by BLAKE3. A file whose ctime is newer than the snapshot changed
   since the snapshot and is counted, not compared. Any other file must have the same size and mtime as its restored
   copy, and the same bytes. Symbolic links must point to the same place, and a file that did not change must not
   come back as a link, or a link as a file.
4. Each file on disk that is older than the snapshot must be restored, unless the repository leaves it out by choice
   (its excludes, a `CACHEDIR.TAG`, a build output, another file system).
5. Passes: removes the restored copy (and the folder, if the test made it) and records `last_restore_test` in the
   state. Fails (exit 1): keeps the copy and
   logs up to 50 files with the reason.

Run it after a big change to the settings, and from time to time on a folder you care about.

## What the backups leave out

```bash
mpereira-backup excludes report mac --top 40
mpereira-backup excludes review mac
```

`excludes report REPO` walks the repository's sources the way a backup sees them, reads nothing in the repository, and
writes into `STATE_DIR/excludes/`:

| File | What it holds |
|---|---|
| `REPO-left-out.tsv` | each topmost place the backups leave out: the reason, its bytes, its files, and its path. The last one is kept as `REPO-left-out.previous.tsv` |
| `REPO-review.tsv` | each kept folder named like a cache or a build output (`cache`, `tmp`, `build`, `target`, `dist`, `.venv`, `__pycache__`, `.gradle`, `.terraform`, `vendor`, `site-packages`, …), and each kept file of 1 MiB or more with such an extension (`.o`, `.class`, `.log`, `.tmp`, `.part`, `.crdownload`, `.vmem`, `.ipsw`, …). Archives are not on the list |
| `REPO-errors.txt` | the paths it could not read: a backup cannot read them either |
| `REPO-report.json` | the totals, for `review` and the next report |
| `REPO-walk.jsonl` | the walk's progress, one line per top-level folder |

The reasons are `exclude PATTERN` (the first pattern that matches), `CACHEDIR.TAG`, `build output` (with
`build_outputs`), and `another file system`. It prints the totals by reason, the largest places, what is left out now
that was not in the last report and what is no longer left out, the count of candidates to review, and up to 50
unreadable paths. A stopped walk goes on with `--continue`, from the first top-level folder it did not finish.

`excludes review REPO` prints the candidates from the last report that it has not shown before (all of them with
`--all`), largest first, and records them in `REPO-reviewed.json`. Each one is a question: add a pattern to the
repository's excludes, or keep it. When in doubt, keep it: a byte too many in a backup costs less than a file that is
not there.

## Restore

The repository's settings come with each `mpereira-backup restic REPO` command, so the commands are restic's own.

A file or a folder, to a new folder (never over the original):

```bash
mpereira-backup restic mac snapshots --path ~/Documents
mpereira-backup restic mac find 'report*.pdf'
mpereira-backup restic mac restore latest --path ~/Documents --include ~/Documents/report.pdf --target ~/Restored
```

Browse the snapshots as folders (needs macFUSE: the playbook's `macfuse` role, tagged `backup-tools`, installs it):

```bash
mpereira-backup restic mac mount ~/Restic
```

A whole source of a pool repository, unit by unit (each unit is its own snapshot):

```bash
mpereira-backup restic offsite snapshots --json
mpereira-backup restic offsite restore latest --path /Volumes/pool/files/Photos/2019 --target /Volumes/new/restore
```

On a new computer, without this tool: install restic, put the password in a file, and for B2 the keys in the
environment:

```bash
export RESTIC_REPOSITORY=s3:https://s3.us-west-000.backblazeb2.com/example-bucket/offsite
export RESTIC_PASSWORD_FILE=~/offsite.password
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
restic snapshots
restic restore latest --path /Volumes/pool/files/Documents --target ~/restore
```

Restore to a new folder, and compare before you move anything back.

## Unplug a drive

Run `mpereira-backup eject DRIVE`, and unplug when it says so. It waits for a running job, so a backup is never cut
in the middle, and a ZFS pool is exported first (OpenZFS on macOS can hang or panic when a pool disappears while it is
imported). Ejecting a ZFS pool needs `sudo`, so run it in a terminal.

## Files and folders

| Path | What it is |
|---|---|
| `~/.config/mpereira-backup/config.json` | the settings (mode 0600) |
| `STATE_DIR/state.json` | each job's last success, attempt, result, and message; each source's last backup; each repository's last check and prune |
| `STATE_DIR/logs/run.log` | one line per job per scheduled run |
| `STATE_DIR/logs/JOB.log`, `COMMAND.log` | each job's and each command's full log, with an ISO 8601 time on each line and progress once a minute; over 10 MB, it is renamed to `.log.1` when a command opens it |
| `STATE_DIR/logs/launchd.log` | what launchd caught (should be empty) |
| `STATE_DIR/backups/` | each repository's progress and build outputs list |
| `STATE_DIR/excludes/` | the last `excludes report` of each repository |
| `STATE_DIR/dedupe/BATCH/` | the fclones run, `plan.tsv`, `review.tsv`, and `plan.json` of each `dedupe plan` |
| `STATE_DIR/transfers/` | each `ingest` and `copy-media` run's list and journal; a finished run's folder is kept, named with its start time |
| `STATE_DIR/PAUSED` | the scheduled runs are paused while it exists |
| `STATE_DIR/lock` | the lock (`flock`: a crash leaves no stale lock) |
| `~/Library/LaunchAgents/org.mpereira.backup.plist` | the agent |
| `/usr/local/bin/mpereira-backup`, `/usr/local/bin/mpereira-backup-launch`, `/usr/local/lib/mpereira-backup/` | the command, the launcher, and the package |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | done |
| 1 | a job failed; some files of an `ingest` stay; a check, read-back, or restore test found a fault |
| 3 | restic could not read some files (not when every error is an extended attribute it could not read: that passes with a warning) |
| 64 | a wrong argument (an unknown repository, job, or drive; nested folders for `ingest`) |
| 66 | a source that is not there |
| 69 | a drive that is away, a program that cannot run, or a lock file that cannot be opened |
| 75 | try again later: `ingest --then-trash` waits for a backup; `quarantine purge` waits for the batch's age; `ingest`, `copy-media`, or `restore-test` would leave its drive with less than 10 GB free |
| 78 | the settings are wrong or missing |
| 130 | stopped with Control-C |

Other codes are restic's.

## The tests

```bash
cd roles/mpereira-backup
/usr/bin/python3 -m unittest discover -s tests -v
```

They run in temporary folders with a fake restic ([`tests/fake-restic`](tests/fake-restic)), which records each call,
keeps "snapshots" of the real folders, and can be told to fail or to leave a file out of `ls`. They use the real
`b3sum`. They cover the settings, restic's patterns, when a job is due, the units and their parents, a backup that
stops and continues, the notifications, a backup that adds more than `max_added_bytes`, a drive that is away, pause, the lock, the reminders, `ingest` (a conflict, a
stop and a continue, a file that changes, a bad read, a bad copy, the Trash with and without the backup, a locked
file, a drive that is nearly full),
`copy-media`, and `quarantine` (a keeper that changed, is missing, is in the quarantine, or is not in the snapshot; a
snapshot with other content; metadata that differs; an old check; what a repository leaves out by choice; renames;
saved hashes; a keeper whose first read set its ctime; and a purge that waits, passes, fails, or meets a changed or
locked file), the read-back of new repository files
(a file that does not match its name, a repository on B2, the time it records, the `read_new` job), the restore test
(a pass, other content, a missing file, an excluded file, a file that changed since the snapshot, a drive that is
nearly full), `excludes` (the
topmost place left out and its reason, a `CACHEDIR.TAG`, a build output, the candidates to review once, the changes
since the last report, a walk that continues), `dedupe` (the keeper by `prefer`, `stay`, `within_tier`, repository trees,
packages, small files, the names fclones escapes, the review of mixed and left-out keepers, a move with its journal, a
second run that finds it done, a stop between a move and its line, a changed file, a gone keeper, a hard link, a
locked file, a file whose first read set its ctime, the
hashes taken from fclones and when they are not, a plan that moves a keeper, the settings), and the options before
`--`. The `dedupe` tests that run fclones skip when it is not installed. The fake restic also prints each file's mtime, ctime, and inode in `ls`, and serves `dump` and `restore`.

## Not done yet

- `dedupe albums`: the album-level step of 2026. A folder "Album [N]" is a second copy of "Album" by the same artist;
  the folder without a suffix stays first, then the ones with more files, then the bigger ones. A folder moved to the
  quarantine whole, and only when each of its files (cover art and logs too, not `.DS_Store` or `._` files) had a
  byte-identical file in a folder that stays. It was a one-time script; it comes back as a command when it is needed
  again.
- A monthly `zpool scrub` needs root, so a LaunchDaemon, not this agent. Until then, run `sudo zpool scrub POOL` by
  hand.

## Lessons

What a large reorganization of files (about 2 TB, 2026) and its backups taught. Each lesson names the part of the tool
that uses it.

**Hashing and copying**

- BLAKE3 (`b3sum`) is fast enough to hash everything that is copied: it is limited by the disk, not the CPU. Hash in
  batches of about 200 files per `b3sum` call, so the start-up cost of each call does not count, and four calls at a
  time. (`ingest`, `copy-media`)
- Read a copy back before you trust it, and when it does not match, read the original again before you decide which
  read was wrong: an SD card gave one bad read in about 30,000 files. (`ingest`)
- Copy under a temporary name, and give the final name only after the read-back, with a hard link (never `rename`
  over a name that a file got in the meantime). (`ingest`)
- A journal line per file, synced before the next change, makes every long step continue where it stopped. Read the
  journal so that a line cut off by a stop is skipped, and its file is checked again. (`ingest`, `backup`)
- Find what is new since a copy by the change time (ctime), not the modification time: a copy or a move keeps the
  modification time, so a file copied in from an old card looks old. (`copy-media`)
- Before you read a disk again, look for a list, hash, or log that already answers the question. A walk of a 2 TB pool
  takes an hour; a list made last week is read in a second. Write each long read to a file that the next step can use.
- Walk folders with `os.scandir`, not BSD `find`: `find` stopped with `fts_read: Interrupted system call` after 43
  seconds and left most of a home folder unchecked, with exit 0 and an empty list that looked like an answer.
- Check the free space before a copy, not only during it: the Mac's disk filled up twice during the work, once to
  728 MB. (`ingest`, `copy-media`, `restore-test`)
- A file locked in Finder (`uchg`) cannot be moved or removed, and its folder's lock stops a move of anything in it.
  Leave such a file where it is and say so; clear the flag yourself if it must move. (`ingest --then-trash`, `dedupe
  apply`, `quarantine purge`)

**Names**

- macOS keeps names as they were written: a "ü" can be one character (NFC) or two (NFD), and two files can have names
  that look the same. Compare names after NFC normalization, and always open, move, and delete by the name on disk.
  (`ingest --then-trash` matches `restic ls` by NFC name)
- restic's `--include` needs each accented character as it is on disk.

**iCloud**

- A file that is only in iCloud ("dataless", the `SF_DATALESS` flag) is downloaded when it is opened. Look at it with
  `lstat` only, and leave it. (`ingest`)

**restic**

- One snapshot per folder makes a backup of terabytes continue after a stop, and keeps each snapshot's parent exact.
  (`backup`)
- Exit 3 means that some files were not read: treat it as a failure, or nobody reads the log. The exception is a
  backup whose only errors are extended attributes that restic could not read (empty `com.apple.FinderInfo` on ZFS,
  ACLs): the files and their contents are in the snapshot, so it passes with a warning. (`backup`)
- `--iexclude` patterns ignore case, `--exclude` ones do not. A pattern without a leading `/` matches a name at any
  depth. restic expands `$NAME` in exclude files. (`patterns`)
- restic has no rename detection: a moved or renamed file is read in full again, also in a dry run, although the data
  it adds is almost nil. Move large folders in few batches, and back up after each, not before each.
- To leave out a path from the snapshots you already have (after you add an exclude), rewrite them:
  1. `mpereira-backup restic REPO rewrite --exclude-file FILE --dry-run` and read what it would remove;
  2. the same without `--dry-run`, and with `--forget`: restic writes a new snapshot (tagged `rewrite`) for each one
     that changes, and forgets the old one. The next backup takes the new one as its parent;
  3. `mpereira-backup prune REPO`, to free the space;
  4. a `restore-test` of a folder near the removed paths.
- A large backup that adds far more than usual is most often a cache or build folder that appeared since the last one.
  `max_added_bytes` warns about it, so you exclude it before many snapshots hold it. (`backup`)
- `--one-file-system` keeps a backup from walking into another drive mounted inside the source.
- Never let restic make its cache on a drive that is away: the folder it makes at the mount point keeps the drive from
  mounting there. (`restic.global_args`)
- A repository on a drive runs with `--no-cache`: restic's cache of its trees grew to 8.7 GB in `~/Library/Caches`
  for the Mac's hourly home backups, to save reads from a drive that is about as fast. A repository on B2 keeps its
  cache, which saves downloads. (`restic.global_args`)
- Make the B2 bucket private, with Object Lock off (restic must delete what `prune` removes) and the lifecycle rule
  "Keep only the last version of the file": with the default rule ("Keep all versions"), a delete through B2's S3 API
  only hides the file, and you pay for what `prune` removed.
- B2 has no minimum storage time, so prune often. A prune on a portable disk with `--max-repack-size` writes less at a
  time.

**ZFS and drives**

- Check a batch of moves against its journal (each path it moved is gone, each new path holds the same file), not
  with `zfs diff`. On OpenZFS on macOS 2.4, `zfs diff` costs time per changed object, 25 to 164 lines a second (61
  minutes for 456,860 lines), and a long run was the likely trigger of a kernel panic (`SPL: releasing held mutex`).
  (`dedupe apply`, `quarantine check`)
- On OpenZFS on macOS, the first read of a file can set its ctime, with no change to its content. A check that
  compares the ctime before and after a hash must read again before it calls the file changed. (`dedupe apply`,
  `quarantine check`)
- OpenZFS on macOS can panic when a pool disappears while it is imported: export it first. (`eject`)
- Turn Spotlight off on the pool: its index is large and rebuilt for nothing.

**Extended attributes and ACLs on ZFS**

restic stops with exit 3 on attributes it cannot read; these were the causes, and how they were repaired. The tool
has no command for this: the repairs were one-time scripts, and the lessons are here.

- `getxattr` of `com.apple.FinderInfo` on ZFS needs a buffer of exactly 32 bytes; a larger one gives `ERANGE` ("value
  too large to be stored in data type"), which is the error restic reports. An empty FinderInfo (all zeros) gives
  length 0, not `ENOATTR`.
- `com.apple.system.Security` (an ACL) cannot be read with `getxattr` at all (`EPERM`, "operation not permitted").
- `ls -l@d PATH` shows the attributes and `ls -led PATH` the ACL as macOS sees them; trust these over a program that
  reads the attributes itself.
- To remove an all-zero FinderInfo: keep its value, in hex, in a journal, then `removexattr`; the journal lets you put
  it back.
- To remove an ACL that `chmod -N` cannot: copy the file next to itself without the ACL (`cp -p`, then compare the
  bytes), keep a journal line, and rename the copy over the original. Each step continues after a stop.
- After any such repair, the ctime of each file is new, so the next backup reads its metadata again.
- A drive is there only when its mount point is a mount point: test that, not that the folder exists. (`util.mounted`)

**Duplicates**

- `fclones group` finds duplicate groups fast, and its BLAKE3 hashes can be used later instead of reading a file again,
  when the file's change time is before fclones started. Its JSON escapes a backslash as two and control characters
  as `\t`, `\n`, `\r`, or `\xHH`: decode them before you use a path. (`dedupe`)
- Choose the copy that stays by rules, in this order: the organized folder over a "bag" folder (a torrents or downloads
  folder), the library over the archive and the inbox, and whole music albums only, never single tracks. (`dedupe`)
- Move duplicates to a quarantine with a date, not to the Trash, and purge only after 60 days. (for `quarantine`)
- `fclones --cache` follows a renamed file, so a second run need not read all of a pool again. But a file with new
  bytes, the same size, and the same mtime still counts as a duplicate from the cache: read it again before a move.
  (`dedupe plan --cache`)
- Two copies of a song can differ only in their tags or cover art. To compare the music, hash the audio stream alone:
  `ffmpeg -v error -i FILE -map 0:a:0 -c copy -f md5 -`. Two files with the same audio md5 hold the same music.
  (for `dedupe albums`)

**macOS**

- Full Disk Access belongs to the program that launchd starts, by code signature: one small signed launcher that does
  not `exec` holds it for everything it starts. (`mpereira-backup-launch`)
- macOS's `/bin/bash` is 3.2: `set -e` does not stop on a failed `[[ ]]`, and arrays are limited. One reason this tool
  is Python.
- `/usr/bin/python3` (3.9) does not change with Homebrew upgrades. It reads at most microseconds in `fromisoformat`,
  and restic writes nanoseconds. (`units.snapshot_time`)
