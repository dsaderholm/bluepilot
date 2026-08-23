---
description: Put the current branch on the comma and reboot it, verified by content. Use whenever he asks whether the car is current or wants it updated -- "is my comma updated?", "update my comma", "push it to the car", "did you update my Comma?" -- and after any push he is waiting on.
---

Update the car to the tip of the branch it tracks, and reboot it. Do all of it — do not print a
command for him to run. He has said so directly: *"You do reboots and updates! I don't care!"*

## 1. Find the device

Use **PowerShell, not the Bash tool**. The Windows OpenSSH agent is a named pipe and Git Bash's ssh
cannot speak to it. Every remote command goes in a **single-quoted here-string**, because PowerShell
expands `$(...)` inside double quotes before ssh ever sees it — that has already produced a
confident report about the car that was actually read off this laptop.

```powershell
$env:SSH_AUTH_SOCK='\\.\pipe\openssh-ssh-agent'
$cmd = @'
cd /data/openpilot && echo "HEAD: $(git rev-parse --short HEAD)"
'@; ssh -4 comma@comma-34b959b "bash -lc '$cmd'"
```

Always force `-4`. Bitwarden Desktop is the agent — never ask him to export a key.

**If `comma-34b959b` does not resolve**, he has moved networks. Do not ask him for an IP. Read this
laptop's own subnet and sweep it:

```powershell
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*'}
```

then ping-sweep that /24 in parallel and try `comma@<ip>` on the live hosts. The device has been
found this way on both `10.0.1.x` and `192.168.1.x`.

## 2. Check `IsOnroad` FIRST

```
cat /data/params/d/IsOnroad
```

**`1` means he is driving. Stop. Touch nothing.** That constraint has never changed and never will.
Say the car is in use and that you will do it when it is parked.

## 3. Reset — never `git pull`

```
cd /data/openpilot && git fetch origin <branch> && git reset --hard origin/<branch>
```

`git pull` fails every time a branch is rebased, which is most updates here, and it has already left
him standing at the car with four conflicts. The device is a deployment with no local edits, so
discarding them is free.

**If the fetch fails with an SSL/certificate error**, the car's network is intercepting TLS. Do not
wait for the updater. Ship a bundle from the laptop, which can reach both:

```
git bundle create /tmp/fp.bundle <device-HEAD>..<branch>
scp /tmp/fp.bundle comma@<host>:/tmp/fp.bundle
```

then on the device `git bundle verify`, `git fetch /tmp/fp.bundle <branch>:refs/remotes/origin/<branch> -f`,
`git reset --hard origin/<branch>`.

## 4. Verify BY CONTENT, not by hash

A rebase gives every commit a new id, and a matching hash has lied here before. `grep -c` for a
string the new commits introduced and one they removed. If a commit added a capnp field, check it on
the device's own schema rather than in the file:

```
/usr/local/venv/bin/python -c "from cereal import custom; print('<field>' in set(custom.<Struct>.schema.fieldnames))"
```

Note `python` is not on a non-interactive PATH — use `bash -lc` or `/usr/local/venv/bin/python`.

## 5. Reboot

```
sudo reboot
```

Code is inert until the processes restart, so this is the step that decides whether any of it
reached him. Then wait for it to come back and confirm HEAD — do not assert the update landed
without reading it back.

## 6. Report in one or two lines

Which commit it is on, and that it rebooted. If anything on the device looked wrong, one line, no
investigation.
