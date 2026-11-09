"""Build private copied runtime. Build-time only; never receives credentials."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig


def build(root, upstream, site_packages):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    for src,dst in [(Path(sysconfig.get_path('stdlib')),root/'usr/lib/python3.13'),(Path(site_packages),root/'venv'),(Path(upstream),root/'bundle/upstream/apps/predbat')]:
        shutil.copytree(src,dst,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','site-packages','test','tests'))
    shutil.copy2(Path(__file__).with_name('plan_job.py'),root/'bundle/plan_job.py')
    libs={Path('/lib/x86_64-linux-gnu/libseccomp.so.2')}
    for obj in [Path(sys.executable)]+list(root.rglob('*.so')):
        p=subprocess.run(['ldd',str(obj)],capture_output=True,text=True,timeout=5)
        for name in re.findall(r'(/[^\s()]+)',p.stdout):
            src=Path(name)
            if src.is_file() and root.resolve() not in src.resolve().parents:libs.add(src)
    for src in libs|{Path('/etc/ld.so.cache'),Path('/usr/share/zoneinfo/Europe/London'),Path('/usr/share/zoneinfo/UTC')}:
        dst=root/str(src).lstrip('/');dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    (root/'work').mkdir(exist_ok=True)
    (root/'input').mkdir(exist_ok=True)
    os.chown(root/'work',65534,65534);(root/'work').chmod(0o700)
    return root

if __name__=='__main__':build(*sys.argv[1:])
