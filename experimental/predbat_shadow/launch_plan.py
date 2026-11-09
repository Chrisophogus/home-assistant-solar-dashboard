"""Privileged setup only; no planner import until confinement is complete."""
import ctypes
import os
import resource
import signal
import sys
from pathlib import Path
from isolation import install_seccomp

root=Path(sys.argv[1]).resolve()
if os.getuid()!=0:raise RuntimeError('Chroot launcher requires initial root')
if not (root/'bundle/plan_job.py').is_file():raise RuntimeError('Runtime not built')
os.environ.clear()
os.environ.update(HOME='/work',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
sys.dont_write_bytecode=True
os.nice(15)
resource.setrlimit(resource.RLIMIT_CPU,(60,65))
resource.setrlimit(resource.RLIMIT_AS,(768*1024**2,)*2)
resource.setrlimit(resource.RLIMIT_FSIZE,(16*1024**2,)*2)
resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))
signal.alarm(90)
os.closerange(3,65536)
libc=ctypes.CDLL(None,use_errno=True)
if libc.prctl(38,1,0,0,0):raise RuntimeError('no_new_privs failed')
for cap in range(41):libc.prctl(24,cap,0,0,0)
os.chroot(root);os.chdir('/work');os.setgroups([]);os.setgid(65534);os.setuid(65534)
class Header(ctypes.Structure):_fields_=[('version',ctypes.c_uint32),('pid',ctypes.c_int)]
class Data(ctypes.Structure):_fields_=[('effective',ctypes.c_uint32),('permitted',ctypes.c_uint32),('inheritable',ctypes.c_uint32)]
h=Header(0x20080522,0);d=(Data*2)()
if libc.capget(ctypes.byref(h),ctypes.byref(d)) or any(x.effective|x.permitted|x.inheritable for x in d):raise RuntimeError('Capabilities remain')
install_seccomp()
sys.path[:]=['/bundle','/usr/lib/python3.13','/usr/lib/python3.13/lib-dynload','/venv']
exec(compile(Path('/bundle/plan_job.py').read_bytes(),'/bundle/plan_job.py','exec'),{'__name__':'__main__','__file__':'/bundle/plan_job.py'})
