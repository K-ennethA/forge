# heavy_lock.py -- <cmd>: one heavy Blender job at a time on this machine (lock file improve/heavy.lock; a dead pid is taken over).
# NOTE: until the kit-head-path session finishes, it still uses the session-4f1944ab scratchpad copy (a different lock file).
"""heavy_lock.py -- cmd...   : run cmd while holding the machine-wide heavy-job lock (one full runner at a time, 32 GB box).
Waits (polling 5 s) while another live process holds it; a lock whose pid is dead is taken over."""
import os, sys, time, subprocess, psutil
LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "heavy.lock")
cmd = sys.argv[sys.argv.index("--") + 1:]
t0 = time.time()
while True:
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode()); os.close(fd)
        break
    except FileExistsError:
        try:
            pid = int(open(LOCK).read().strip() or 0)
        except Exception:
            pid = 0
        if pid and not psutil.pid_exists(pid):
            try:
                os.remove(LOCK)
            except OSError:
                pass
            continue
        time.sleep(5)
print("LOCK acquired after %.0f s" % (time.time() - t0), flush=True)
try:
    rc = subprocess.call(cmd)
finally:
    try:
        os.remove(LOCK)
    except OSError:
        pass
print("LOCK released, exit", rc, flush=True)
sys.exit(rc)
